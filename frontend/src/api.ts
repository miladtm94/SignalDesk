export type ModelOption = { label: string; value: string };
export type Provider = {
  id: string;
  availability: string;
  quick_models: ModelOption[];
  deep_models: ModelOption[];
  masked_key: string | null;
};

export type ProviderResponse = {
  providers: Provider[];
  defaults: {
    llm_provider: string;
    deep_think_llm: string;
    quick_think_llm: string;
    temperature: number | null;
    max_debate_rounds: number;
    max_risk_discuss_rounds: number;
    checkpoint_enabled: boolean;
    output_language: string;
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

export type Usage = {
  llm_calls: number;
  tool_calls: number;
  tokens_in: number;
  tokens_out: number;
  estimated_cost_usd: number;
};

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
  status: "queued" | "running" | "completed" | "failed";
  error_message: string | null;
  starred: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  decision: Decision | null;
  usage: Usage | null;
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
};

export type RunChart = {
  symbol: string;
  as_of: string;
  current_price: number;
  candles: ChartCandle[];
  levels: StrategyLevel[];
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
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? "Request failed");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  providers: () => request<ProviderResponse>("/api/config/providers"),
  vendors: () => request<{ vendors: Record<string, string> }>("/api/config/vendors"),
  costs: () => request<{ prices: Record<string, { input: number; output: number }> }>("/api/config/costs"),
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
  note: (id: string, note_text: string, tags: string[]) => request<RunNote>(`/api/runs/${id}/notes`, { method: "POST", body: JSON.stringify({ note_text, tags, starred: false }) }),
};

export function streamUrl(runId: string) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/api/runs/${runId}/stream`;
}
