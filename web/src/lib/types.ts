export type Bar = { t: string; o: number; h: number; l: number; c: number };

export type ClosedTrade = {
  time?: string;
  utc_day?: string;
  symbol?: string;
  side?: string;
  volume?: number;
  price?: number;
  profit?: number;
  ticket?: string;
};

export type Position = {
  symbol: string;
  side: string;
  volume: number;
  price: number;
  profit: number;
  ticket: string;
  verdict?: string;
  why?: string;
  pnl_state?: string;
};

export type Analysis = {
  action?: string;
  bias?: string;
  confidence?: number;
  sl_pips?: number;
  tp_pips?: number;
  trend?: string;
  rsi?: number;
  atr_pips?: number;
  reasons?: string[];
  checks?: Record<string, string>;
};

export type BotState = {
  running: boolean;
  broker?: string;
  trade_mode?: string;
  symbol?: string;
  connected?: boolean;
  last_price?: number | null;
  last_reason?: string;
  last_signal?: string;
  analysis?: Analysis;
  pnl?: {
    today?: number;
    total?: number;
    floating?: number;
    closed?: number;
    status?: string;
    recover?: number;
    start_deposit?: number;
    start_equity?: number;
    history_net?: number;
    history_count?: number;
  };
  closed_trades?: ClosedTrade[];
  positions?: Position[];
  bars?: Bar[];
  bid?: number | null;
  ask?: number | null;
  fills?: number;
  wins?: number;
  win_rate?: number;
  net_edge?: number;
  spread?: number | null;
  hedge_pct?: number;
  hedge_dir?: string;
  open_count?: number;
  max_positions?: number;
  bankroll?: number;
  next_lot?: number;
  risk_pct?: number;
  max_lot?: number;
  poll_seconds?: number;
  min_atr_pips?: number;
  session_start_hour?: number;
  session_end_hour?: number;
  min_score?: number;
  take_atr_mult?: number;
  stop_atr_mult?: number;
  strategy?: string;
  min_entry_seconds?: number;
  htf?: string | null;
  h1?: string | null;
  h4?: string | null;
  max_spread_pips?: number;
  mutex?: {
    required?: boolean;
    magic?: number;
    python_active?: boolean;
    ea_active?: boolean;
    leftover_ea?: boolean;
    owner?: string;
    one_bot?: boolean;
    yielded_to_ea?: boolean;
  };
  blockers?: string[];
  logs?: string[];
  updated?: string;
  login?: number | string;
  server?: string;
  venue?: string;
  fill_venue?: string;
  mt5?: {
    attached?: boolean;
    login?: number | string;
    server?: string;
    company?: string | null;
    currency?: string | null;
    balance?: number | null;
    equity?: number | null;
    credit?: number | null;
    error?: string | null;
    fills_in_terminal?: boolean;
    trade_allowed?: boolean | null;
    trade_expert?: boolean | null;
  };
  account?: {
    login?: number | string;
    server?: string;
    currency?: string;
    balance?: number;
    equity?: number;
    credit?: number;
    note?: string;
  } | null;
};

export type HealthReport = {
  status?: "HEALTHY" | "DEGRADED" | "CRITICAL" | string;
  timestamp?: number;
  checks?: {
    trade_mode_safe?: boolean;
    mt5_process_running?: boolean;
    ipc_loop_responsive?: boolean;
    mutex_lock_clear?: boolean;
    script_paths_ok?: boolean;
  };
  metrics?: {
    ipc_latency_ms?: number;
    active_zombies_detected?: number;
    wine_agent_pids?: number;
  };
  warnings?: string[];
  error_reason?: string | null;
};

export type ApiResult = {
  ok?: boolean;
  locked?: boolean;
  message?: string;
  ticket?: string;
  max_positions?: number;
  fill_venue?: string;
};

export type UnlockStatus = {
  ok?: boolean;
  required: boolean;
  unlocked: boolean;
  message?: string;
};

export type History = {
  px: number[];
  rsi: number[];
  atr: number[];
};

export function emptyHistory(): History {
  return { px: [], rsi: [], atr: [] };
}
