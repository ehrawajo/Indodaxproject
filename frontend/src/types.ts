// Hand-written mirrors of backend/models/schemas.py — nothing infers across the HTTP boundary.
// Keep this file in sync in the same edit as any Pydantic model change.

export interface UserOut {
  id: string;
  email: string;
  name: string;
  role: "admin" | "user";
  created_at: string | null;
  last_login_at: string | null;
}

export interface UserCreatedOut {
  user: UserOut;
  initial_password: string;
}

export interface PasswordResetOut {
  user: UserOut;
  new_password: string;
}

export interface WeightsOut {
  key: string;
  technical: number;
  fundamental: number;
  updated_at: string | null;
}

export interface WeightsAllOut {
  daily: WeightsOut;
  weekly: WeightsOut;
}

export interface Headline {
  title: string;
  source: string;
  url: string;
  published_at: string;
  sentiment: number;
  matched: boolean;
}

export interface TechnicalDetail {
  rsi: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  sma20: number | null;
  sma50: number | null;
  support: number | null;
  resistance: number | null;
  range_position: number | null;
  volume_trend: string;
  volume_ratio: number | null;
  trend: string;
  change_pct: number | null;
  score: number;
  source: string;
  timeframe_note: string;
}

export interface FundamentalDetail {
  score: number;
  polarity: number | null;
  positives: number;
  negatives: number;
  headlines: Headline[];
  summary: string;
}

export interface ProjectionBreakdown {
  headroom_pct: number;
  trend_factor: number;
  volume_factor: number;
  liquidity_factor: number;
  momentum_pct: number;
  horizon_factor: number;
  volume_ratio: number | null;
  vol_idr: number | null;
  trend: string;
  final_pct: number;
}

export interface TradeLevels {
  entry: number;
  entry_low: number;
  entry_high: number;
  tp1: number;
  tp2: number;
  tp3: number;
  tp1_pct: number;
  tp2_pct: number;
  tp3_pct: number;
  stop_loss: number;
  stop_loss_pct: number;
  risk_reward: number | null;
  max_risk_pct: number;
  support: number | null;
  resistance: number | null;
  key_resistance: number | null;
}

export interface SnapshotItem {
  rank: number;
  pair: string;
  coin: string;
  name: string;
  logo: string;
  score: number;
  technical_score: number;
  fundamental_score: number;
  est_gain_pct: number;
  entry_price: number;
  target_price: number;
  vol_idr: number | null;
  projection: ProjectionBreakdown | null;
  levels: TradeLevels | null;
  technical: TechnicalDetail;
  fundamental: FundamentalDetail;
  snapshot_rank?: number | null;
  snapshot_est_gain_pct?: number | null;
  period_start?: string | null;
  period_end?: string | null;
}

export interface Weights {
  technical: number;
  fundamental: number;
}

export interface Snapshot {
  id: string;
  kind: "daily" | "weekly";
  period_start: string;
  period_end: string;
  generated_at: string;
  weights: Weights;
  items: SnapshotItem[];
  actuals_recorded: boolean;
  approximated: boolean;
  evaluated_at: string | null;
}

export interface SnapshotSummary {
  id: string;
  kind: string;
  period_start: string;
  period_end: string;
  generated_at: string | null;
  top_pairs: string[];
  avg_projected: number;
  evaluated: boolean;
  hit_rate: number | null;
  avg_deviation: number | null;
}

export interface CurrentAnalysis {
  kind: string;
  period_start: string | null;
  period_end: string | null;
  generating: boolean;
  snapshot: Snapshot | null;
}

export interface ActualRow {
  pair: string;
  entry_price: number;
  actual_high: number;
  actual_pct: number;
  projected_pct: number;
  deviation_pct: number;
  target_hit: boolean;
  recorded_at: string | null;
}

export interface EvalDetail {
  snapshot: Snapshot;
  rows: ActualRow[];
  hit_rate: number;
  positive_rate: number;
  avg_deviation: number;
  avg_projected: number;
  avg_actual: number;
}

export interface EvalResponse {
  latest: EvalDetail | null;
  history: SnapshotSummary[];
}

export interface MonthlyPoint {
  date: string;
  hit_rate: number;
  avg_deviation: number;
  avg_projected: number;
  avg_actual: number;
  coins: number;
  positive_rate: number;
}

export interface MonthlyOverall {
  snapshots: number;
  hit_rate: number;
  positive_rate: number;
  avg_deviation: number;
  best_day: MonthlyPoint | null;
  worst_day: MonthlyPoint | null;
}

export interface MonthlyEvalResponse {
  points: MonthlyPoint[];
  overall: MonthlyOverall;
}

export interface LiveRow {
  pair: string;
  name: string;
  rank: number;
  entry_price: number;
  last_price: number | null;
  live_high: number;
  live_high_at: string | null;
  live_gain_pct: number;
  current_gain_pct: number | null;
  projected_pct: number;
  achieved_ratio: number | null;
  target_hit: boolean;
  tp_reached: number;
  sl_touched: boolean;
}

export interface LiveSummary {
  coins: number;
  avg_live_gain_pct: number;
  avg_projected_pct: number;
  positive_count: number;
  target_hit_count: number;
  tp1_count: number;
  tp2_count: number;
  tp3_count: number;
  sl_count: number;
  best: string | null;
}

export interface LiveProgress {
  kind: string;
  period_start: string;
  period_end: string;
  updated_at: string;
  window_label: string;
  rows: LiveRow[];
  summary: LiveSummary;
}

export interface WatchlistEntry {
  pair: string;
  coin: string;
  name: string;
  logo: string;
  created_at: string | null;
  last_price: number | null;
  high_24h: number | null;
  low_24h: number | null;
  vol_idr: number | null;
  snapshot_rank: number | null;
  score: number | null;
  est_gain_pct: number | null;
  in_snapshot: boolean;
}

export interface CoinAccuracyRow {
  pair: string;
  coin: string;
  name: string;
  logo: string;
  appearances: number;
  hits: number;
  hit_rate: number;
  avg_projected: number;
  avg_actual: number;
  avg_deviation: number;
  best_actual: number;
  worst_actual: number;
  last_seen: string | null;
}

export interface CoinAccuracyResponse {
  kind: string;
  rows: CoinAccuracyRow[];
  snapshots_evaluated: number;
  min_appearances: number;
}

export interface PricePoint {
  ts: string;
  price: number;
}

export interface Candle {
  ts: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  from_trades: boolean;
}

export interface CandleSeries {
  pair: string;
  bucket_minutes: number;
  source: string;
  note: string;
  candles: Candle[];
}

export interface SyncStatusOut {
  last_ticker_sync_at: string | null;
  last_ticker_ok: boolean;
  last_ticker_error: string | null;
  last_generation_at: string | null;
  last_generation_error: string | null;
  universe_count: number;
  candidates_last_run: number;
}

export interface CronRunOut {
  job: string;
  status: string;
  detail: string | null;
  at: string | null;
}

export interface MetaStatusOut {
  server_time: string;
  wita_time: string;
  next_daily_run: string;
  next_weekly_run: string;
  sync: SyncStatusOut;
  scheduler: string;
  recent_cron_runs: CronRunOut[];
}
