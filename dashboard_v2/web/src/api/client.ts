/* Shared REST + WebSocket client. All endpoints are relative paths
   that vite proxies to the FastAPI backend in dev. */

export type Portfolio = {
  starting_bankroll: number;
  cash: number;
  open_position_cost: number;
  realized_pnl: number;
  bankroll: number;
  n_open_positions: number;
  n_open_kalshi: number;
  n_open_polymarket: number;
};

export type EquityPoint = { timestamp_unix: number; equity: number; venue?: string | null };

export type Position = {
  id: number; venue: string; ticker: string; side: string;
  contracts: number; avg_price: number; opened_at: string;
  closed_at: string | null; realized_pnl: number;
};

export type Fill = {
  id: number; placed_at: string; venue: string; strategy: string;
  ticker: string; side: string; action: string; contracts: number;
  price: number; fees: number; cost: number; is_maker: boolean;
  notes: string | null;
};

export type CohortWallet = {
  wallet: string; rank: number; score: number;
  realized_pnl_usd: number; n_trades: number; n_resolved: number;
  last_trade_unix: number; pnl_stability: number;
  in_cohort_since_unix: number | null;
};

export type Signal = {
  idempotency_key: string; detected_at: string;
  condition_id: string; outcome_token_id: string; outcome_index: number;
  market_question: string | null; cohort_size: number; consensus_k: number;
  agreeing_wallets: string[]; first_trade_unix: number;
  last_trade_unix: number; window_start_unix: number; window_end_unix: number;
  cohort_version: string; avg_wallet_entry_price: number;
  total_wallet_notional_usd: number; decision: string;
  executed_price: number | null; executed_contracts: number | null;
  slippage_cents: number | null; decision_unix: number;
  notes: string | null; latency_seconds: number | null;
};

export type LatencyBucket = { upper_seconds: number; count: number };

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

export const api = {
  portfolio: (venue?: string) => getJSON<Portfolio>(`/api/portfolio${venue ? `?venue=${venue}` : ""}`),
  equity:    (venue?: string) => getJSON<EquityPoint[]>(`/api/equity${venue ? `?venue=${venue}` : ""}`),
  positions: (venue?: string) => getJSON<Position[]>(`/api/positions${venue ? `?venue=${venue}` : ""}`),
  fills:     (venue?: string) => getJSON<Fill[]>(`/api/fills${venue ? `?venue=${venue}` : ""}`),
  signals:   () => getJSON<Signal[]>("/api/signals?limit=200"),
  cohort:    () => getJSON<CohortWallet[]>("/api/cohort"),
  latency:   () => getJSON<LatencyBucket[]>("/api/latency"),
  health:    () => getJSON<{ ok: boolean }>("/api/health"),
};

/* ----- WebSocket with auto-reconnect ----- */

export type StreamEvent =
  | { type: "fill"; payload: Fill; ts_unix: number }
  | { type: "signal"; payload: Signal; ts_unix: number }
  | { type: "heartbeat"; ts_unix: number };

export class StreamClient {
  private ws: WebSocket | null = null;
  private retry = 0;
  private dead = false;
  constructor(private onEvent: (e: StreamEvent) => void, private onStatus: (alive: boolean) => void) {}
  connect() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws`;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => { this.retry = 0; this.onStatus(true); };
    ws.onmessage = (m) => {
      try { this.onEvent(JSON.parse(m.data)); } catch {}
    };
    ws.onclose = () => {
      this.onStatus(false);
      if (this.dead) return;
      this.retry = Math.min(this.retry + 1, 6);
      setTimeout(() => this.connect(), 500 * 2 ** this.retry);
    };
    ws.onerror = () => ws.close();
  }
  close() { this.dead = true; this.ws?.close(); }
}
