"""Analysis routes — locked snapshot reads, history, admin force-refresh, live coin detail."""

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from lib import engine
from lib.auth import get_current_user, require_admin
from lib.dates import now_utc
from lib.db import db
from models.schemas import (
    CandleSeries,
    CurrentAnalysis,
    LiveProgress,
    PricePoint,
    RefreshIn,
    Snapshot,
    SnapshotSummary,
    SnapshotItem,
)

router = APIRouter(prefix="/analysis", tags=["analysis"])

_KINDS = ("daily", "weekly")


def _check_kind(kind: str) -> str:
    if kind not in _KINDS:
        raise HTTPException(status_code=422, detail="kind harus 'daily' atau 'weekly'")
    return kind


@router.get("/current", response_model=CurrentAnalysis)
async def current_analysis(kind: str = Query("daily")):
    _check_kind(kind)
    start, end = engine._snap_coord(kind, now_utc())
    doc = await db.snapshots.find_one({"kind": kind, "period_start": start})
    if doc:
        return CurrentAnalysis(
            kind=kind,
            period_start=start,
            period_end=end,
            generating=False,
            snapshot=Snapshot(**engine.snapshot_out(doc)),
        )
    # Nothing locked yet for this window — kick generation in the background and say so.
    asyncio.create_task(engine.ensure_current(now_utc()))
    return CurrentAnalysis(kind=kind, period_start=start, period_end=end, generating=True, snapshot=None)


@router.get("/live", response_model=LiveProgress)
async def live_window_progress(kind: str = Query("daily"), _user: dict = Depends(get_current_user)):
    """High live + kenaikan aktual sepanjang window yang sedang berjalan."""
    _check_kind(kind)
    data = await engine.live_progress(kind)
    if not data:
        raise HTTPException(status_code=404, detail="Belum ada snapshot untuk periode ini")
    return LiveProgress(**data)


@router.get("/history", response_model=list[SnapshotSummary])
async def history(kind: str = Query("daily"), limit: int = Query(14, le=60)):
    _check_kind(kind)
    docs = await db.snapshots.find({"kind": kind}).sort("period_end", -1).to_list(limit)
    return [SnapshotSummary(**engine.snapshot_summary(d)) for d in docs]


@router.get("/snapshot/{snapshot_id}", response_model=Snapshot)
async def get_snapshot(snapshot_id: str):
    doc = await db.snapshots.find_one({"id": snapshot_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Snapshot tidak ditemukan")
    return Snapshot(**engine.snapshot_out(doc))


@router.post("/refresh", response_model=Snapshot)
async def refresh(body: RefreshIn, _admin: dict = Depends(require_admin)):
    doc = await engine.generate_snapshot(body.kind, force=True)
    return Snapshot(**engine.snapshot_out(doc))


@router.get("/coin/{pair}/history", response_model=list[PricePoint])
async def coin_price_history(pair: str, hours: int = Query(24, le=168), _user: dict = Depends(get_current_user)):
    """Indodax-native price samples (15-min scheduler ticks) — feeds the coin-page trend chart."""
    since = now_utc() - timedelta(hours=hours)
    docs = await db.price_history.find(
        {"ts": {"$gte": since}}, {"ts": 1, "prices." + pair: 1}
    ).sort("ts", 1).to_list(300)
    points = [
        PricePoint(ts=engine.aware_or_none(d["ts"]) or now_utc(), price=float(p))
        for d in docs
        if (p := (d.get("prices") or {}).get(pair))
    ]
    return points


@router.get("/coin/{pair}/candles", response_model=CandleSeries)
async def coin_candles(
    pair: str,
    bucket: int = Query(15, ge=5, le=240),
    hours: int = Query(24, ge=1, le=168),
    _user: dict = Depends(get_current_user),
):
    """Indodax-native OHLC candles — used when the coin has no global candlestick chart."""
    return CandleSeries(**await engine.indodax_candles(pair, bucket, hours))


@router.get("/coin/{pair}", response_model=SnapshotItem)
async def coin_detail(pair: str, kind: str = Query("daily"), _user: dict = Depends(get_current_user)):
    _check_kind(kind)
    item = await engine.live_coin_detail(pair, kind)
    if not item:
        raise HTTPException(status_code=404, detail="Pair tidak ditemukan di market Indodax")
    return SnapshotItem(**item)
