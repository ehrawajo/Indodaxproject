"""Pydantic v2 models — the Python half of the typed-fetch boundary.

Every model here has a hand-written TS mirror in frontend/src/types.ts.
Keep the pair in sync in the same edit.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------- auth / users ----------


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str = ""
    role: str = "user"
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class UserCreateIn(BaseModel):
    email: EmailStr
    name: str = ""
    role: str = "user"

    @field_validator("role")
    @classmethod
    def role_allowed(cls, v: str) -> str:
        if v not in ("user", "admin"):
            raise ValueError("role harus 'user' atau 'admin'")
        return v


class UserCreatedOut(BaseModel):
    user: UserOut
    initial_password: str


class PasswordResetOut(BaseModel):
    user: UserOut
    new_password: str


# ---------- weights settings ----------


class WeightsOut(BaseModel):
    key: str
    technical: int
    fundamental: int
    updated_at: datetime | None = None


class WeightsAllOut(BaseModel):
    daily: WeightsOut
    weekly: WeightsOut


class WeightsUpdateIn(BaseModel):
    technical: int
    fundamental: int

    @field_validator("technical", "fundamental")
    @classmethod
    def bounded(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("bobot harus 0-100")
        return v

    @field_validator("fundamental")
    @classmethod
    def sums_to_100(cls, v: int, info) -> int:
        if info.data.get("technical", 0) + v != 100:
            raise ValueError("total bobot teknikal + fundamental harus 100")
        return v


# ---------- analysis ----------


class Headline(BaseModel):
    title: str
    source: str = ""
    url: str = ""
    published_at: str = ""
    sentiment: float = 0.0
    matched: bool = True


class TechnicalDetail(BaseModel):
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    support: float | None = None
    resistance: float | None = None
    range_position: float | None = None
    volume_trend: str = "stabil"
    volume_ratio: float | None = None
    trend: str = "netral"
    change_pct: float | None = None
    score: float = 0.0
    source: str = ""
    timeframe_note: str = ""

    model_config = {"extra": "ignore"}


class FundamentalDetail(BaseModel):
    score: float = 50.0
    polarity: float | None = None
    positives: int = 0
    negatives: int = 0
    headlines: list[Headline] = []
    summary: str = ""


class ProjectionBreakdown(BaseModel):
    """How est_gain_pct was built — volume and trend are explicit, auditable factors."""

    headroom_pct: float = 0.0
    trend_factor: float = 1.0
    volume_factor: float = 1.0
    liquidity_factor: float = 1.0
    momentum_pct: float = 0.0
    horizon_factor: float = 1.0
    volume_ratio: float | None = None
    vol_idr: float | None = None
    trend: str = "netral"
    final_pct: float = 0.0


class TradeLevels(BaseModel):
    """Actionable trade plan derived from support/resistance + the projection."""

    entry: float
    entry_low: float
    entry_high: float
    tp1: float
    tp2: float
    tp3: float
    tp1_pct: float
    tp2_pct: float
    tp3_pct: float
    stop_loss: float
    stop_loss_pct: float
    risk_reward: float | None = None
    max_risk_pct: float = 8.0
    support: float | None = None
    resistance: float | None = None
    key_resistance: float | None = None


class SnapshotItem(BaseModel):
    rank: int = 0
    pair: str
    coin: str
    name: str
    logo: str = ""
    score: float
    technical_score: float
    fundamental_score: float
    est_gain_pct: float
    entry_price: float
    target_price: float
    vol_idr: float | None = None
    # absent on snapshots locked before the volume/trend-aware projection shipped
    projection: ProjectionBreakdown | None = None
    levels: TradeLevels | None = None
    technical: TechnicalDetail
    fundamental: FundamentalDetail
    # live coin-detail extras (absent inside locked snapshots)
    snapshot_rank: int | None = None
    snapshot_est_gain_pct: float | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None


class Weights(BaseModel):
    technical: float
    fundamental: float


class Snapshot(BaseModel):
    id: str
    kind: str
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    weights: Weights
    items: list[SnapshotItem] = []
    actuals_recorded: bool = False
    approximated: bool = False
    evaluated_at: datetime | None = None


class SnapshotSummary(BaseModel):
    id: str
    kind: str
    period_start: datetime
    period_end: datetime
    generated_at: datetime | None = None
    top_pairs: list[str] = []
    avg_projected: float = 0.0
    evaluated: bool = False
    hit_rate: float | None = None
    avg_deviation: float | None = None


class CurrentAnalysis(BaseModel):
    kind: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    generating: bool = False
    snapshot: Snapshot | None = None


class RefreshIn(BaseModel):
    kind: str

    @field_validator("kind")
    @classmethod
    def kind_allowed(cls, v: str) -> str:
        if v not in ("daily", "weekly"):
            raise ValueError("kind harus 'daily' atau 'weekly'")
        return v


# ---------- evaluation ----------


class ActualRow(BaseModel):
    pair: str
    entry_price: float
    actual_high: float
    actual_pct: float
    projected_pct: float
    deviation_pct: float
    target_hit: bool
    recorded_at: datetime | None = None


class EvalDetail(BaseModel):
    snapshot: Snapshot
    rows: list[ActualRow] = []
    hit_rate: float = 0.0
    positive_rate: float = 0.0
    avg_deviation: float = 0.0
    avg_projected: float = 0.0
    avg_actual: float = 0.0


class EvalResponse(BaseModel):
    latest: EvalDetail | None = None
    history: list[SnapshotSummary] = []


class MonthlyPoint(BaseModel):
    date: str
    hit_rate: float
    avg_deviation: float
    avg_projected: float
    avg_actual: float
    coins: int = 0
    positive_rate: float = 0.0


class MonthlyOverall(BaseModel):
    snapshots: int = 0
    hit_rate: float = 0.0
    positive_rate: float = 0.0
    avg_deviation: float = 0.0
    best_day: MonthlyPoint | None = None
    worst_day: MonthlyPoint | None = None


class MonthlyEvalResponse(BaseModel):
    points: list[MonthlyPoint] = []
    overall: MonthlyOverall


class LiveRow(BaseModel):
    pair: str
    name: str
    rank: int = 0
    entry_price: float
    last_price: float | None = None
    live_high: float
    live_high_at: datetime | None = None
    live_gain_pct: float
    current_gain_pct: float | None = None
    projected_pct: float
    achieved_ratio: float | None = None
    target_hit: bool = False
    tp_reached: int = 0
    sl_touched: bool = False


class LiveSummary(BaseModel):
    coins: int = 0
    avg_live_gain_pct: float = 0.0
    avg_projected_pct: float = 0.0
    positive_count: int = 0
    target_hit_count: int = 0
    tp1_count: int = 0
    tp2_count: int = 0
    tp3_count: int = 0
    sl_count: int = 0
    best: str | None = None


class LiveProgress(BaseModel):
    kind: str
    period_start: datetime
    period_end: datetime
    updated_at: datetime
    window_label: str
    rows: list[LiveRow] = []
    summary: LiveSummary


# ---------- watchlist ----------


class WatchlistAddIn(BaseModel):
    pair: str


class WatchlistEntry(BaseModel):
    pair: str
    coin: str
    name: str
    logo: str = ""
    created_at: datetime | None = None
    # live enrichment (ticker + current locked snapshot), null when unavailable
    last_price: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    vol_idr: float | None = None
    snapshot_rank: int | None = None
    score: float | None = None
    est_gain_pct: float | None = None
    in_snapshot: bool = False


# ---------- per-coin accuracy ----------


class CoinAccuracyRow(BaseModel):
    pair: str
    coin: str
    name: str
    logo: str = ""
    appearances: int
    hits: int
    hit_rate: float
    avg_projected: float
    avg_actual: float
    avg_deviation: float
    best_actual: float
    worst_actual: float
    last_seen: datetime | None = None


class CoinAccuracyResponse(BaseModel):
    kind: str
    rows: list[CoinAccuracyRow] = []
    snapshots_evaluated: int = 0
    min_appearances: int = 1


# ---------- meta ----------


class PricePoint(BaseModel):
    ts: datetime
    price: float


class Candle(BaseModel):
    ts: datetime
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0
    from_trades: bool = True


class CandleSeries(BaseModel):
    pair: str
    bucket_minutes: int
    source: str
    note: str
    candles: list[Candle] = []


class SyncStatusOut(BaseModel):
    last_ticker_sync_at: datetime | None = None
    last_ticker_ok: bool = True
    last_ticker_error: str | None = None
    last_generation_at: datetime | None = None
    last_generation_error: str | None = None
    universe_count: int = 0
    candidates_last_run: int = 0


class CronRunOut(BaseModel):
    job: str
    status: str
    detail: str | None = None
    at: datetime | None = None


class MetaStatusOut(BaseModel):
    server_time: datetime
    wita_time: str
    next_daily_run: datetime
    next_weekly_run: datetime
    sync: SyncStatusOut = SyncStatusOut()
    scheduler: str = "platform-cron"
    recent_cron_runs: list[CronRunOut] = []
