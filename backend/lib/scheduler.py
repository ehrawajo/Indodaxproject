"""In-pod catch-up safety net.

The AUTHORITATIVE scheduler is the Emergent platform cron (.emergent/crons.yml ->
routers/cron.py), which fires the 06:00 WITA snapshots regardless of whether anyone has
the app open. This loop exists only as a safety net for the gaps a platform cron cannot
cover by itself:

  1. boot catch-up — if the pod was down at 06:00 WITA, the missed window is generated as
     soon as the backend comes back up (ensure_current)
  2. finalization — closes snapshots whose window ended while the pod was asleep
  3. sampling backstop — keeps Actual High tracking alive if a cron delivery is missed

Everything it calls is idempotent (unique index on kind+period_start, sample de-dupe
window, finalize guarded by actuals_recorded), so cron and this loop can never double-write.
"""

import asyncio
import logging
from datetime import timedelta

import httpx

from lib import engine, market
from lib.dates import now_utc
from lib.db import db

logger = logging.getLogger(__name__)

TICK_SECONDS = 300          # safety net only — the platform cron drives the real schedule
SAMPLE_MIN_GAP_MINUTES = 10  # skip if a sample already landed this recently (cron or loop)


async def sample_market(now) -> bool:
    """Record one Indodax ticker sample + running Actual Highs. Skips if one is recent."""
    recent = await db.price_history.find_one(
        {"ts": {"$gte": now - timedelta(minutes=SAMPLE_MIN_GAP_MINUTES)}}, {"_id": 1}
    )
    if recent:
        return False
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            tickers = await market.get_ticker_all(client)
        prices = {tid: t["last"] for tid, t in tickers.items() if t["last"] > 0}
        if prices:
            await db.price_history.insert_one({"ts": now, "prices": prices})
        await engine._set_sync(
            last_ticker_sync_at=now, last_ticker_ok=True, last_ticker_error=None,
            universe_count=len(prices),
        )
        return True
    except Exception as exc:
        logger.warning("price sample failed: %s", exc)
        await engine._set_sync(last_ticker_ok=False, last_ticker_error=str(exc)[:500])
        return False


async def tick() -> None:
    now = now_utc()
    await sample_market(now)
    await engine.finalize_due(now)
    await engine.ensure_current(now)


async def scheduler_loop() -> None:
    logger.info(
        "catch-up loop started (tick %ss); platform cron in .emergent/crons.yml is the primary scheduler",
        TICK_SECONDS,
    )
    while True:
        try:
            await tick()
        except Exception:
            logger.exception("catch-up tick failed")
        await asyncio.sleep(TICK_SECONDS)
