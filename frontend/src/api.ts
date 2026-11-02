export type ModelOption = {
  label: string;
  value: string;
  reasoning_levels: string[];
  default_reasoning_level: string | null;
};
export type OutputLanguageOption = { label: string; value: string };
export type ReasoningControl = {
  config_key: "google_thinking_level" | "openai_reasoning_effort" | "anthropic_effort";
  env_var: string;
  label: string;
  levels: string[];
  default: string | null;
};
export type Subscription = {
  ready: boolean;
  installed: boolean;
  message: string;
  login_command: string;
  signed_in?: boolean;
  token?: boolean;
  token_env?: string;
  account?: string | null;
  plan?: string | null;
  cli_path?: string;
  version?: string | null;
};
export type Provider = {
  id: string;
  availability: string;
  configured: boolean;
  key_env: string | null;
  quick_models: ModelOption[];
  deep_models: ModelOption[];
  reasoning: ReasoningControl | null;
  masked_key: string | null;
  subscription: Subscription | null;
};

export type ProviderResponse = {
  providers: Provider[];
  output_languages: OutputLanguageOption[];
  defaults: {
    llm_provider: string;
    deep_think_llm: string;
    quick_think_llm: string;
    temperature: number | null;
    max_debate_rounds: number;
    max_risk_discuss_rounds: number;
    checkpoint_enabled: boolean;
    output_language: string;
    google_thinking_level: string | null;
    openai_reasoning_effort: string | null;
    anthropic_effort: string | null;
  };
};

export type Decision = {
  rating: string | null;
  action: string | null;
  price_target: number | null;
  time_horizon: string | null;
  confidence: string | null;
  entry_price: number | null;
  stop_loss: number | null;
};

export type DataOutcome = "ok" | "empty" | "withheld" | "unavailable" | "error";

export type Usage = {
  llm_calls: number;
  tool_calls: number;
  tokens_in: number;
  tokens_out: number;
  estimated_cost_usd: number;
  api_equivalent_usd: number | null;
  // What each data tool returned during the run, by outcome.
  data_coverage: Record<string, Record<DataOutcome, number>> | null;
};

export type RunStatus = "queued" | "running" | "completed" | "failed" | "stopped";
export type RunSummary = {
  id: string;
  ticker: string;
  trade_date: string;
  asset_type: string;
  selected_analysts: string[];
  llm_provider: string;
  deep_think_llm: string;
  quick_think_llm: string;
  temperature: number | null;
  max_debate_rounds: number;
  max_risk_discuss_rounds: number;
  checkpoint_enabled: boolean;
  output_language: string;
  estimated_cost_usd: number;
  status: RunStatus;
  error_message: string | null;
  starred: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  decision: Decision | null;
  usage: Usage | null;
  used_portfolio: boolean;
  backtest_id: string | null;
};

export type RunSection = {
  section_key: string;
  content_md: string;
  structured_json: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type RunNote = {
  id: number;
  note_text: string;
  tags: string[];
  starred: boolean;
  created_at: string;
};

export type RunEvent = {
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type RunDetail = RunSummary & {
  benchmark_ticker: string | null;
  config_snapshot: Record<string, unknown>;
  sections: RunSection[];
  notes: RunNote[];
  events: RunEvent[];
  portfolio_snapshot: Portfolio | null;
  portfolio_context: string | null;
};

export type ChartCandle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type StrategyLevel = {
  kind: "entry" | "take_profit" | "stop_loss";
  price: number;
  source: string;
  label: string | null;
};

export type AnalysisOutcome = {
  date: string;
  close: number;
  latest_date: string;
  latest_close: number;
  change_pct: number;
  sessions: number;
  high: number | null;
  high_date: string | null;
  low: number | null;
  low_date: string | null;
  touches: { kind: StrategyLevel["kind"]; label: string | null; price: number; date: string | null }[];
};

export type RunChart = {
  symbol: string;
  as_of: string;
  current_price: number;
  candles: ChartCandle[];
  levels: StrategyLevel[];
  analysis_date: string | null;
  direction: "long" | "hold" | "reduce" | null;
  notes: string[];
  outcome: AnalysisOutcome | null;
};

export type SecretMask = { name: string; configured: boolean; masked_value: string | null };
export type Preferences = {
  selected_analysts: string[];
  llm_provider: string | null;
  deep_think_llm: string | null;
  quick_think_llm: string | null;
  max_debate_rounds: number;
  max_risk_discuss_rounds: number;
  checkpoint_enabled: boolean;
  output_language: string;
  google_thinking_level: string | null;
  openai_reasoning_effort: string | null;
  anthropic_effort: string | null;
  custom_models: Record<string, string[]>;
};

export type RunPayload = {
  ticker: string;
  trade_date: string;
  asset_type: "stock" | "crypto";
  selected_analysts: string[];
  llm_provider: string;
  deep_think_llm: string;
  quick_think_llm: string;
  temperature: number | null;
  max_debate_rounds: number;
  max_risk_discuss_rounds: number;
  checkpoint_enabled: boolean;
  output_language: string;
  google_thinking_level: string | null;
  openai_reasoning_effort: string | null;
  anthropic_effort: string | null;
  use_portfolio: boolean;
};

// currency: what the average price is in (USD for NVDA, AUD for NXT.AX); the book's own currency labels the cash.
export type Position = { ticker: string; quantity: number; average_price: number | null; currency?: string | null };
// use_by_default: new analyses and backtests start on deciding against this book.
export type Portfolio = { cash: number | null; currency: string | null; positions: Position[]; use_by_default?: boolean };
export type SavedPortfolio = Portfolio & { configured: boolean; use_by_default: boolean; updated_at: string | null };
export type PortfolioPreview = { ticker: string; held: Position | null; context: string };
export type Quote = { ticker: string; last_close: number | null; as_of: string | null; error: string | null };

export type BacktestStatus = "queued" | "running" | "stopping" | "settling" | "completed" | "stopped" | "failed";
export type CellStatus = "queued" | "running" | "completed" | "failed" | "stopped";
export type BacktestCell = {
  position: number;
  ticker: string;
  trade_date: string;
  status: CellStatus;
  run_id: string | null;
  rating: string | null;
  pending: boolean;
  raw_return: number | null;
  alpha_return: number | null;
  holding_days: number | null;
  resolved_date: string | null;
  benchmark: string | null;
  reflection: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
};
export type RatingScore = { count: number; hit_rate: number | null; mean_alpha: number };
export type GroupScore = { resolved: number; directional: number; hit_rate: number | null; mean_alpha: number | null };
export type BacktestScores = {
  upstream: { resolved: number; pending: number; unscored: number; holding: string; by_rating: Record<string, RatingScore>; text: string } | null;
  console: (GroupScore & { per_ticker: Record<string, GroupScore> }) | null;
  settlement: { settled: number; failed: { ticker: string; date: string; reason: string }[] } | null;
};
export type BacktestSummary = {
  id: string;
  name: string | null;
  tickers: string[];
  start_date: string;
  end_date: string;
  every_n_days: number;
  dates: string[];
  asset_type: string;
  selected_analysts: string[];
  llm_provider: string;
  deep_think_llm: string;
  quick_think_llm: string;
  status: BacktestStatus;
  error_message: string | null;
  estimated_cost_usd: number;
  used_portfolio: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  settled_at: string | null;
  progress: Record<CellStatus | "total" | "scored", number>;
  scores: BacktestScores | null;
  usage: { llm_calls: number; tokens_in: number; tokens_out: number; estimated_cost_usd: number; api_equivalent_usd: number };
};
export type BacktestDetail = BacktestSummary & {
  config_snapshot: Record<string, unknown>;
  portfolio_snapshot: Portfolio | null;
  cells: BacktestCell[];
};
export type BacktestPayload = Omit<RunPayload, "ticker" | "trade_date" | "checkpoint_enabled"> & {
  name: string | null;
  tickers: string[];
  start_date: string;
  end_date: string;
  every_n_days: number;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    // FastAPI validation errors arrive as a list of {loc, msg}; show their messages.
    const detail = Array.isArray(body.detail) ? body.detail.map((item: { msg?: string }) => item.msg?.replace(/^Value error, /, "")).filter(Boolean).join("; ") : body.detail;
    throw new Error(detail || "Request failed");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  providers: (fresh = false) => request<ProviderResponse>(`/api/config/providers${fresh ? "?fresh=true" : ""}`),
  vendors: () => request<{ vendors: Record<string, string> }>("/api/config/vendors"),
  costs: () => request<{ prices: Record<string, { input: number; output: number }>; plan_billed_providers: string[] }>("/api/config/costs"),
  subscription: (provider: string) => request<Subscription>(`/api/config/subscriptions/${provider}?fresh=true`),
  // Claude Code's own login, run by the backend for a browser elsewhere (a container has none).
  startClaudeSignIn: () => request<{ url: string; expires_in: number }>("/api/config/subscriptions/claude/sign-in", { method: "POST" }),
  finishClaudeSignIn: (code: string) => request<Subscription>("/api/config/subscriptions/claude/sign-in/code", { method: "POST", body: JSON.stringify({ code }) }),
  cancelClaudeSignIn: () => request<void>("/api/config/subscriptions/claude/sign-in", { method: "DELETE" }),
  runtime: () => request<{ data_directory: string; localhost_only: boolean }>("/api/config/runtime"),
  preferences: () => request<Preferences>("/api/config/preferences"),
  savePreferences: (value: Preferences) => request<Preferences>("/api/config/preferences", { method: "PUT", body: JSON.stringify(value) }),
  secrets: () => request<SecretMask[]>("/api/config/secrets"),
  saveSecret: (name: string, value: string) => request<SecretMask>(`/api/config/secrets/${name}`, { method: "PUT", body: JSON.stringify({ name, value }) }),
  deleteSecret: (name: string) => request<void>(`/api/config/secrets/${name}`, { method: "DELETE" }),
  runs: (query = "") => request<RunSummary[]>(`/api/runs${query ? `?${query}` : ""}`),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  chart: (id: string) => request<RunChart>(`/api/runs/${id}/chart`),
  createRun: (payload: RunPayload) => request<{ run_id: string; status: string; estimated_cost_usd: number }>("/api/runs", { method: "POST", body: JSON.stringify(payload) }),
  deleteRun: (id: string) => request<void>(`/api/runs/${id}`, { method: "DELETE" }),
  star: (id: string, starred: boolean) => request<RunSummary>(`/api/runs/${id}`, { method: "PATCH", body: JSON.stringify({ starred }) }),
  resume: (id: string) => request<{ run_id: string }>(`/api/runs/${id}/resume`, { method: "POST" }),
  stopRun: (id: string) => request<RunSummary>(`/api/runs/${id}/stop`, { method: "POST" }),
  note: (id: string, note_text: string, tags: string[]) => request<RunNote>(`/api/runs/${id}/notes`, { method: "POST", body: JSON.stringify({ note_text, tags, starred: false }) }),
  portfolio: () => request<SavedPortfolio>("/api/portfolio"),
  savePortfolio: (value: Portfolio) => request<SavedPortfolio>("/api/portfolio", { method: "PUT", body: JSON.stringify(value) }),
  clearPortfolio: () => request<void>("/api/portfolio", { method: "DELETE" }),
  portfolioPreview: (ticker: string) => request<PortfolioPreview>(`/api/portfolio/preview?ticker=${encodeURIComponent(ticker)}`),
  portfolioQuotes: () => request<Quote[]>("/api/portfolio/quotes"),
  backtests: () => request<BacktestSummary[]>("/api/backtests"),
  backtest: (id: string) => request<BacktestDetail>(`/api/backtests/${id}`),
  backtestGrid: (start: string, end: string, every: number, tickers: number) => request<{ dates: string[]; cells: number; max_cells: number }>(`/api/backtests/grid?start_date=${start}&end_date=${end}&every_n_days=${every}&tickers=${Math.max(1, tickers)}`),
  createBacktest: (payload: BacktestPayload) => request<{ backtest_id: string; cells: number; estimated_cost_usd: number }>("/api/backtests", { method: "POST", body: JSON.stringify(payload) }),
  stopBacktest: (id: string) => request<BacktestSummary>(`/api/backtests/${id}/stop`, { method: "POST" }),
  resumeBacktest: (id: string) => request<BacktestSummary>(`/api/backtests/${id}/resume`, { method: "POST" }),
  settleBacktest: (id: string) => request<BacktestSummary>(`/api/backtests/${id}/settle`, { method: "POST" }),
  deleteBacktest: (id: string) => request<void>(`/api/backtests/${id}`, { method: "DELETE" }),
};

export function streamUrl(runId: string) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/api/runs/${runId}/stream`;
}
