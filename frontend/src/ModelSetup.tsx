import { useEffect } from "react";
import { BadgeCheck, KeyRound } from "lucide-react";
import { api, Preferences, ProviderResponse } from "./api";
import { isPlanBilled, providerName, ReasoningSettings, reasoningValue, selectableModels, sharedReasoningLevels, withReasoning } from "./shared";

export type ModelFields = ReasoningSettings & { llm_provider: string; quick_think_llm: string; deep_think_llm: string };
export type Prices = Record<string, { input: number; output: number }>;
export type ModelDefaults = {
  config: ProviderResponse;
  prices: Prices;
  prefs: Preferences;
  savedModels: Record<string, string[]>;
  fields: ModelFields | null;
};

/** The provider catalog, prices and saved defaults, with the model fields a new form starts from. */
export async function loadModelDefaults(): Promise<ModelDefaults> {
  const [config, costs, prefs] = await Promise.all([api.providers(), api.costs(), api.preferences()]);
  const ready = config.providers.filter((p) => p.configured);
  const provider = ready.find((p) => p.id === prefs.llm_provider) ?? ready.find((p) => p.id === config.defaults.llm_provider) ?? ready[0];
  const savedModels = { ...prefs.custom_models };
  if (!provider) return { config, prices: costs.prices, prefs, savedModels, fields: null };
  const known = new Set([...provider.quick_models, ...provider.deep_models].map((model) => model.value));
  [prefs.quick_think_llm, prefs.deep_think_llm].forEach((model) => { if (model && model !== "custom" && !known.has(model)) savedModels[provider.id] = [...new Set([...(savedModels[provider.id] ?? []), model])]; });
  const quickModels = selectableModels(provider, "quick", savedModels[provider.id]), deepModels = selectableModels(provider, "deep", savedModels[provider.id]);
  return {
    config, prices: costs.prices, prefs, savedModels,
    fields: {
      llm_provider: provider.id,
      quick_think_llm: quickModels.some((m) => m.value === prefs.quick_think_llm) ? prefs.quick_think_llm! : quickModels[0]?.value ?? "",
      deep_think_llm: deepModels.some((m) => m.value === prefs.deep_think_llm) ? prefs.deep_think_llm! : deepModels[0]?.value ?? "",
      google_thinking_level: prefs.google_thinking_level,
      openai_reasoning_effort: prefs.openai_reasoning_effort,
      anthropic_effort: prefs.anthropic_effort,
    },
  };
}

/** Rough per-run guardrail: tool-heavy analyst calls and longer synthesis calls; a plan bills none of it. */
export function estimateRunCost(prices: Prices, fields: ModelFields, analysts: number, debateRounds: number, riskRounds: number) {
  if (isPlanBilled(fields.llm_provider)) return 0;
  const rate = (model: string) => prices[model] ?? prices.default ?? { input: .002, output: .008 };
  const quickCalls = analysts * 2 + 1, deepCalls = debateRounds * 2 + riskRounds * 3 + 2, q = rate(fields.quick_think_llm), d = rate(fields.deep_think_llm);
  return quickCalls * (5 * q.input + 1.5 * q.output) + deepCalls * (8 * d.input + 2 * d.output);
}

export function ModelSetup({ config, value, savedModels, quickCustom, deepCustom, onQuickCustom, onDeepCustom, onChange, onSettings }: {
  config: ProviderResponse | null;
  value: ModelFields;
  savedModels: Record<string, string[]>;
  quickCustom: string;
  deepCustom: string;
  onQuickCustom: (value: string) => void;
  onDeepCustom: (value: string) => void;
  onChange: (value: ModelFields) => void;
  onSettings: () => void;
}) {
  const provider = config?.providers.find((item) => item.id === value.llm_provider);
  const quickModels = selectableModels(provider, "quick", savedModels[value.llm_provider]), deepModels = selectableModels(provider, "deep", savedModels[value.llm_provider]);
  const reasoningLevels = sharedReasoningLevels(provider, value.quick_think_llm, value.deep_think_llm, quickModels, deepModels);
  const readyProviders = config?.providers.filter((item) => item.configured) ?? [];
  useEffect(() => { const current = reasoningValue(value, provider); if (current && !reasoningLevels.includes(current)) onChange(withReasoning(value, provider, "")); }, [value.llm_provider, value.quick_think_llm, value.deep_think_llm, reasoningLevels.join(":")]);
  const updateProvider = (id: string) => { const next = config?.providers.find((item) => item.id === id), quick = selectableModels(next, "quick", savedModels[id]), deep = selectableModels(next, "deep", savedModels[id]); onChange(withReasoning({ ...value, llm_provider: id, quick_think_llm: quick[0]?.value ?? "", deep_think_llm: deep[0]?.value ?? "" }, next, "")); };
  if (!readyProviders.length) return <div className="connect-provider"><KeyRound size={18} /><div><strong>No model provider is ready</strong><span>Add a server-side key, sign in to a Claude or ChatGPT plan, or connect Ollama in settings.</span></div><button onClick={onSettings}>Open settings</button></div>;
  const plan = provider?.subscription;
  return <>
    <div className="config-row">
      <label><span>Active provider</span><select value={value.llm_provider} onChange={(e) => updateProvider(e.target.value)}>{readyProviders.map((item) => <option key={item.id} value={item.id}>{providerName(item.id)}</option>)}</select></label>
      <label><span>Quick model</span><select value={value.quick_think_llm} onChange={(e) => onChange({ ...value, quick_think_llm: e.target.value })}>{quickModels.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      <label><span>Deep model</span><select value={value.deep_think_llm} onChange={(e) => onChange({ ...value, deep_think_llm: e.target.value })}>{deepModels.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      {provider?.reasoning && reasoningLevels.length > 0 && <label className="reasoning-field"><span>{provider.reasoning.label}</span><select value={reasoningLevels.includes(reasoningValue(value, provider)) ? reasoningValue(value, provider) : ""} onChange={(e) => onChange(withReasoning(value, provider, e.target.value))}><option value="">Provider default</option>{reasoningLevels.map((level) => <option key={level} value={level}>{level}{level === provider.reasoning?.default ? " · recommended" : ""}</option>)}</select><small>{provider.reasoning.env_var} · applies to both quick and deep models</small></label>}
    </div>
    {(value.quick_think_llm === "custom" || value.deep_think_llm === "custom") && <div className="custom-models">{value.quick_think_llm === "custom" && <label><span>Quick model ID</span><input value={quickCustom} onChange={(e) => onQuickCustom(e.target.value)} placeholder="Exact provider model ID" /></label>}{value.deep_think_llm === "custom" && <label><span>Deep model ID</span><input value={deepCustom} onChange={(e) => onDeepCustom(e.target.value)} placeholder="Exact provider model ID" /></label>}</div>}
    {plan && <div className="plan-note"><BadgeCheck size={16} /><div><strong>{value.llm_provider === "claude" ? "Runs on your Claude plan through Claude Code" : "Runs on your ChatGPT plan"}{plan.account ? ` · ${plan.account}` : ""}{plan.plan ? ` · ${plan.plan.charAt(0).toUpperCase()}${plan.plan.slice(1)}` : ""}</strong><span>Each call counts toward your plan's usage limits instead of a per-token charge. Temperature is not used on a plan.</span></div></div>}
  </>;
}
