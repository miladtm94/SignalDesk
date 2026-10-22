import type { ReactNode } from "react";
import { AlertCircle, ArrowRight, FlaskConical } from "lucide-react";
import type { ModelOption, OutputLanguageOption, Provider } from "./api";

export const analystOptions = [
  ["market", "Market", "Technical trend, price action and verified market snapshots"],
  ["social", "Sentiment", "Cross-source narrative, intensity and confidence"],
  ["news", "News & macro", "Company catalysts, global context and insider activity"],
  ["fundamentals", "Fundamentals", "Financial statements, quality and valuation"],
] as const;

// Billed to the person's own ChatGPT or Claude plan rather than per token.
export const planBilledProviders = new Set(["claude", "chatgpt"]);
export const isPlanBilled = (provider?: string | null) => planBilledProviders.has(provider ?? "");

export function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-AU", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
}
export function formatShortDate(value: string) {
  return new Intl.DateTimeFormat("en-AU", { day: "2-digit", month: "short" }).format(new Date(`${value}T00:00:00`));
}
// SQLite hands timestamps back without their UTC offset; read those as UTC, not local time.
export function parseTimestamp(value: string) {
  return new Date(/(?:[zZ]|[+-]\d\d:?\d\d)$/.test(value) ? value : `${value}Z`);
}
// Today (or an offset from it) as YYYY-MM-DD on this machine's calendar, not UTC's.
export function localISODate(offsetDays = 0) {
  const day = new Date();
  day.setDate(day.getDate() + offsetDays);
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
}
export function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("en-AU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(parseTimestamp(value));
}
export function formatClock(value: string) {
  return new Intl.DateTimeFormat("en-AU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(parseTimestamp(value));
}
export function formatSignedPercent(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}
export function formatPercent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}
export function formatNumber(value: number | null | undefined, digits = 2) {
  return value === null || value === undefined ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}
export function ratingTone(rating?: string | null) {
  if (["Buy", "Overweight"].includes(rating ?? "")) return "positive";
  if (["Sell", "Underweight"].includes(rating ?? "")) return "negative";
  return "neutral";
}

export function providerName(id: string) {
  const names: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", google: "Google Gemini", xai: "xAI", ollama: "Ollama", openrouter: "OpenRouter", deepseek: "DeepSeek", bedrock: "Amazon Bedrock", azure: "Azure OpenAI", openai_compatible: "OpenAI-compatible", claude: "Claude plan", chatgpt: "ChatGPT plan", glm: "GLM (Z.AI)", "glm-cn": "GLM (BigModel, China)", qwen: "Qwen", "qwen-cn": "Qwen (China)", minimax: "MiniMax", "minimax-cn": "MiniMax (China)", kimi: "Kimi (Moonshot)", groq: "Groq", nvidia: "NVIDIA NIM", mistral: "Mistral" };
  return names[id] ?? id.split("-").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

export function selectableModels(provider: Provider | undefined, mode: "quick" | "deep", customModels: string[] = []): ModelOption[] {
  if (!provider) return [];
  const base = (mode === "quick" ? provider.quick_models : provider.deep_models).filter((model) => model.value !== "custom");
  const known = new Set(base.map((model) => model.value));
  const additions = customModels.filter((model) => !known.has(model)).map((model) => ({ label: `${model} · Added`, value: model, reasoning_levels: provider.reasoning?.levels ?? [], default_reasoning_level: provider.reasoning?.default ?? null }));
  const custom = (mode === "quick" ? provider.quick_models : provider.deep_models).find((model) => model.value === "custom") ?? { label: "Enter another model ID", value: "custom", reasoning_levels: provider.reasoning?.levels ?? [], default_reasoning_level: provider.reasoning?.default ?? null };
  return [...base, ...additions, custom];
}

export type ReasoningSettings = { google_thinking_level: string | null; openai_reasoning_effort: string | null; anthropic_effort: string | null };
export function reasoningValue(settings: ReasoningSettings, provider?: Provider) { return provider?.reasoning ? settings[provider.reasoning.config_key] ?? "" : ""; }
export function withReasoning<T extends ReasoningSettings>(settings: T, provider: Provider | undefined, level: string): T { return provider?.reasoning ? { ...settings, [provider.reasoning.config_key]: level || null } : settings; }
export function sharedReasoningLevels(provider: Provider | undefined, quick: string | null, deep: string | null, quickModels: ModelOption[], deepModels: ModelOption[]) {
  if (!provider?.reasoning) return [];
  const quickLevels = quickModels.find((model) => model.value === quick)?.reasoning_levels ?? provider.reasoning.levels;
  const deepLevels = deepModels.find((model) => model.value === deep)?.reasoning_levels ?? provider.reasoning.levels;
  return provider.reasoning.levels.filter((level) => quickLevels.includes(level) && deepLevels.includes(level));
}

export function LanguageField({ value, options, onChange }: { value: string; options: OutputLanguageOption[]; onChange: (value: string) => void }) {
  const selected = options.some((option) => option.value === value) ? value : "custom";
  return <label className="language-field"><span>Output language</span><select value={selected} onChange={(event) => onChange(event.target.value === "custom" ? "" : event.target.value)}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}<option value="custom">Custom language</option></select>{selected === "custom" && <input value={value} onChange={(event) => onChange(event.target.value)} placeholder="e.g. Turkish, Vietnamese, Thai" aria-label="Custom output language" />}</label>;
}

export function PageHeader({ eyebrow, title, copy, action }: { eyebrow: string; title: string; copy?: string; action?: ReactNode }) {
  return <header className="topbar"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1>{copy && <p>{copy}</p>}</div>{action}</header>;
}
export function Metric({ label, value, detail }: { label: string; value: string; detail: string }) { return <article className="metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>; }
export function FormHeading({ step, title, hint }: { step: string; title: string; hint?: string }) { return <div className="section-heading"><div><span className="step">{step}</span><h2>{title}</h2></div>{hint && <span className="hint">{hint}</span>}</div>; }
export function Stepper({ label, value, onChange, min = 1, max = 10 }: { label: string; value: number; onChange: (value: number) => void; min?: number; max?: number }) { return <label><span>{label}</span><div className="stepper"><button onClick={() => onChange(Math.max(min, value - 1))}>−</button><strong>{value}</strong><button onClick={() => onChange(Math.min(max, value + 1))}>+</button></div></label>; }
export function Status({ value }: { value: string }) { return <span className={`status status-${value}`}><i /> {value}</span>; }
export function OfflineNotice({ message }: { message: string }) { return <div className="offline-notice"><AlertCircle size={16} /><div><strong>Backend is not connected</strong><span>{message}. Start the local service to load real run history.</span></div></div>; }
export function LoadingRows() { return <div className="loading-rows">{[1, 2, 3].map((i) => <span key={i} />)}</div>; }
export function EmptyState({ title, copy, action, onAction, compact = false }: { title: string; copy: string; action: string; onAction: () => void; compact?: boolean }) { return <div className={`empty-state ${compact ? "compact" : ""}`}><div><FlaskConical size={21} /></div><h2>{title}</h2><p>{copy}</p><button className="primary-button" onClick={onAction}>{action} <ArrowRight size={14} /></button></div>; }
