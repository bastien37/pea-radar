// Client de l'API REST. Le jeton de session vit dans un cookie httpOnly : il n'est
// jamais accessible au JavaScript, donc rien de sensible n'est stocké côté navigateur.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg).join(" ; ") : `Erreur ${res.status}`;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
};

export interface User { id: number; email: string }

export interface Headline { title: string; source: string; published: string; link: string }

export interface News {
  status: "ok" | "no_key" | "no_news" | "error";
  headlines: Headline[];
  model: string | null;
  sentiment?: number;
  confidence?: number;
  summary?: string;
  catalysts?: string[];
  risks?: string[];
}

export interface HorizonStat {
  days: number; samples: number; median_pct: number; low_pct: number; high_pct: number; win_rate: number;
}

export interface Opportunity {
  id: number; rank: number; ticker: string; name: string; price: number; price_date: string;
  score: number; adjusted_score: number; risk_level: number; risk_label: string;
  expected_gain_pct: number; gain_low_pct: number; gain_high_pct: number; win_rate: number;
  holding_days: number; stop_loss: number; news: News; sector: string | null;
  details: {
    indicators: Record<string, number | null>;
    subscores: Record<string, number>;
    estimate: { basis: string; samples: number; by_horizon: HorizonStat[] };
    global_score: number;
    fundamentals: Partial<Record<"pe" | "forward_pe" | "earnings_growth" | "debt_to_equity" | "roe" | "dividend_yield_pct", number | null>> & { next_earnings?: string | null };
    fundamental_score: number | null;
    earnings_in_days: number | null;
    earnings_warning: boolean;
    market_unfavorable: boolean;
  };
}

export interface RankRow {
  rank: number; ticker: string; name: string; sector: string; price: number; score: number; risk_level: number;
  rsi: number; stoch_k: number; perf_3m_pct: number; volatility_pct: number;
}

export interface ScanInfo {
  id: number; status: "pending" | "running" | "done" | "failed"; progress: string;
  error: string | null; started_at: string; finished_at: string | null;
}

export interface Regime {
  index: string; close: number; sma200: number; favorable: boolean; distance_pct: number; date: string;
}

export interface EtfRow {
  ticker: string; name: string; index: string; price: number; perf_3m_pct: number;
  perf_1y_pct: number | null; volatility_pct: number;
}

export interface ScanResult extends ScanInfo {
  opportunities: Opportunity[]; ranking: RankRow[];
  market: { regime: Regime | null; etfs: EtfRow[] };
}

export interface Alert { id: number; kind: string; ticker: string; message: string; read: boolean; created_at: string }

export interface Latest { current: ScanInfo | null; result: ScanResult | null }

export interface Position {
  ticker: string; name: string; quantity: number; avg_cost: number; price: number;
  value: number; pnl: number; pnl_pct: number;
  opened_on: string | null; holding_days: number | null; elapsed_days: number | null;
  stop_loss: number | null; target_price: number | null; trigger: string | null;
}

export interface Tax {
  pea_opened_on: string; years: number; after_5_years: boolean; rate_pct: number;
  social_rate_pct: number; income_tax_rate_pct: number;
  gross_gain: number; tax: number; net_gain: number; net_value: number;
}

export interface Portfolio {
  initial_cash: number; cash: number; invested: number; total_value: number;
  performance_pct: number; realized_pnl: number; fees_paid: number; fee_rate: number;
  auto_follow: boolean; auto_follow_positions: number; tax: Tax;
  positions: Position[];
}

export interface Trade {
  id: number; ticker: string; side: "buy" | "sell"; quantity: number; price: number;
  fee: number; realized_pnl: number | null; executed_at: string;
}

export interface TrackItem {
  ticker: string; name: string; date: string; entry_price: number; score: number;
  expected_gain_pct: number; holding_days: number; elapsed_days: number;
  outcome_pct: number; matured: boolean; verdict: string;
}

export interface Track {
  summary: { total: number; closed: number; win_rate: number | null;
             avg_outcome_pct: number | null; avg_expected_pct: number | null };
  items: TrackItem[];
}

export interface SeriesPoint {
  date: string; close: number | null; sma20: number | null; sma50: number | null;
  sma200: number | null; rsi: number | null; stoch_k: number | null; stoch_d: number | null;
  macd_hist: number | null; score: number | null;
}

export const eur = (n: number) =>
  n.toLocaleString("fr-FR", { style: "currency", currency: "EUR" });
export const pct = (n: number, digits = 1) =>
  `${n > 0 ? "+" : ""}${n.toLocaleString("fr-FR", { maximumFractionDigits: digits })} %`;
export const weeks = (tradingDays: number) => {
  const w = Math.round(tradingDays / 5);
  return w >= 9 ? `≈ ${Math.round(w / 4.35)} mois` : `≈ ${w} semaines`;
};

export interface Metrics {
  total_return_pct: number; cagr_pct: number; max_drawdown_pct: number; volatility_pct: number; sharpe: number;
}

export interface BtTrade { ticker: string; entry: string; exit: string; days: number; reason: string; pnl_pct: number }

export interface BacktestParams {
  years: number; positions: number; holding_days: number; stop_atr: number; min_score: number;
  rebalance_days: number; max_per_sector: number; market_filter: boolean;
}

export interface BacktestResult {
  period: { start: string; end: string; tickers: number };
  metrics: Record<string, Metrics>;
  trades: {
    count: number; exposure_pct: number; win_rate: number | null; avg_pnl_pct: number | null;
    avg_days: number | null; stopped_pct: number | null; best: BtTrade[]; worst: BtTrade[];
  };
  yearly: ({ year: number } & Record<string, number>)[];
  curve: ({ date: string } & Record<string, number | string>)[];
  notes: string[];
}

export interface Backtest {
  id: number; status: "pending" | "running" | "done" | "failed"; error: string | null;
  params: BacktestParams; result: BacktestResult | null; finished_at: string | null;
}

export interface WfStrategy extends Metrics {
  name: string; reference: boolean; years_won: number | null; years: number;
  turnover_pct_per_year: number | null; beats_reference: boolean | null;
}

export interface WalkForwardResult {
  params: { years: number; train_years: number; fee_rate: number };
  period: { start: string; end: string; tickers: number; test_years: number };
  reference: string; rule: string; strategies: WfStrategy[]; winners: string[];
  yearly: ({ year: number } & Record<string, number>)[];
  choices: ({ year: number } & Record<string, string | number>)[];
  curve: ({ date: string } & Record<string, number | string>)[];
  notes: string[];
}

export interface WalkForward {
  id: number; status: "pending" | "running" | "done" | "failed"; error: string | null;
  result: WalkForwardResult | null; finished_at: string | null;
}
