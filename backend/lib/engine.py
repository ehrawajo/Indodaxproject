"""Hybrid Analysis engine — screens the Indodax IDR universe, scores candidates with
Technical + Fundamental signals, and writes LOCKED snapshots to Mongo.

Scoring contract (weights are admin-configurable, defaults per spec):
- daily   : 70% technical + 30% fundamental
- weekly  : 50% technical + 50% fundamental

A snapshot is one immutable document per (kind, period_start). The scheduler
generates it at the 06:00 WITA rollover (or lazily on cold start) and finalizes
it with Actual High data at the end of its window.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from lib import indicators, market
from lib import news as news_lib
from lib.dates import daily_period, now_utc, weekly_period
from lib.db import db

logger = logging.getLogger(__name__)

CANDIDATE_DEPTH = 48
DEFAULT_WEIGHTS = {"daily": (70, 30), "weekly": (50, 50)}
_analysis_lock = asyncio.Lock()
_detail_cache = market.TTLCache()


# ---------- settings / sync ----------


async def get_weights(kind: str) -> tuple[int, int]:
    doc = await db.settings.find_one({"key": f"weights_{kind}"})
    if doc and doc.get("technical") is not None:
        return int(doc["technical"]), int(doc["fundamental"])
    return DEFAULT_WEIGHTS.get(kind, (50, 50))


async def _set_sync(**fields: Any) -> None:
    await db.sync_status.update_one({"_id": "sync"}, {"$set": fields}, upsert=True)


async def get_sync() -> dict:
    return await db.sync_status.find_one({"_id": "sync"}) or {}


def _snap_coord(kind: str, now: datetime) -> tuple[datetime, datetime]:
    if kind == "weekly":
        return weekly_period(now)
    return daily_period(now)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def aware_or_none(dt: datetime | None) -> datetime | None:
    """Motor hands back naive UTC datetimes — normalise before Pydantic serialises them."""
    return _aware(dt) if isinstance(dt, datetime) else None


# ---------- candidate analysis ----------


async def _analyze_candidate(
    client: httpx.AsyncClient,
    p: dict,
    t: dict[str, float],
    fund: dict | None,
    weights: tuple[int, int],
    kind: str,
    window_hours: int,
) -> dict | None:
    coin = p["traded_currency"]
    pair_id = p.get("id") or coin + "idr"
    entry = t["last"]
    t_w, f_w = weights

    candles = await market.get_candles(client, coin, pair_id)
    if candles:
        ratio = entry / candles["price_ref"] if candles.get("price_ref") else 1.0
        timeframe = "daily" if kind == "daily" else "weekly"
        series = candles["h1"] if kind == "daily" else (candles.get("d1") or candles["h1"])
        det = indicators.technical_detail(series, timeframe, candles["source"])
        for key in ("sma20", "sma50", "support", "resistance", "macd", "macd_signal", "macd_hist"):
            if det.get(key) is not None:
                det[key] = det[key] * ratio
        score = float(det["score"])
        est, projection = indicators.estimate_gain_pct(det, entry, kind, t.get("vol_idr"))
    else:
        # ticker-only fallback: range position of the last 24h, still volume/trend aware
        high, low = t["high"], t["low"]
        pos = (entry - low) / (high - low) if high > low else 0.5
        score = round(min(100.0, 35.0 + 30.0 * pos), 1)
        fallback_trend = "bullish" if pos >= 0.66 else ("bearish" if pos <= 0.33 else "netral")
        det = {
            "rsi": None, "macd": None, "macd_signal": None, "macd_hist": None,
            "sma20": None, "sma50": None, "support": low, "resistance": high,
            "range_position": round(pos, 3), "volume_trend": "stabil", "volume_ratio": None,
            "trend": fallback_trend,
            "change_pct": round((entry / high - 1) * 100, 2) if high else 0.0,
            "score": score, "source": "indodax_ticker",
            "timeframe_note": "Ticker 24 jam Indodax (data candle tidak tersedia untuk koin ini)",
        }
        est, projection = indicators.estimate_gain_pct(det, entry, kind, t.get("vol_idr"))

    fscore = float(fund["score"]) if fund else 50.0
    levels = indicators.trade_levels(det, entry, est, kind)
    hybrid = (t_w * score + f_w * fscore) / 100.0
    headlines = (fund or {}).get("headlines") or []
    fund_out = {
        "score": fscore,
        "polarity": (fund or {}).get("polarity"),
        "positives": (fund or {}).get("positives", 0),
        "negatives": (fund or {}).get("negatives", 0),
        "headlines": headlines[:6],
        "summary": news_lib.build_summary(fund or {}, window_hours),
    }

    return {
        "rank": 0,
        "pair": p["ticker_id"],
        "coin": coin,
        "name": p.get("traded_currency_unit") or coin.upper(),
        "logo": p.get("url_logo") or "",
        "score": round(hybrid, 1),
        "technical_score": round(score, 1),
        "fundamental_score": round(fscore, 1),
        "est_gain_pct": est,
        "entry_price": entry,
        "target_price": round(entry * (1 + est / 100.0), 8),
        "vol_idr": t.get("vol_idr"),
        "projection": projection,
        "levels": levels,
        "technical": det,
        "fundamental": fund_out,
    }


# ---------- snapshot lifecycle ----------


async def generate_snapshot(kind: str, force: bool = False) -> dict:
    """Build (or rebuild when force) the locked snapshot for the current period of `kind`."""
    async with _analysis_lock:
        now = now_utc()
        start, end = _snap_coord(kind, now)
        if not force:
            existing = await db.snapshots.find_one({"kind": kind, "period_start": start})
            if existing:
                return existing

        t_w, f_w = await get_weights(kind)
        window_hours = 24 if kind == "daily" else 168

        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                pairs = await market.get_pairs(client)
                tickers = await market.get_ticker_all(client)
            except Exception as exc:
                await _set_sync(last_ticker_ok=False, last_ticker_error=str(exc)[:500])
                raise
            await _set_sync(last_ticker_sync_at=now, last_ticker_ok=True, universe_count=len(tickers))

            universe: list[tuple[dict, dict[str, float]]] = []
            for p in pairs:
                t = tickers.get(p["ticker_id"])
                if not t or t["vol_idr"] <= 0 or t["last"] <= 0:
                    continue
                universe.append((p, t))
            universe.sort(key=lambda pt: -pt[1]["vol_idr"])
            universe = universe[:CANDIDATE_DEPTH]

            news_items = await news_lib.fetch_news(client)
            coins_meta = [
                {"traded_currency": p["traded_currency"], "traded_currency_unit": p.get("traded_currency_unit") or ""}
                for p, _ in universe
            ]
            fundamentals = news_lib.fundamental_for_coins(news_items, coins_meta, window_hours)

            sem = market.candidate_sem

            async def run(pt: tuple[dict, dict[str, float]]) -> dict | None:
                p, t = pt
                async with sem:
                    try:
                        return await _analyze_candidate(
                            client, p, t, fundamentals.get(p["traded_currency"]), (t_w, f_w), kind, window_hours
                        )
                    except Exception as exc:
                        logger.warning("analyze %s failed: %s", p.get("ticker_id"), exc)
                        return None

            results = await asyncio.gather(*(run(pt) for pt in universe))

        items = [r for r in results if r]
        items.sort(key=lambda r: (-r["est_gain_pct"], -r["score"]))
        top = items[:10]
        for i, it in enumerate(top):
            it["rank"] = i + 1

        doc = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "period_start": start,
            "period_end": end,
            "generated_at": now,
            "weights": {"technical": t_w, "fundamental": f_w},
            "items": top,
            "actuals": {},
            "actuals_running": {},
            "actuals_recorded": False,
            "approximated": False,
            "evaluated_at": None,
        }
        await db.snapshots.replace_one({"kind": kind, "period_start": start}, doc, upsert=True)
        await _set_sync(
            last_generation_at=now,
            candidates_last_run=len(universe),
            last_generation_error=None,
        )
        logger.info("snapshot %s generated: %d items (period %s)", kind, len(top), start)
        return doc


async def ensure_current(now: datetime | None = None) -> bool:
    """Guarantee a locked snapshot exists for the current daily and weekly windows."""
    now = now or now_utc()
    made = False
    for kind in ("daily", "weekly"):
        start, _ = _snap_coord(kind, now)
        exists = await db.snapshots.find_one({"kind": kind, "period_start": start}, {"_id": 1})
        if not exists:
            try:
                await generate_snapshot(kind)
                made = True
            except Exception as exc:
                logger.error("ensure_current(%s): %s", kind, exc)
                await _set_sync(last_generation_error=str(exc)[:500])
    return made


async def record_running_actuals(tickers: dict[str, dict[str, float]], now: datetime) -> None:
    """Deprecated no-op.

    A stored running maximum proved unsafe: it could never come back down, so one write
    with the wrong semantics (the ticker's rolling 24h high, which can predate the 06:00
    WITA window) permanently inflated the actual gain. In-window highs are now recomputed
    from window-filtered sources on every read — see window_high(). Kept as a no-op so any
    in-flight caller stays harmless.
    """
    return None


async def _sampled_highs(pairs: list[str], start: datetime, end: datetime) -> dict[str, dict]:
    """Max sampled price per pair inside [start, end) from our own price_history.

    Returns {pair: {"high": float, "at": datetime}} — `at` is when that high was sampled.
    """
    if not pairs:
        return {}
    projection: dict[str, int] = {f"prices.{p}": 1 for p in pairs}
    projection["ts"] = 1
    docs = await db.price_history.find(
        {"ts": {"$gte": start, "$lt": end}}, projection
    ).to_list(2000)
    out: dict[str, dict] = {}
    for d in docs:
        prices = d.get("prices") or {}
        ts = _aware(d["ts"])
        for p in pairs:
            v = prices.get(p)
            if v and float(v) > (out.get(p, {}).get("high") or 0.0):
                out[p] = {"high": float(v), "at": ts}
    return out


async def _trade_highs(
    client: httpx.AsyncClient, pairs: list[str], start: datetime
) -> dict[str, float]:
    """Max traded price per pair at/after `start`, straight from Indodax trades.

    Fills the gaps between our samples — the trade feed carries every print.
    """
    start_ts = start.timestamp()

    async def one(pair: str) -> tuple[str, float]:
        try:
            trades = await market.get_trades(client, pair.replace("_", ""))
            highs = [t["price"] for t in trades if t["ts"] >= start_ts]
            return pair, max(highs) if highs else 0.0
        except Exception:
            return pair, 0.0

    sem = market.candidate_sem

    async def guarded(pair: str) -> tuple[str, float]:
        async with sem:
            return await one(pair)

    results = await asyncio.gather(*(guarded(p) for p in pairs))
    return {p: v for p, v in results if v > 0}


async def window_high(
    items: list[dict],
    start: datetime,
    end: datetime,
    tickers: dict[str, dict[str, float]],
    trade_highs: dict[str, float] | None = None,
) -> dict[str, dict]:
    """True in-window high per pair, from window-filtered Indodax sources only.

    Deliberately does NOT consult a stored running maximum: such a value cannot be
    re-verified, and a single bad write (e.g. a rolling 24h high that predates the window)
    would stick forever because a running max never comes back down. Every source here is
    filtered to [start, end) and recomputed from scratch on each call.
    """
    pairs = [it["pair"] for it in items]
    sampled = await _sampled_highs(pairs, start, end)
    by_pair: dict[str, dict] = {}
    for it in items:
        pair = it["pair"]
        entry = float(it.get("entry_price") or 0.0)
        best = entry
        at: datetime | None = None

        s = sampled.get(pair)
        if s and s["high"] > best:
            best, at = s["high"], s["at"]
        th = (trade_highs or {}).get(pair)
        if th and th > best:
            best, at = th, None  # trade feed: precise price, timestamp not tracked
        t = tickers.get(pair)
        if t and t["last"] > best:
            best, at = t["last"], None

        by_pair[pair] = {"high": best, "at": at, "last": t["last"] if t else None}
    return by_pair


async def finalize_due(now: datetime | None = None) -> int:
    """Lock in Actual High results for every snapshot whose window has closed."""
    now = now or now_utc()
    pending = await db.snapshots.find(
        {"actuals_recorded": False, "period_end": {"$lte": now}}
    ).to_list(20)
    if not pending:
        return 0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tickers = await market.get_ticker_all(client)

        finalized = 0
        for snap in pending:
            if not snap.get("items"):
                await db.snapshots.update_one(
                    {"_id": snap["_id"]},
                    {"$set": {"actuals_recorded": True, "approximated": True, "actuals": {}}},
                )
                continue
            start = _aware(snap["period_start"])
            end = _aware(snap["period_end"])
            late = (now - end).total_seconds() > 2 * 3600
            # same high computation the live view shows, so the locked result matches it
            trade_highs = {} if late else await _trade_highs(
                client, [it["pair"] for it in snap["items"]], start
            )
            highs = await window_high(snap["items"], start, end, tickers, trade_highs)

            rows: dict[str, dict] = {}
            for it in snap["items"]:
                tid = it["pair"]
                entry = it["entry_price"] or 1e-12
                actual_high = max(float(highs.get(tid, {}).get("high") or 0.0), it["entry_price"])
                actual_pct = (actual_high - entry) / entry * 100.0
                rows[tid] = {
                    "pair": tid,
                    "entry_price": it["entry_price"],
                    "actual_high": round(actual_high, 8),
                    "actual_pct": round(actual_pct, 2),
                    "projected_pct": it["est_gain_pct"],
                    "deviation_pct": round(actual_pct - it["est_gain_pct"], 2),
                    "target_hit": bool(actual_high >= it["target_price"]),
                    "recorded_at": now,
                }
            await db.snapshots.update_one(
                {"_id": snap["_id"]},
                {"$set": {
                    "actuals": rows,
                    "actuals_recorded": True,
                    "approximated": late,
                    "evaluated_at": now,
                }},
            )
            finalized += 1
    return finalized


async def live_progress(kind: str) -> dict | None:
    """Live in-window performance of the current locked snapshot.

    "High live" is the highest price reached since the window opened — 06:00 WITA today for
    daily, Monday 06:00 WITA for weekly — and "kenaikan aktual" is that high measured against
    the entry price recorded when the snapshot was locked.
    """
    cache_key = f"live_{kind}"
    cached = _detail_cache.get(cache_key)
    if cached is not None:
        return cached

    now = now_utc()
    start, end = _snap_coord(kind, now)
    snap = await db.snapshots.find_one({"kind": kind, "period_start": start})
    if not snap or not snap.get("items"):
        return None

    items = snap["items"]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tickers = await market.get_ticker_all(client)
        trade_highs = await _trade_highs(client, [it["pair"] for it in items], start)
    highs = await window_high(items, start, end, tickers, trade_highs)

    rows = []
    for it in items:
        pair = it["pair"]
        entry = float(it["entry_price"]) or 1e-12
        info = highs.get(pair) or {}
        high = float(info.get("high") or entry)
        last = info.get("last")
        live_gain = (high - entry) / entry * 100.0
        current_gain = ((float(last) - entry) / entry * 100.0) if last else None
        projected = float(it["est_gain_pct"])
        levels = it.get("levels") or {}

        tp_reached = 0
        for idx, key in enumerate(("tp1", "tp2", "tp3"), start=1):
            if levels.get(key) and high >= float(levels[key]):
                tp_reached = idx
        sl_touched = bool(levels.get("stop_loss") and last and float(last) <= float(levels["stop_loss"]))

        rows.append({
            "pair": pair,
            "name": it.get("name") or pair.split("_")[0].upper(),
            "rank": it.get("rank", 0),
            "entry_price": it["entry_price"],
            "last_price": last,
            "live_high": round(high, 8),
            "live_high_at": aware_or_none(info.get("at")),
            "live_gain_pct": round(live_gain, 2),
            "current_gain_pct": round(current_gain, 2) if current_gain is not None else None,
            "projected_pct": projected,
            "achieved_ratio": round(live_gain / projected * 100.0, 1) if projected > 0 else None,
            "target_hit": bool(high >= float(it["target_price"])),
            "tp_reached": tp_reached,
            "sl_touched": sl_touched,
        })

    n = len(rows)
    summary = {
        "coins": n,
        "avg_live_gain_pct": round(sum(r["live_gain_pct"] for r in rows) / n, 2) if n else 0.0,
        "avg_projected_pct": round(sum(r["projected_pct"] for r in rows) / n, 2) if n else 0.0,
        "positive_count": sum(1 for r in rows if r["live_gain_pct"] > 0),
        "target_hit_count": sum(1 for r in rows if r["target_hit"]),
        "tp1_count": sum(1 for r in rows if r["tp_reached"] >= 1),
        "tp2_count": sum(1 for r in rows if r["tp_reached"] >= 2),
        "tp3_count": sum(1 for r in rows if r["tp_reached"] >= 3),
        "sl_count": sum(1 for r in rows if r["sl_touched"]),
        "best": max(rows, key=lambda r: r["live_gain_pct"])["name"] if rows else None,
    }

    out = {
        "kind": kind,
        "period_start": start,
        "period_end": end,
        "updated_at": now,
        "window_label": (
            "sejak 06:00 WITA hari ini" if kind == "daily" else "sejak Senin 06:00 WITA"
        ),
        "rows": rows,
        "summary": summary,
    }
    _detail_cache.set(cache_key, out, 60)
    return out


# ---------- live coin detail (on-demand, not a locked snapshot) ----------


async def live_coin_detail(pair: str, kind: str = "daily") -> dict | None:
    cache_key = f"detail_{pair}_{kind}"
    cached = _detail_cache.get(cache_key)
    if cached is not None:
        return cached

    t_w, f_w = await get_weights(kind)
    window_hours = 24 if kind == "daily" else 168
    async with httpx.AsyncClient(follow_redirects=True) as client:
        pairs = await market.get_pairs(client)
        p = next((x for x in pairs if x["ticker_id"] == pair), None)
        if not p:
            return None
        tickers = await market.get_ticker_all(client)
        t = tickers.get(pair)
        if not t:
            return None
        news_items = await news_lib.fetch_news(client)
        fund = news_lib.fundamental_for_coins(
            news_items,
            [{"traded_currency": p["traded_currency"], "traded_currency_unit": p.get("traded_currency_unit") or ""}],
            window_hours,
        ).get(p["traded_currency"])
        item = await _analyze_candidate(client, p, t, fund, (t_w, f_w), kind, window_hours)
        if not item:
            return None

    start, end = _snap_coord(kind, now_utc())
    snap = await db.snapshots.find_one({"kind": kind, "period_start": start}, {"items": 1})
    in_snap = next((it for it in (snap or {}).get("items") or [] if it["pair"] == pair), None)
    item["snapshot_rank"] = in_snap["rank"] if in_snap else None
    item["period_start"] = start
    item["period_end"] = end
    item["snapshot_est_gain_pct"] = in_snap["est_gain_pct"] if in_snap else None

    _detail_cache.set(cache_key, item, 120)
    return item


# ---------- evaluation ----------


def snapshot_out(doc: dict) -> dict:
    """Normalize a Mongo snapshot doc into response-model shape (aware datetimes)."""
    out = dict(doc)
    for key in ("period_start", "period_end", "generated_at", "evaluated_at"):
        if out.get(key) is not None:
            out[key] = _aware(out[key])
    return out


def snapshot_summary(doc: dict) -> dict:
    items = doc.get("items") or []
    actuals = doc.get("actuals") or {}
    hits = [a.get("target_hit") for a in actuals.values()]
    return {
        "id": doc["id"],
        "kind": doc["kind"],
        "period_start": _aware(doc["period_start"]),
        "period_end": _aware(doc["period_end"]),
        "generated_at": _aware(doc["generated_at"]) if doc.get("generated_at") else None,
        "top_pairs": [f"{it['name']}/IDR" for it in items[:3]],
        "avg_projected": round(sum(it["est_gain_pct"] for it in items) / len(items), 2) if items else 0.0,
        "evaluated": bool(doc.get("actuals_recorded") and hits),
        "hit_rate": round(100.0 * sum(1 for h in hits if h) / len(hits), 1) if hits else None,
        "avg_deviation": round(sum(a["deviation_pct"] for a in actuals.values()) / len(actuals), 2) if actuals else None,
    }


def eval_detail(doc: dict) -> dict:
    items = doc.get("items") or []
    actuals = doc.get("actuals") or {}
    rows = []
    for it in items:
        a = actuals.get(it["pair"])
        if a:
            row = dict(a)
            row["recorded_at"] = aware_or_none(row.get("recorded_at"))
            rows.append(row)
    hits = [r["target_hit"] for r in rows]
    positives = [r["actual_pct"] > 0 for r in rows]
    n = len(rows)
    return {
        "snapshot": snapshot_out(doc),
        "rows": rows,
        "hit_rate": round(100.0 * sum(1 for h in hits if h) / n, 1) if n else 0.0,
        "positive_rate": round(100.0 * sum(1 for p in positives if p) / n, 1) if n else 0.0,
        "avg_deviation": round(sum(r["deviation_pct"] for r in rows) / n, 2) if n else 0.0,
        "avg_projected": round(sum(r["projected_pct"] for r in rows) / n, 2) if n else 0.0,
        "avg_actual": round(sum(r["actual_pct"] for r in rows) / n, 2) if n else 0.0,
    }


async def latest_evaluated(kind: str) -> dict | None:
    doc = await db.snapshots.find_one(
        {"kind": kind, "actuals_recorded": True},
        sort=[("period_end", -1)],
    )
    return doc


async def evaluation_history(kind: str, limit: int) -> list[dict]:
    docs = await db.snapshots.find(
        {"kind": kind, "actuals_recorded": True}
    ).sort("period_end", -1).to_list(limit)
    return [snapshot_summary(d) for d in docs]


async def monthly_recap() -> dict:
    now = now_utc()
    since = now - timedelta(days=30)
    docs = await db.snapshots.find(
        {"kind": "daily", "actuals_recorded": True, "period_end": {"$gte": since}}
    ).sort("period_end", 1).to_list(60)

    points = []
    for doc in docs:
        actuals = doc.get("actuals") or {}
        if not actuals:
            continue
        hits = [a["target_hit"] for a in actuals.values()]
        positives = [a["actual_pct"] > 0 for a in actuals.values()]
        points.append({
            "date": _aware(doc["period_start"]).astimezone(_wita_tz()).strftime("%Y-%m-%d"),
            "hit_rate": round(100.0 * sum(1 for h in hits if h) / len(hits), 1),
            "avg_deviation": round(sum(a["deviation_pct"] for a in actuals.values()) / len(actuals), 2),
            "avg_projected": round(sum(a["projected_pct"] for a in actuals.values()) / len(actuals), 2),
            "avg_actual": round(sum(a["actual_pct"] for a in actuals.values()) / len(actuals), 2),
            "coins": len(actuals),
            "positive_rate": round(100.0 * sum(1 for p in positives if p) / len(positives), 1),
        })

    if points:
        best = max(points, key=lambda p: p["hit_rate"])
        worst = min(points, key=lambda p: p["hit_rate"])
        overall = {
            "snapshots": len(points),
            "hit_rate": round(sum(p["hit_rate"] for p in points) / len(points), 1),
            "positive_rate": round(sum(p.get("positive_rate", 0.0) for p in points) / len(points), 1),
            "avg_deviation": round(sum(p["avg_deviation"] for p in points) / len(points), 2),
            "best_day": best,
            "worst_day": worst,
        }
    else:
        overall = {"snapshots": 0, "hit_rate": 0.0, "positive_rate": 0.0, "avg_deviation": 0.0,
                   "best_day": None, "worst_day": None}
    return {"points": points, "overall": overall}


async def accuracy_by_coin(kind: str, limit_snapshots: int = 90, min_appearances: int = 1) -> dict:
    """Per-coin model reliability across every evaluated snapshot of `kind`.

    Aggregates each coin's appearances in locked snapshots against its recorded Actual High,
    so the ranking answers "which coins does the model predict best".
    """
    docs = await db.snapshots.find(
        {"kind": kind, "actuals_recorded": True}
    ).sort("period_end", -1).to_list(limit_snapshots)

    acc: dict[str, dict] = {}
    counted = 0
    for doc in docs:
        actuals = doc.get("actuals") or {}
        if not actuals:
            continue
        counted += 1
        meta = {it["pair"]: it for it in doc.get("items") or []}
        period_end = _aware(doc["period_end"])
        for pair, a in actuals.items():
            it = meta.get(pair, {})
            e = acc.setdefault(
                pair,
                {
                    "pair": pair,
                    "coin": it.get("coin") or pair.split("_")[0],
                    "name": it.get("name") or pair.split("_")[0].upper(),
                    "logo": it.get("logo") or "",
                    "appearances": 0,
                    "hits": 0,
                    "proj": [],
                    "act": [],
                    "dev": [],
                    "last_seen": period_end,
                },
            )
            e["appearances"] += 1
            if a.get("target_hit"):
                e["hits"] += 1
            e["proj"].append(float(a.get("projected_pct") or 0.0))
            e["act"].append(float(a.get("actual_pct") or 0.0))
            e["dev"].append(float(a.get("deviation_pct") or 0.0))
            if period_end > e["last_seen"]:
                e["last_seen"] = period_end
            if it.get("logo") and not e["logo"]:
                e["logo"] = it["logo"]

    rows = []
    for e in acc.values():
        if e["appearances"] < min_appearances:
            continue
        n = e["appearances"]
        rows.append({
            "pair": e["pair"],
            "coin": e["coin"],
            "name": e["name"],
            "logo": e["logo"],
            "appearances": n,
            "hits": e["hits"],
            "hit_rate": round(100.0 * e["hits"] / n, 1),
            "avg_projected": round(sum(e["proj"]) / n, 2),
            "avg_actual": round(sum(e["act"]) / n, 2),
            "avg_deviation": round(sum(e["dev"]) / n, 2),
            "best_actual": round(max(e["act"]), 2),
            "worst_actual": round(min(e["act"]), 2),
            "last_seen": e["last_seen"],
        })

    # most reliable first: hit rate, then sample size, then how close the projection ran
    rows.sort(key=lambda r: (-r["hit_rate"], -r["appearances"], abs(r["avg_deviation"])))
    return {
        "kind": kind,
        "rows": rows,
        "snapshots_evaluated": counted,
        "min_appearances": min_appearances,
    }


async def indodax_candles(pair: str, bucket_minutes: int = 15, hours: int = 24) -> dict:
    """Indodax-native OHLC candles — the chart for coins with no global listing.

    Indodax has no OHLC endpoint, so candles are built from two Indodax-only sources:
      1. `/api/trades/{pair}` — the last 500 trades, bucketed into true OHLCV candles
         (for thin coins this spans 15-20h; for very liquid ones only ~1h).
      2. `price_history` — our own 15-min ticker samples, aggregated per bucket to
         backfill buckets older than the trade window.
    Trade-derived candles always win where both exist.
    """
    bucket = max(5, min(240, bucket_minutes)) * 60
    since = now_utc() - timedelta(hours=hours)
    pair_id = pair.replace("_", "")

    trade_candles: list[dict] = []
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            trades = await market.get_trades(client, pair_id)
        trade_candles = market.candles_from_trades(trades, bucket // 60)
    except Exception as exc:
        logger.warning("indodax_candles trades(%s): %s", pair, exc)

    buckets: dict[int, dict] = {}
    oldest_trade_slot: int | None = None
    for c in trade_candles:
        if datetime.fromtimestamp(c["ts"], tz=timezone.utc) < since:
            continue
        slot = int(c["ts"] // bucket) * bucket
        buckets[slot] = {**c, "ts": slot, "from_trades": True}
        oldest_trade_slot = slot if oldest_trade_slot is None else min(oldest_trade_slot, slot)

    # backfill older buckets from our stored ticker samples
    docs = await db.price_history.find(
        {"ts": {"$gte": since}}, {"ts": 1, "prices." + pair: 1}
    ).sort("ts", 1).to_list(500)
    sample_buckets: dict[int, list[float]] = {}
    for d in docs:
        price = (d.get("prices") or {}).get(pair)
        if not price:
            continue
        ts = _aware(d["ts"]).timestamp()
        slot = int(ts // bucket) * bucket
        if oldest_trade_slot is not None and slot >= oldest_trade_slot:
            continue  # inside the trade window — real candles already cover it
        sample_buckets.setdefault(slot, []).append(float(price))

    for slot, prices in sample_buckets.items():
        buckets[slot] = {
            "ts": slot,
            "o": prices[0],
            "h": max(prices),
            "l": min(prices),
            "c": prices[-1],
            "v": 0.0,
            "from_trades": False,
        }

    ordered = [buckets[k] for k in sorted(buckets)]
    from_trades = sum(1 for c in ordered if c["from_trades"])
    candles = [
        {
            "ts": datetime.fromtimestamp(c["ts"], tz=timezone.utc),
            "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"],
            "from_trades": c["from_trades"],
        }
        for c in ordered
    ]

    if from_trades and from_trades < len(candles):
        note = (
            f"{from_trades} candle OHLC dari trade Indodax, "
            f"{len(candles) - from_trades} candle lama direkonstruksi dari sampel harga server"
        )
    elif from_trades:
        note = f"{from_trades} candle OHLC asli dari trade Indodax (500 transaksi terakhir)"
    elif candles:
        note = f"{len(candles)} candle direkonstruksi dari sampel harga Indodax (tiap 15 menit)"
    else:
        note = "Belum ada data trade maupun sampel harga untuk pair ini"

    return {
        "pair": pair,
        "bucket_minutes": bucket // 60,
        "source": "indodax_trades" if from_trades else ("indodax_samples" if candles else "none"),
        "note": note,
        "candles": candles,
    }


def _wita_tz():
    from lib.dates import WITA
    return WITA
