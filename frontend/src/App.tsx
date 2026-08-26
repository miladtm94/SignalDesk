import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { CandlestickSeries, ColorType, createChart, LineStyle } from "lightweight-charts";
import {
  Activity, AlertCircle, ArrowLeft, ArrowRight, BarChart3, BookOpen, Bot,
  Check, CheckCircle2, ChevronDown, CircleDollarSign, Download, FileText,
  FlaskConical, History, KeyRound, Menu, MessageSquarePlus, Play, RefreshCw,
  Search, Settings, ShieldCheck, Sparkles, Star, Trash2, X,
} from "lucide-react";
import { api, Preferences, ProviderResponse, RunChart, RunDetail, RunPayload, RunSection, RunSummary, SecretMask, streamUrl } from "./api";

type View = "dashboard" | "new" | "run" | "compare" | "settings";
const analystOptions = [
  ["market", "Market", "Technical trend, price action and verified market snapshots"],
  ["social", "Sentiment", "Cross-source narrative, intensity and confidence"],
  ["news", "News & macro", "Company catalysts, global context and insider activity"],
  ["fundamentals", "Fundamentals", "Financial statements, quality and valuation"],
] as const;
const sectionMeta: Record<string, { label: string; group: string; tone: string }> = {
  portfolio_manager: { label: "Portfolio manager", group: "Final decision", tone: "lime" },
  research_manager: { label: "Research manager", group: "Research decision", tone: "teal" },
  trader: { label: "Trader proposal", group: "Trading plan", tone: "blue" },
  risk_aggressive: { label: "Aggressive risk", group: "Risk debate", tone: "red" },
  risk_conservative: { label: "Conservative risk", group: "Risk debate", tone: "amber" },
  risk_neutral: { label: "Neutral risk", group: "Risk debate", tone: "violet" },
  market_report: { label: "Market analyst", group: "Analyst desk", tone: "teal" },
  sentiment_report: { label: "Sentiment analyst", group: "Analyst desk", tone: "violet" },
  news_report: { label: "News & macro analyst", group: "Analyst desk", tone: "blue" },
  fundamentals_report: { label: "Fundamentals analyst", group: "Analyst desk", tone: "amber" },
  bull: { label: "Bull researcher", group: "Research debate", tone: "lime" },
  bear: { label: "Bear researcher", group: "Research debate", tone: "red" },
};
const sectionOrder = Object.keys(sectionMeta);

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-AU", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
}
function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("en-AU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
function formatClock(value: string) {
  return new Intl.DateTimeFormat("en-AU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}
function ratingTone(rating?: string | null) {
  if (["Buy", "Overweight"].includes(rating ?? "")) return "positive";
  if (["Sell", "Underweight"].includes(rating ?? "")) return "negative";
  return "neutral";
}

export default function App() {
  const [view, setView] = useState<View>("dashboard");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = (next: View) => { setView(next); setMenuOpen(false); window.scrollTo(0, 0); };
  const openRun = (id: string) => { setActiveRunId(id); navigate("run"); };

  return <div className="app-shell">
    <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
      <div className="brand"><div className="brand-mark">S</div><div><strong>Signal Desk</strong><span>Research console</span></div><button className="close-menu" onClick={() => setMenuOpen(false)}><X size={18} /></button></div>
      <nav>
        <button className={view === "dashboard" ? "active" : ""} onClick={() => navigate("dashboard")}><BarChart3 size={18} /> Overview</button>
        <button className={view === "new" ? "active" : ""} onClick={() => navigate("new")}><FlaskConical size={18} /> New analysis</button>
        <button className={view === "run" ? "active" : ""} onClick={() => activeRunId ? navigate("run") : navigate("dashboard")}><History size={18} /> Run detail</button>
        <button className={view === "compare" ? "active" : ""} onClick={() => navigate("compare")}><BookOpen size={18} /> Compare notes</button>
      </nav>
      <div className="sidebar-spacer" /><div className="local-card"><ShieldCheck size={17} /><div><strong>Local workspace</strong><span>Keys stay on this machine</span></div></div>
      <button className={`settings-link ${view === "settings" ? "active" : ""}`} onClick={() => navigate("settings")}><Settings size={18} /> Settings</button>
    </aside>
    <button className="mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open navigation"><Menu size={20} /></button>
    <main>
      {view === "dashboard" && <Dashboard onNew={() => navigate("new")} onOpen={openRun} compareIds={compareIds} setCompareIds={setCompareIds} onCompare={() => navigate("compare")} />}
      {view === "new" && <NewAnalysis onLaunched={openRun} onSettings={() => navigate("settings")} />}
      {view === "run" && activeRunId && <LiveRun runId={activeRunId} onBack={() => navigate("dashboard")} />}
      {view === "run" && !activeRunId && <EmptyState title="Choose a run from history" copy="Open a past run to read its complete research trail." action="Browse history" onAction={() => navigate("dashboard")} />}
      {view === "compare" && <CompareRuns ids={compareIds} onOpen={openRun} onBack={() => navigate("dashboard")} />}
      {view === "settings" && <SettingsView />}
    </main>
    <div className="disclaimer"><Activity size={15} /><span><strong>Research workspace, not financial advice.</strong> Ratings, actions and price targets are model-generated hypotheses. Verify independently before making any decision.</span></div>
    {menuOpen && <button className="menu-scrim" onClick={() => setMenuOpen(false)} aria-label="Close navigation" />}
  </div>;
}

function PageHeader({ eyebrow, title, copy, action }: { eyebrow: string; title: string; copy?: string; action?: React.ReactNode }) {
  return <header className="topbar"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1>{copy && <p>{copy}</p>}</div>{action}</header>;
}

function Dashboard({ onNew, onOpen, compareIds, setCompareIds, onCompare }: { onNew: () => void; onOpen: (id: string) => void; compareIds: string[]; setCompareIds: (ids: string[]) => void; onCompare: () => void }) {
  const [runs, setRuns] = useState<RunSummary[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState(""), [query, setQuery] = useState(""), [onlyStarred, setOnlyStarred] = useState(false), [removingId, setRemovingId] = useState<string | null>(null);
  useEffect(() => { api.runs().then(setRuns).catch((e) => setError(e.message)).finally(() => setLoading(false)); }, []);
  const filtered = runs.filter((run) => (!query || run.ticker.includes(query.toUpperCase())) && (!onlyStarred || run.starred));
  const complete = runs.filter((run) => run.status === "completed");
  const avgCost = complete.length ? complete.reduce((sum, run) => sum + (run.usage?.estimated_cost_usd ?? 0), 0) / complete.length : 0;
  const toggleCompare = (id: string) => setCompareIds(compareIds.includes(id) ? compareIds.filter((item) => item !== id) : [...compareIds.slice(-1), id]);
  const removeRun = async (run: RunSummary) => { if (!window.confirm(`Remove the ${run.ticker} analysis from history? Its saved report files and notes will also be permanently removed.`)) return; setRemovingId(run.id); setError(""); try { await api.deleteRun(run.id); setRuns((current) => current.filter((item) => item.id !== run.id)); setCompareIds(compareIds.filter((item) => item !== run.id)); } catch (e) { setError(e instanceof Error ? e.message : "The analysis could not be removed"); } finally { setRemovingId(null); } };
  return <><PageHeader eyebrow="RESEARCH NOTEBOOK" title="Your signal archive" copy="Every thesis, debate and outcome — with the assumptions left attached." action={<button className="primary-button" onClick={onNew}><Sparkles size={16} /> New analysis</button>} />
    <section className="metric-grid"><Metric label="Research runs" value={String(runs.length).padStart(2, "0")} detail={`${runs.filter((r) => r.status === "running").length} active now`} /><Metric label="Completed" value={String(complete.length).padStart(2, "0")} detail={`${runs.filter((r) => r.status === "failed").length} partial / failed`} /><Metric label="Avg. run cost" value={`$${avgCost.toFixed(2)}`} detail="Estimated from token metadata" /><Metric label="Starred calls" value={String(runs.filter((r) => r.starred).length).padStart(2, "0")} detail="Pinned for review" /></section>
    <section className="table-card"><div className="table-toolbar"><div><h2>Run history</h2><span>{filtered.length} recorded analyses</span></div><div className="filter-actions"><label className="search-field"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter ticker" /></label><button className={onlyStarred ? "filter active" : "filter"} onClick={() => setOnlyStarred(!onlyStarred)}><Star size={14} /> Starred</button>{compareIds.length === 2 && <button className="compare-button" onClick={onCompare}>Compare 2 runs <ArrowRight size={14} /></button>}</div></div>
      {error && <OfflineNotice message={error} />}{loading ? <LoadingRows /> : filtered.length ? <div className="run-table-wrap"><table className="run-table"><thead><tr><th></th><th>Ticker</th><th>Research date</th><th>Final rating</th><th>Trader action</th><th>Model record</th><th>Usage</th><th>Status</th><th>Actions</th></tr></thead><tbody>{filtered.map((run) => <tr key={run.id}><td><label className="compare-check"><input type="checkbox" checked={compareIds.includes(run.id)} onChange={() => toggleCompare(run.id)} /><span>{compareIds.includes(run.id) && <Check size={11} />}</span></label></td><td><button className="ticker-link" onClick={() => onOpen(run.id)}>{run.ticker}</button>{run.starred && <Star className="starred" size={11} fill="currentColor" />}</td><td>{formatDate(run.trade_date)}</td><td><span className={`rating ${ratingTone(run.decision?.rating)}`}>{run.decision?.rating ?? "—"}</span></td><td>{run.decision?.action ?? "—"}</td><td><strong>{run.deep_think_llm}</strong><small>{run.llm_provider} · T {run.temperature ?? "default"}</small></td><td><strong>{((run.usage?.tokens_in ?? 0) + (run.usage?.tokens_out ?? 0)).toLocaleString()} tok</strong><small>${run.usage?.estimated_cost_usd.toFixed(2) ?? "0.00"} est.</small></td><td><Status value={run.status} /></td><td><button className="remove-run" disabled={run.status === "running" || removingId === run.id} onClick={() => removeRun(run)} aria-label={`Remove ${run.ticker} analysis`} title={run.status === "running" ? "Wait for the running analysis to finish before removing it" : "Remove analysis"}>{removingId === run.id ? <RefreshCw className="spin" size={14} /> : <Trash2 size={14} />}<span>{removingId === run.id ? "Removing…" : "Remove"}</span></button></td></tr>)}</tbody></table></div> : <EmptyState title="No research runs yet" copy="Start with a ticker you know and keep the complete reasoning trail." action="Run first analysis" onAction={onNew} compact />}</section></>;
}
function Metric({ label, value, detail }: { label: string; value: string; detail: string }) { return <article className="metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>; }

function NewAnalysis({ onLaunched, onSettings }: { onLaunched: (id: string) => void; onSettings: () => void }) {
  const [config, setConfig] = useState<ProviderResponse | null>(null), [prices, setPrices] = useState<Record<string, { input: number; output: number }>>({ default: { input: .002, output: .008 } }), [error, setError] = useState(""), [launching, setLaunching] = useState(false), [advanced, setAdvanced] = useState(false), [quickCustom, setQuickCustom] = useState(""), [deepCustom, setDeepCustom] = useState("");
  const [form, setForm] = useState<RunPayload>({ ticker: "NVDA", trade_date: new Date().toISOString().slice(0, 10), asset_type: "stock", selected_analysts: analystOptions.map(([id]) => id), llm_provider: "", deep_think_llm: "", quick_think_llm: "", temperature: null, max_debate_rounds: 1, max_risk_discuss_rounds: 1, checkpoint_enabled: true, output_language: "English" });
  useEffect(() => { Promise.all([api.providers(), api.costs(), api.preferences()]).then(([providers, costs, prefs]) => { setConfig(providers); setPrices(costs.prices); const provider = providers.providers.find((p) => p.id === prefs.llm_provider) ?? providers.providers.find((p) => p.id === providers.defaults.llm_provider) ?? providers.providers[0]; if (provider) setForm((old) => ({ ...old, selected_analysts: prefs.selected_analysts, llm_provider: provider.id, quick_think_llm: provider.quick_models.some((m) => m.value === prefs.quick_think_llm) ? prefs.quick_think_llm! : provider.quick_models[0]?.value ?? "", deep_think_llm: provider.deep_models.some((m) => m.value === prefs.deep_think_llm) ? prefs.deep_think_llm! : provider.deep_models[0]?.value ?? "", max_debate_rounds: prefs.max_debate_rounds, max_risk_discuss_rounds: prefs.max_risk_discuss_rounds, checkpoint_enabled: prefs.checkpoint_enabled, output_language: prefs.output_language })); }).catch((e) => setError(e.message)); }, []);
  const provider = config?.providers.find((item) => item.id === form.llm_provider), rate = (model: string) => prices[model] ?? prices.default ?? { input: .002, output: .008 };
  const estimate = useMemo(() => { const quickCalls = form.selected_analysts.length * 2 + 1, deepCalls = form.max_debate_rounds * 2 + form.max_risk_discuss_rounds * 3 + 2, q = rate(form.quick_think_llm), d = rate(form.deep_think_llm); return quickCalls * (5 * q.input + 1.5 * q.output) + deepCalls * (8 * d.input + 2 * d.output); }, [form, prices]);
  const updateProvider = (id: string) => { const next = config?.providers.find((item) => item.id === id); setForm({ ...form, llm_provider: id, quick_think_llm: next?.quick_models[0]?.value ?? "", deep_think_llm: next?.deep_models[0]?.value ?? "" }); };
  const toggleAnalyst = (id: string) => setForm({ ...form, selected_analysts: form.selected_analysts.includes(id) ? form.selected_analysts.filter((item) => item !== id) : [...form.selected_analysts, id] });
  const launch = async () => { setError(""); setLaunching(true); try { const payload = { ...form, quick_think_llm: form.quick_think_llm === "custom" ? quickCustom : form.quick_think_llm, deep_think_llm: form.deep_think_llm === "custom" ? deepCustom : form.deep_think_llm }; const result = await api.createRun(payload); onLaunched(result.run_id); } catch (e) { setError(e instanceof Error ? e.message : "Run could not be launched"); } finally { setLaunching(false); } };
  return <><PageHeader eyebrow="NEW RESEARCH RUN" title="Start a new analysis" copy="Choose the evidence desk and preserve the exact model setup with the result." /><section className="hero-grid"><div className="launch-card">
    <FormHeading step="01" title="Instrument & date" hint="Suffixes: .AX · .L · .HK · .T · .NS · .TO · BTC-USD" /><div className="instrument-row"><label className="ticker-field"><span>Ticker</span><div><input aria-label="Ticker" value={form.ticker} onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })} /><button className="asset-chip" onClick={() => setForm({ ...form, asset_type: form.asset_type === "stock" ? "crypto" : "stock" })}>{form.asset_type}</button></div></label><label><span>Research date</span><input type="date" value={form.trade_date} onChange={(e) => setForm({ ...form, trade_date: e.target.value })} /></label></div>
    <div className="divider" /><FormHeading step="02" title="Build the analyst desk" hint={`${form.selected_analysts.length} selected`} /><div className="analyst-grid">{analystOptions.map(([key, label, copy]) => { const selected = form.selected_analysts.includes(key); return <label className={`analyst-card ${selected ? "selected" : ""}`} key={key}><input type="checkbox" checked={selected} onChange={() => toggleAnalyst(key)} /><span className="check">{selected && "✓"}</span><strong>{label}</strong><small>{copy}</small></label>; })}</div>
    <div className="divider" /><FormHeading step="03" title="Reasoning setup" />{config?.providers.length ? <><div className="config-row"><label><span>Provider</span><select value={form.llm_provider} onChange={(e) => updateProvider(e.target.value)}>{config.providers.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label><label><span>Quick model</span><select value={form.quick_think_llm} onChange={(e) => setForm({ ...form, quick_think_llm: e.target.value })}>{provider?.quick_models.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label><span>Deep model</span><select value={form.deep_think_llm} onChange={(e) => setForm({ ...form, deep_think_llm: e.target.value })}>{provider?.deep_models.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label></div>{(form.quick_think_llm === "custom" || form.deep_think_llm === "custom") && <div className="custom-models">{form.quick_think_llm === "custom" && <label><span>Quick model ID</span><input value={quickCustom} onChange={(e) => setQuickCustom(e.target.value)} placeholder="Exact provider model ID" /></label>}{form.deep_think_llm === "custom" && <label><span>Deep model ID</span><input value={deepCustom} onChange={(e) => setDeepCustom(e.target.value)} placeholder="Exact provider model ID" /></label>}</div>}</> : <div className="connect-provider"><KeyRound size={18} /><div><strong>No model provider is ready</strong><span>Add a server-side key or connect Ollama in settings.</span></div><button onClick={onSettings}>Open settings</button></div>}
    <button className="advanced" onClick={() => setAdvanced(!advanced)}>Advanced configuration <ChevronDown className={advanced ? "rotated" : ""} size={16} /></button>{advanced && <div className="advanced-grid"><Stepper label="Research debate rounds" value={form.max_debate_rounds} onChange={(value) => setForm({ ...form, max_debate_rounds: value })} /><Stepper label="Risk debate rounds" value={form.max_risk_discuss_rounds} onChange={(value) => setForm({ ...form, max_risk_discuss_rounds: value })} /><label><span>Temperature</span><input type="number" min="0" max="2" step="0.1" value={form.temperature ?? ""} placeholder="Provider default" onChange={(e) => setForm({ ...form, temperature: e.target.value === "" ? null : Number(e.target.value) })} /></label><label><span>Output language</span><input value={form.output_language} onChange={(e) => setForm({ ...form, output_language: e.target.value })} /></label><label className="checkpoint-toggle"><input type="checkbox" checked={form.checkpoint_enabled} onChange={(e) => setForm({ ...form, checkpoint_enabled: e.target.checked })} /><span><strong>Checkpoint run</strong><small>Resume after provider failures</small></span></label></div>}
  </div><aside className="estimate-card"><div className="estimate-icon"><CircleDollarSign size={22} /></div><span className="eyebrow">ROUGH RUN ESTIMATE</span><div className="estimate-price"><sup>$</sup>{estimate.toFixed(2)}</div><p>Planning guardrail based on typical input/output sizes. Tool use and provider billing can differ.</p><dl><div><dt>Analyst passes</dt><dd>{form.selected_analysts.length}</dd></div><div><dt>Research debate</dt><dd>{form.max_debate_rounds} round{form.max_debate_rounds !== 1 && "s"}</dd></div><div><dt>Risk viewpoints</dt><dd>{form.max_risk_discuss_rounds * 3}</dd></div><div><dt>Checkpointing</dt><dd className="positive">{form.checkpoint_enabled ? "On" : "Off"}</dd></div></dl>{error && <p className="form-error"><AlertCircle size={13} /> {error}</p>}<button className="launch-button" disabled={launching || !provider || !form.selected_analysts.length || !form.ticker || (form.quick_think_llm === "custom" && !quickCustom.trim()) || (form.deep_think_llm === "custom" && !deepCustom.trim())} onClick={launch}>{launching ? <RefreshCw className="spin" size={17} /> : <Sparkles size={17} />} {launching ? "Starting analysis…" : "Run Analysis"}</button><small>Runs may take several minutes and call paid APIs.</small></aside></section></>;
}
function FormHeading({ step, title, hint }: { step: string; title: string; hint?: string }) { return <div className="section-heading"><div><span className="step">{step}</span><h2>{title}</h2></div>{hint && <span className="hint">{hint}</span>}</div>; }
function Stepper({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) { return <label><span>{label}</span><div className="stepper"><button onClick={() => onChange(Math.max(1, value - 1))}>−</button><strong>{value}</strong><button onClick={() => onChange(Math.min(10, value + 1))}>+</button></div></label>; }

function LiveRun({ runId, onBack }: { runId: string; onBack: () => void }) {
  const [run, setRun] = useState<RunDetail | null>(null), [error, setError] = useState(""), [note, setNote] = useState(""), [tags, setTags] = useState(""), [lastActivity, setLastActivity] = useState<string | null>(null), [activeSectionKey, setActiveSectionKey] = useState<string | null>(null);
  const load = () => api.run(runId).then((value) => { setRun(value); setError(""); const latest = value.events[value.events.length - 1]?.created_at ?? value.started_at; if (latest) setLastActivity((current) => !current || new Date(latest) > new Date(current) ? latest : current); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [runId]);
  useEffect(() => { if (!run || !["queued", "running"].includes(run.status)) return; const timer = window.setInterval(load, 15_000); return () => window.clearInterval(timer); }, [runId, run?.status]);
  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    const socket = new WebSocket(streamUrl(runId));
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      setLastActivity(event.created_at ?? new Date().toISOString());
      if (event.event_type === "section") setRun((current) => current ? { ...current, sections: mergeSection(current.sections, event.payload) } : current);
      if (["completed", "failed"].includes(event.event_type)) load();
    };
    return () => socket.close(1000, "Run view updated");
  }, [runId, run?.status]);
  const ordered = useMemo(() => [...(run?.sections ?? [])].sort((a, b) => sectionOrder.indexOf(a.section_key) - sectionOrder.indexOf(b.section_key)), [run?.sections]);
  const availableSectionKeys = ordered.map((section) => section.section_key).join(":");
  useEffect(() => {
    if (!ordered.length) setActiveSectionKey(null);
    else if (!activeSectionKey || !ordered.some((section) => section.section_key === activeSectionKey)) setActiveSectionKey(ordered[0].section_key);
  }, [runId, availableSectionKeys, activeSectionKey]);
  const saveNote = async () => { if (!note.trim()) return; const saved = await api.note(runId, note, tags.split(",").map((tag) => tag.trim()).filter(Boolean)); setRun((current) => current ? { ...current, notes: [saved, ...current.notes] } : current); setNote(""); setTags(""); };
  if (error) return <EmptyState title="Run unavailable" copy={error} action="Back to history" onAction={onBack} />;
  if (!run) return <div className="loading-page"><RefreshCw className="spin" /><span>Opening research record…</span></div>;
  const activeSection = ordered.find((section) => section.section_key === activeSectionKey) ?? ordered[0], completeCount = new Set(run.sections.map((s) => s.section_key)).size, progress = Math.min(100, Math.round((completeCount / 12) * 100));
  const strategyRevision = run.sections.filter((section) => ["portfolio_manager", "research_manager", "trader"].includes(section.section_key)).map((section) => section.updated_at).join(":");
  return <><div className="run-topline"><button className="back-button" onClick={onBack}><ArrowLeft size={15} /> Run history</button><div className="run-actions"><button onClick={() => api.star(run.id, !run.starred).then(() => setRun({ ...run, starred: !run.starred }))}><Star size={15} fill={run.starred ? "currentColor" : "none"} /> {run.starred ? "Starred" : "Star"}</button>{run.status === "failed" && <button onClick={() => api.resume(run.id).then(load)}><Play size={14} /> Resume</button>}<a href={`/api/runs/${run.id}/report.md`}><Download size={14} /> Markdown</a></div></div>
    <section className="run-hero"><div><span className="eyebrow">{run.asset_type.toUpperCase()} RESEARCH · {formatDate(run.trade_date)}</span><h1>{run.ticker}</h1><p>{run.quick_think_llm} for evidence gathering · {run.deep_think_llm} for synthesis</p></div><div className="decision-panel"><span>Portfolio rating</span><strong className={ratingTone(run.decision?.rating)}>{run.decision?.rating ?? (run.status === "running" ? "Pending" : "—")}</strong><small>{run.decision?.time_horizon ?? "Final synthesis appears after the risk debate"}</small></div></section>
    <section className="run-progress"><div className="progress-head"><Status value={run.status} /><span>{completeCount} of 12 research sections captured</span>{run.status === "running" && <span className="activity-stamp"><span className="pulse-dot" /> Provider call monitored · {lastActivity ? formatClock(lastActivity) : "starting"}</span>}<strong>{run.usage?.tokens_in.toLocaleString() ?? 0} in / {run.usage?.tokens_out.toLocaleString() ?? 0} out</strong><strong>${run.usage?.estimated_cost_usd.toFixed(3) ?? "0.000"} est.</strong></div><div className="progress-track"><span style={{ width: `${run.status === "completed" ? 100 : progress}%` }} /></div></section>
    {run.error_message && <div className="failure-banner"><AlertCircle size={18} /><div><strong>The run stopped before completion</strong><span>{run.error_message}</span></div></div>}
    <StrategyChart runId={run.id} revision={strategyRevision} />
    <div className="report-layout"><section className="report-flow">{ordered.length ? <><div className="section-tabs" role="tablist" aria-label="Research report sections">{ordered.map((section, index) => { const meta = sectionMeta[section.section_key] ?? { label: section.section_key, group: "Research", tone: "teal" }; const selected = section.section_key === activeSection?.section_key; return <button key={section.section_key} className={`section-tab ${selected ? "active" : ""}`} role="tab" aria-selected={selected} onClick={() => setActiveSectionKey(section.section_key)}><span>{String(index + 1).padStart(2, "0")} · {meta.group}</span><strong>{meta.label}</strong></button>; })}</div>{activeSection && <ReportSection section={activeSection} index={ordered.indexOf(activeSection)} />}</> : <div className="waiting-card"><RefreshCw className="spin" /><strong>The analyst desk is gathering evidence</strong><span>Sections will appear here as each agent completes.</span></div>}{run.status === "running" && ordered.length > 0 && <div className="next-section"><span className="pulse-dot" /><span>Waiting for the next completed agent step…</span></div>}</section><aside className="run-aside"><div className="record-card"><span className="eyebrow">REPRODUCIBILITY RECORD</span><dl><div><dt>Provider</dt><dd>{run.llm_provider}</dd></div><div><dt>Deep model</dt><dd>{run.deep_think_llm}</dd></div><div><dt>Quick model</dt><dd>{run.quick_think_llm}</dd></div><div><dt>Temperature</dt><dd>{run.temperature ?? "Default"}</dd></div><div><dt>Debate rounds</dt><dd>{run.max_debate_rounds} + {run.max_risk_discuss_rounds} risk</dd></div><div><dt>Checkpoint</dt><dd>{run.checkpoint_enabled ? "Enabled" : "Disabled"}</dd></div><div><dt>Started</dt><dd>{run.started_at ? formatTimestamp(run.started_at) : "Queued"}</dd></div></dl></div><div className="notes-card"><div className="card-heading"><div><MessageSquarePlus size={16} /><strong>Notebook</strong></div><span>{run.notes.length}</span></div><textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="What do you want to remember about this call?" /><input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Tags, comma separated" /><button onClick={saveNote}>Save note</button>{run.notes.map((item) => <article key={item.id}><p>{item.note_text}</p><div>{item.tags.map((tag) => <span key={tag}>#{tag}</span>)}</div><small>{formatTimestamp(item.created_at)}</small></article>)}</div></aside></div></>;
}

const levelPresentation = {
  entry: { label: "Entry", color: "#2d8b68" },
  take_profit: { label: "Take profit", color: "#3b72c4" },
  stop_loss: { label: "Stop loss", color: "#c4574e" },
} as const;

function StrategyChart({ runId, revision }: { runId: string; revision: string }) {
  const container = useRef<HTMLDivElement | null>(null);
  const [data, setData] = useState<RunChart | null>(null), [error, setError] = useState(""), [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.chart(runId).then((result) => { if (!cancelled) { setData(result); setError(""); } }).catch((reason) => { if (!cancelled) { setData(null); setError(reason instanceof Error ? reason.message : "Chart unavailable"); } }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, revision]);
  useEffect(() => {
    if (!container.current || !data?.candles.length) return;
    const element = container.current;
    const chart = createChart(element, {
      width: element.clientWidth,
      height: 370,
      layout: { background: { type: ColorType.Solid, color: "#faf9f4" }, textColor: "#61706b" },
      grid: { vertLines: { color: "#eef0ea" }, horzLines: { color: "#e6e9e2" } },
      rightPriceScale: { borderColor: "#d8ddd5" },
      timeScale: { borderColor: "#d8ddd5", timeVisible: false, rightOffset: 4 },
      crosshair: { vertLine: { color: "#91a39c" }, horzLine: { color: "#91a39c" } },
    });
    const series = chart.addSeries(CandlestickSeries, { upColor: "#4e9871", downColor: "#c96b62", borderVisible: false, wickUpColor: "#4e9871", wickDownColor: "#c96b62" });
    series.setData(data.candles);
    data.levels.forEach((level) => { const presentation = levelPresentation[level.kind]; series.createPriceLine({ price: level.price, color: presentation.color, lineWidth: 2, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: presentation.label }); });
    chart.timeScale().fitContent();
    const resize = new ResizeObserver(() => chart.applyOptions({ width: element.clientWidth }));
    resize.observe(element);
    return () => { resize.disconnect(); chart.remove(); };
  }, [data]);
  return <section className="strategy-chart-card"><div className="strategy-chart-heading"><div><span className="eyebrow">STRATEGY VISUAL</span><h2>Current price chart & analysis levels</h2><p>{data ? `${data.symbol} · Daily candles through ${formatDate(data.as_of)} · Last close ${data.current_price.toLocaleString(undefined, { maximumFractionDigits: 4 })}` : "Current daily candles with levels stated in the saved analysis."}</p></div><span className="chart-source">TradingView Lightweight Charts · Yahoo Finance data</span></div>{loading ? <div className="chart-state"><RefreshCw className="spin" /> Loading current candles…</div> : error ? <div className="chart-state chart-error"><AlertCircle size={17} /> {error}</div> : <><div ref={container} className="strategy-chart" />{data && <div className="strategy-legend">{data.levels.map((level) => <div key={level.kind}><i style={{ background: levelPresentation[level.kind].color }} /><span>{levelPresentation[level.kind].label}</span><strong>{level.price.toLocaleString(undefined, { maximumFractionDigits: 4 })}</strong><small>{level.source}</small></div>)}{!data.levels.length && <p>No entry, take-profit, or stop-loss price was explicitly stated in this analysis.</p>}</div>}</>}</section>;
}
function mergeSection(sections: RunSection[], payload: Record<string, unknown>) { const section_key = String(payload.section_key), next: RunSection = { section_key, content_md: String(payload.content_md ?? ""), structured_json: (payload.structured_json as Record<string, unknown> | null) ?? null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }; return sections.some((item) => item.section_key === section_key) ? sections.map((item) => item.section_key === section_key ? next : item) : [...sections, next]; }
function ReportSection({ section, index }: { section: RunSection; index: number }) { const meta = sectionMeta[section.section_key] ?? { label: section.section_key, group: "Research", tone: "teal" }; return <article className={`report-section tone-${meta.tone}`}><div className="report-index">{String(index + 1).padStart(2, "0")}</div><div className="report-body"><div className="report-heading"><div><span>{meta.group}</span><h2>{meta.label}</h2></div><CheckCircle2 size={18} /></div><div className="markdown"><ReactMarkdown>{section.content_md}</ReactMarkdown></div></div></article>; }

function CompareRuns({ ids, onOpen, onBack }: { ids: string[]; onOpen: (id: string) => void; onBack: () => void }) {
  const [runs, setRuns] = useState<RunDetail[]>([]), [error, setError] = useState("");
  useEffect(() => { if (ids.length === 2) Promise.all(ids.map(api.run)).then(setRuns).catch((e) => setError(e.message)); }, [ids.join(":")]);
  if (ids.length !== 2) return <EmptyState title="Select two research runs" copy="Use the checkboxes in run history, then bring the full reasoning trails together." action="Choose runs" onAction={onBack} />;
  if (error) return <EmptyState title="Comparison unavailable" copy={error} action="Back to history" onAction={onBack} />;
  if (runs.length !== 2) return <div className="loading-page"><RefreshCw className="spin" /> Building comparison…</div>;
  const keys = sectionOrder.filter((key) => runs.some((run) => run.sections.some((s) => s.section_key === key)));
  return <><PageHeader eyebrow="SIDE-BY-SIDE REVIEW" title={`${runs[0].ticker} / ${runs[1].ticker}`} copy="Compare evidence and assumptions — not just the final label." action={<button className="secondary-button" onClick={onBack}><ArrowLeft size={15} /> History</button>} /><section className="compare-summary">{runs.map((run) => <article key={run.id} onClick={() => onOpen(run.id)}><div><strong>{run.ticker}</strong><span>{formatDate(run.trade_date)}</span></div><span className={`rating large ${ratingTone(run.decision?.rating)}`}>{run.decision?.rating ?? "—"}</span><dl><div><dt>Trader</dt><dd>{run.decision?.action ?? "—"}</dd></div><div><dt>Model</dt><dd>{run.deep_think_llm}</dd></div><div><dt>Temperature</dt><dd>{run.temperature ?? "Default"}</dd></div><div><dt>Estimated cost</dt><dd>${run.usage?.estimated_cost_usd.toFixed(2) ?? "0.00"}</dd></div></dl></article>)}</section><section className="comparison-flow">{keys.map((key) => <div className="comparison-row" key={key}><div className="comparison-label"><span>{sectionMeta[key].group}</span><strong>{sectionMeta[key].label}</strong></div><div className="comparison-docs">{runs.map((run) => { const section = run.sections.find((s) => s.section_key === key); return <article key={run.id}>{section ? <ReactMarkdown>{section.content_md}</ReactMarkdown> : <span className="missing">Not included in this run</span>}</article>; })}</div></div>)}</section></>;
}

function SettingsView() {
  const [secrets, setSecrets] = useState<SecretMask[]>([]), [runtime, setRuntime] = useState<{ data_directory: string } | null>(null), [vendors, setVendors] = useState<Record<string, string>>({}), [editing, setEditing] = useState<string | null>(null), [value, setValue] = useState(""), [message, setMessage] = useState("");
  const load = () => Promise.all([api.secrets(), api.runtime(), api.vendors()]).then(([secretData, runtimeData, vendorData]) => { setSecrets(secretData); setRuntime(runtimeData); setVendors(vendorData.vendors); });
  useEffect(() => { load().catch((e) => setMessage(e.message)); }, []);
  const save = async (name: string) => { try { await api.saveSecret(name, value); setValue(""); setEditing(null); setMessage(`${name} saved securely.`); await load(); } catch (e) { setMessage(e instanceof Error ? e.message : "Could not save key"); } };
  return <><PageHeader eyebrow="LOCAL CONFIGURATION" title="Workspace settings" copy="Credentials are stored server-side and are never returned to this browser." /><div className="settings-grid"><section className="settings-card wide"><div className="settings-title"><div><KeyRound size={18} /><div><h2>Provider credentials</h2><p>Only the last four characters are ever shown after save.</p></div></div><span className="security-chip"><ShieldCheck size={13} /> Encrypted at rest</span></div>{message && <div className="settings-message">{message}</div>}<div className="secret-list">{secrets.map((secret) => <div className="secret-row" key={secret.name}><div className={`connection-dot ${secret.configured ? "connected" : ""}`} /><div><strong>{secret.name}</strong><span>{secret.configured ? secret.masked_value : "Not configured"}</span></div>{editing === secret.name ? <div className="secret-editor"><input autoFocus type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Paste new value" autoComplete="off" /><button onClick={() => save(secret.name)}>Save</button><button onClick={() => { setEditing(null); setValue(""); }}>Cancel</button></div> : <button className="row-action" onClick={() => { setEditing(secret.name); setValue(""); }}>{secret.configured ? "Replace" : "Add"}</button>}</div>)}</div></section><PresetSettings /><section className="settings-card"><div className="settings-title"><div><FileText size={18} /><div><h2>Data directory</h2><p>Self-contained runtime state</p></div></div></div><code>{runtime?.data_directory ?? "Loading…"}</code><ul><li>Run database</li><li>Checkpoint stores</li><li>Memory log</li><li>Markdown report trees</li></ul></section><section className="settings-card"><div className="settings-title"><div><Bot size={18} /><div><h2>Data-vendor chains</h2><p>Inherited from upstream defaults</p></div></div></div><dl className="vendor-list">{Object.entries(vendors).map(([category, chain]) => <div key={category}><dt>{category.replace(/_/g, " ")}</dt><dd>{chain}</dd></div>)}</dl></section></div></>;
}

function PresetSettings() {
  const [prefs, setPrefs] = useState<Preferences | null>(null), [providers, setProviders] = useState<ProviderResponse | null>(null), [saved, setSaved] = useState("");
  useEffect(() => { Promise.all([api.preferences(), api.providers()]).then(([p, catalog]) => { setPrefs(p); setProviders(catalog); }); }, []);
  if (!prefs) return <section className="settings-card wide"><div className="loading-rows"><span /></div></section>;
  const provider = providers?.providers.find((item) => item.id === prefs.llm_provider) ?? providers?.providers[0];
  const quickSelect = provider?.quick_models.some((item) => item.value === prefs.quick_think_llm) ? prefs.quick_think_llm ?? "" : "custom";
  const deepSelect = provider?.deep_models.some((item) => item.value === prefs.deep_think_llm) ? prefs.deep_think_llm ?? "" : "custom";
  const changeProvider = (id: string) => { const next = providers?.providers.find((item) => item.id === id); setPrefs({ ...prefs, llm_provider: id, quick_think_llm: next?.quick_models[0]?.value ?? null, deep_think_llm: next?.deep_models[0]?.value ?? null }); };
  const toggle = (id: string) => setPrefs({ ...prefs, selected_analysts: prefs.selected_analysts.includes(id) ? prefs.selected_analysts.filter((item) => item !== id) : [...prefs.selected_analysts, id] });
  const save = () => api.savePreferences(prefs).then(() => setSaved("Defaults saved."));
  return <section className="settings-card wide"><div className="settings-title"><div><Bot size={18} /><div><h2>New-run defaults</h2><p>Model, analyst and reliability presets for the next analysis.</p></div></div>{saved && <span className="security-chip"><Check size={12} /> {saved}</span>}</div><div className="preset-form"><label><span>Provider</span><select value={provider?.id ?? ""} onChange={(e) => changeProvider(e.target.value)}>{providers?.providers.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label><label><span>Quick model</span><select value={quickSelect} onChange={(e) => setPrefs({ ...prefs, quick_think_llm: e.target.value })}>{provider?.quick_models.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>{quickSelect === "custom" && <input className="preset-custom" value={prefs.quick_think_llm === "custom" ? "" : prefs.quick_think_llm ?? ""} onChange={(e) => setPrefs({ ...prefs, quick_think_llm: e.target.value })} placeholder="Exact model ID" />}</label><label><span>Deep model</span><select value={deepSelect} onChange={(e) => setPrefs({ ...prefs, deep_think_llm: e.target.value })}>{provider?.deep_models.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>{deepSelect === "custom" && <input className="preset-custom" value={prefs.deep_think_llm === "custom" ? "" : prefs.deep_think_llm ?? ""} onChange={(e) => setPrefs({ ...prefs, deep_think_llm: e.target.value })} placeholder="Exact model ID" />}</label><div className="preset-analysts"><span>Default desk</span>{analystOptions.map(([id, label]) => <button className={prefs.selected_analysts.includes(id) ? "selected" : ""} key={id} onClick={() => toggle(id)}><Check size={11} /> {label}</button>)}</div><label><span>Output language</span><input value={prefs.output_language} onChange={(e) => setPrefs({ ...prefs, output_language: e.target.value })} /></label><label className="preset-check"><input type="checkbox" checked={prefs.checkpoint_enabled} onChange={(e) => setPrefs({ ...prefs, checkpoint_enabled: e.target.checked })} /> Checkpoint by default</label><button className="primary-button" disabled={!prefs.selected_analysts.length || !provider || !prefs.quick_think_llm || !prefs.deep_think_llm || prefs.quick_think_llm === "custom" || prefs.deep_think_llm === "custom"} onClick={save}>Save defaults</button></div></section>;
}

function Status({ value }: { value: RunSummary["status"] }) { return <span className={`status status-${value}`}><i /> {value}</span>; }
function OfflineNotice({ message }: { message: string }) { return <div className="offline-notice"><AlertCircle size={16} /><div><strong>Backend is not connected</strong><span>{message}. Start the local service to load real run history.</span></div></div>; }
function LoadingRows() { return <div className="loading-rows">{[1, 2, 3].map((i) => <span key={i} />)}</div>; }
function EmptyState({ title, copy, action, onAction, compact = false }: { title: string; copy: string; action: string; onAction: () => void; compact?: boolean }) { return <div className={`empty-state ${compact ? "compact" : ""}`}><div><FlaskConical size={21} /></div><h2>{title}</h2><p>{copy}</p><button className="primary-button" onClick={onAction}>{action} <ArrowRight size={14} /></button></div>; }
