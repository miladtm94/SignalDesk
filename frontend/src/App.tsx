import { useEffect, useMemo, useState } from "react";
import {
  Activity, AlertCircle, ArrowLeft, ArrowRight, BarChart3, BookOpen, Bot,
  Check, CheckCircle2, ChevronDown, ChartLine, CircleDollarSign, CircleStop, Download, FileText,
  FlaskConical, History, KeyRound, Menu, MessageSquarePlus, Play, RefreshCw,
  Search, Settings, ShieldCheck, Sparkles, Star, Terminal, Trash2, Wallet, X,
} from "lucide-react";
import { api, DataOutcome, Preferences, ProviderResponse, RunDetail, RunPayload, RunSection, RunSummary, SecretMask, Subscription, streamUrl, Usage } from "./api";
import { BacktestsView, BacktestView, NewBacktest } from "./Backtests";
import { BookCard, HoldingsView, PortfolioPicker } from "./Holdings";
import { Report } from "./Report";
import { StrategyChart } from "./StrategyChart";
import { estimateRunCost, loadModelDefaults, ModelSetup, Prices } from "./ModelSetup";
import {
  analystOptions, EmptyState, formatClock, formatDate, formatTimestamp, FormHeading, isPlanBilled, LanguageField, LoadingRows, localISODate, parseTimestamp,
  Metric, OfflineNotice, PageHeader, providerName, ratingTone, reasoningValue, selectableModels, sharedReasoningLevels, Status, Stepper, withReasoning,
} from "./shared";

type View = "dashboard" | "new" | "run" | "compare" | "settings" | "backtests" | "new-backtest" | "backtest" | "holdings";
// What to prefill when another view opens the analysis form.
type LaunchIntent = { ticker: string; usePortfolio: boolean } | null;
const sectionMeta: Record<string, { label: string; group: string; tone: string; icon: string }> = {
  portfolio_manager: { label: "Portfolio manager", group: "Final decision", tone: "lime", icon: "🏛️" },
  research_manager: { label: "Research manager", group: "Research decision", tone: "teal", icon: "🧑‍⚖️" },
  trader: { label: "Trader proposal", group: "Trading plan", tone: "blue", icon: "💼" },
  risk_aggressive: { label: "Aggressive risk", group: "Risk debate", tone: "red", icon: "🔥" },
  risk_conservative: { label: "Conservative risk", group: "Risk debate", tone: "amber", icon: "🛡️" },
  risk_neutral: { label: "Neutral risk", group: "Risk debate", tone: "violet", icon: "⚖️" },
  market_report: { label: "Market analyst", group: "Analyst desk", tone: "teal", icon: "📈" },
  sentiment_report: { label: "Sentiment analyst", group: "Analyst desk", tone: "violet", icon: "💬" },
  news_report: { label: "News & macro analyst", group: "Analyst desk", tone: "blue", icon: "📰" },
  fundamentals_report: { label: "Fundamentals analyst", group: "Analyst desk", tone: "amber", icon: "🧾" },
  bull: { label: "Bull researcher", group: "Research debate", tone: "lime", icon: "🐂" },
  bear: { label: "Bear researcher", group: "Research debate", tone: "red", icon: "🐻" },
};
const sectionOrder = Object.keys(sectionMeta);






export default function App() {
  const [view, setView] = useState<View>("dashboard");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeBacktestId, setActiveBacktestId] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [intent, setIntent] = useState<LaunchIntent>(null);
  const navigate = (next: View) => { setView(next); setMenuOpen(false); window.scrollTo(0, 0); };
  const openRun = (id: string) => { setActiveRunId(id); navigate("run"); };
  const openBacktest = (id: string) => { setActiveBacktestId(id); navigate("backtest"); };
  const analyzeWithBook = (ticker: string) => { setIntent({ ticker, usePortfolio: true }); navigate("new"); };

  return <div className="app-shell">
    <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
      <div className="brand"><div className="brand-mark">S</div><div><strong>Signal Desk</strong><span>Research console</span></div><button className="close-menu" onClick={() => setMenuOpen(false)}><X size={18} /></button></div>
      <nav>
        <button className={view === "dashboard" ? "active" : ""} onClick={() => navigate("dashboard")}><BarChart3 size={18} /> Overview</button>
        <button className={view === "new" ? "active" : ""} onClick={() => { setIntent(null); navigate("new"); }}><FlaskConical size={18} /> New analysis</button>
        <button className={["backtests", "new-backtest", "backtest"].includes(view) ? "active" : ""} onClick={() => navigate("backtests")}><ChartLine size={18} /> Backtests</button>
        <button className={view === "holdings" ? "active" : ""} onClick={() => navigate("holdings")}><Wallet size={18} /> Holdings</button>
        <button className={view === "run" ? "active" : ""} onClick={() => activeRunId ? navigate("run") : navigate("dashboard")}><History size={18} /> Run detail</button>
        <button className={view === "compare" ? "active" : ""} onClick={() => navigate("compare")}><BookOpen size={18} /> Compare notes</button>
      </nav>
      <div className="sidebar-spacer" /><div className="local-card"><ShieldCheck size={17} /><div><strong>Local workspace</strong><span>Keys stay on this machine</span></div></div>
      <button className={`settings-link ${view === "settings" ? "active" : ""}`} onClick={() => navigate("settings")}><Settings size={18} /> Settings</button>
    </aside>
    <button className="mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open navigation"><Menu size={20} /></button>
    <main>
      {view === "dashboard" && <Dashboard onNew={() => navigate("new")} onOpen={openRun} compareIds={compareIds} setCompareIds={setCompareIds} onCompare={() => navigate("compare")} />}
      {view === "new" && <NewAnalysis key={intent ? `${intent.ticker}:${intent.usePortfolio}` : "blank"} intent={intent} onLaunched={openRun} onSettings={() => navigate("settings")} onHoldings={() => navigate("holdings")} />}
      {view === "backtests" && <BacktestsView onNew={() => navigate("new-backtest")} onOpen={openBacktest} />}
      {view === "new-backtest" && <NewBacktest onLaunched={openBacktest} onSettings={() => navigate("settings")} onHoldings={() => navigate("holdings")} />}
      {view === "backtest" && activeBacktestId && <BacktestView backtestId={activeBacktestId} onBack={() => navigate("backtests")} onOpenRun={openRun} />}
      {view === "holdings" && <HoldingsView onAnalyze={analyzeWithBook} />}
      {view === "run" && activeRunId && <LiveRun runId={activeRunId} onBack={() => navigate("dashboard")} onOpenBacktest={openBacktest} />}
      {view === "run" && !activeRunId && <EmptyState title="Choose a run from history" copy="Open a past run to read its complete research trail." action="Browse history" onAction={() => navigate("dashboard")} />}
      {view === "compare" && <CompareRuns ids={compareIds} onOpen={openRun} onBack={() => navigate("dashboard")} />}
      {view === "settings" && <SettingsView />}
    </main>
    <div className="disclaimer"><Activity size={15} /><span><strong>Research workspace, not financial advice.</strong> Ratings, actions and price targets are model-generated hypotheses. Verify independently before making any decision.</span></div>
    {menuOpen && <button className="menu-scrim" onClick={() => setMenuOpen(false)} aria-label="Close navigation" />}
  </div>;
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
      {error && <OfflineNotice message={error} />}{loading ? <LoadingRows /> : filtered.length ? <div className="run-table-wrap"><table className="run-table"><thead><tr><th></th><th>Ticker</th><th>Research date</th><th>Final rating</th><th>Trader action</th><th>Model record</th><th>Usage</th><th>Status</th><th>Actions</th></tr></thead><tbody>{filtered.map((run) => <tr key={run.id}><td><label className="compare-check"><input type="checkbox" checked={compareIds.includes(run.id)} onChange={() => toggleCompare(run.id)} /><span>{compareIds.includes(run.id) && <Check size={11} />}</span></label></td><td><button className="ticker-link" onClick={() => onOpen(run.id)}>{run.ticker}</button>{run.starred && <Star className="starred" size={11} fill="currentColor" />}{run.used_portfolio && <small className="book-tag">Against my book</small>}</td><td>{formatDate(run.trade_date)}</td><td><span className={`rating ${ratingTone(run.decision?.rating)}`}>{run.decision?.rating ?? "—"}</span></td><td>{run.decision?.action ?? "—"}</td><td><strong>{run.deep_think_llm}</strong><small>{providerName(run.llm_provider)} · T {run.temperature ?? "default"}</small></td><td><strong>{((run.usage?.tokens_in ?? 0) + (run.usage?.tokens_out ?? 0)).toLocaleString()} tok</strong><small>{isPlanBilled(run.llm_provider) ? "Billed to plan" : `$${run.usage?.estimated_cost_usd.toFixed(2) ?? "0.00"} est.`}</small></td><td><Status value={run.status} /></td><td><button className="remove-run" disabled={run.status === "running" || removingId === run.id} onClick={() => removeRun(run)} aria-label={`Remove ${run.ticker} analysis`} title={run.status === "running" ? "Wait for the running analysis to finish before removing it" : "Remove analysis"}>{removingId === run.id ? <RefreshCw className="spin" size={14} /> : <Trash2 size={14} />}<span>{removingId === run.id ? "Removing…" : "Remove"}</span></button></td></tr>)}</tbody></table></div> : <EmptyState title="No research runs yet" copy="Start with a ticker you know and keep the complete reasoning trail." action="Run first analysis" onAction={onNew} compact />}</section></>;
}

function NewAnalysis({ intent, onLaunched, onSettings, onHoldings }: { intent: LaunchIntent; onLaunched: (id: string) => void; onSettings: () => void; onHoldings: () => void }) {
  const [config, setConfig] = useState<ProviderResponse | null>(null), [prices, setPrices] = useState<Prices>({ default: { input: .002, output: .008 } }), [error, setError] = useState(""), [launching, setLaunching] = useState(false), [advanced, setAdvanced] = useState(false), [quickCustom, setQuickCustom] = useState(""), [deepCustom, setDeepCustom] = useState(""), [savedModels, setSavedModels] = useState<Record<string, string[]>>({});
  const [form, setForm] = useState<RunPayload>({ ticker: intent?.ticker || "NVDA", trade_date: localISODate(), asset_type: "stock", selected_analysts: analystOptions.map(([id]) => id), llm_provider: "", deep_think_llm: "", quick_think_llm: "", temperature: null, max_debate_rounds: 1, max_risk_discuss_rounds: 1, checkpoint_enabled: true, output_language: "English", google_thinking_level: null, openai_reasoning_effort: null, anthropic_effort: null, use_portfolio: intent?.usePortfolio ?? false });
  useEffect(() => { loadModelDefaults().then((defaults) => { setConfig(defaults.config); setPrices(defaults.prices); setSavedModels(defaults.savedModels); const { prefs, fields } = defaults; if (!fields) return; setForm((old) => ({ ...old, ...fields, selected_analysts: prefs.selected_analysts, max_debate_rounds: prefs.max_debate_rounds, max_risk_discuss_rounds: prefs.max_risk_discuss_rounds, checkpoint_enabled: prefs.checkpoint_enabled, output_language: prefs.output_language })); }).catch((e) => setError(e.message)); }, []);
  const provider = config?.providers.find((item) => item.id === form.llm_provider), planBilled = isPlanBilled(form.llm_provider);
  const estimate = useMemo(() => estimateRunCost(prices, form, form.selected_analysts.length, form.max_debate_rounds, form.max_risk_discuss_rounds), [form, prices]);
  const toggleAnalyst = (id: string) => setForm({ ...form, selected_analysts: form.selected_analysts.includes(id) ? form.selected_analysts.filter((item) => item !== id) : [...form.selected_analysts, id] });
  const launch = async () => { setError(""); setLaunching(true); try { const payload = { ...form, quick_think_llm: form.quick_think_llm === "custom" ? quickCustom : form.quick_think_llm, deep_think_llm: form.deep_think_llm === "custom" ? deepCustom : form.deep_think_llm, temperature: planBilled ? null : form.temperature }; const result = await api.createRun(payload); onLaunched(result.run_id); } catch (e) { setError(e instanceof Error ? e.message : "Run could not be launched"); } finally { setLaunching(false); } };
  return <><PageHeader eyebrow="NEW RESEARCH RUN" title="Start a new analysis" copy="Choose the evidence desk and preserve the exact model setup with the result." /><section className="hero-grid"><div className="launch-card">
    <FormHeading step="01" title="Instrument & date" hint="Suffixes: .AX · .L · .HK · .T · .NS · .TO · BTC-USD" /><div className="instrument-row"><label className="ticker-field"><span>Ticker</span><div><input aria-label="Ticker" value={form.ticker} onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })} /><button className="asset-chip" onClick={() => setForm({ ...form, asset_type: form.asset_type === "stock" ? "crypto" : "stock" })}>{form.asset_type}</button></div></label><label><span>Research date</span><input type="date" value={form.trade_date} onChange={(e) => setForm({ ...form, trade_date: e.target.value })} /></label></div>
    <div className="divider" /><FormHeading step="02" title="Build the analyst desk" hint={`${form.selected_analysts.length} selected`} /><div className="analyst-grid">{analystOptions.map(([key, label, copy]) => { const selected = form.selected_analysts.includes(key); return <label className={`analyst-card ${selected ? "selected" : ""}`} key={key}><input type="checkbox" checked={selected} onChange={() => toggleAnalyst(key)} /><span className="check">{selected && "✓"}</span><strong>{label}</strong><small>{copy}</small></label>; })}</div>
    <div className="divider" /><FormHeading step="03" title="Reasoning setup" /><ModelSetup config={config} value={form} savedModels={savedModels} quickCustom={quickCustom} deepCustom={deepCustom} onQuickCustom={setQuickCustom} onDeepCustom={setDeepCustom} onChange={(fields) => setForm((old) => ({ ...old, ...fields }))} onSettings={onSettings} />
    <div className="divider" /><FormHeading step="04" title="Portfolio context" hint="Optional · trader, risk and portfolio manager" /><PortfolioPicker ticker={form.ticker} value={form.use_portfolio} onChange={(value) => setForm((old) => ({ ...old, use_portfolio: value }))} onHoldings={onHoldings} />
    <button className="advanced" onClick={() => setAdvanced(!advanced)}>Advanced configuration <ChevronDown className={advanced ? "rotated" : ""} size={16} /></button>{advanced && <div className="advanced-grid"><Stepper label="Research debate rounds" value={form.max_debate_rounds} onChange={(value) => setForm({ ...form, max_debate_rounds: value })} /><Stepper label="Risk debate rounds" value={form.max_risk_discuss_rounds} onChange={(value) => setForm({ ...form, max_risk_discuss_rounds: value })} /><label><span>Temperature</span><input type="number" min="0" max="2" step="0.1" disabled={planBilled} value={planBilled ? "" : form.temperature ?? ""} placeholder={planBilled ? "Not used on a plan" : "Provider default"} onChange={(e) => setForm({ ...form, temperature: e.target.value === "" ? null : Number(e.target.value) })} /></label><LanguageField value={form.output_language} options={config?.output_languages ?? []} onChange={(value) => setForm({ ...form, output_language: value })} /><label className="checkpoint-toggle"><input type="checkbox" checked={form.checkpoint_enabled} onChange={(e) => setForm({ ...form, checkpoint_enabled: e.target.checked })} /><span><strong>Checkpoint run</strong><small>Resume after provider failures, plan limits or a stop</small></span></label></div>}
  </div><aside className="estimate-card"><div className="estimate-icon"><CircleDollarSign size={22} /></div><span className="eyebrow">{planBilled ? "PLAN USAGE" : "ROUGH RUN ESTIMATE"}</span><div className="estimate-price">{planBilled ? <>Plan <small className="estimate-unit">billed</small></> : <><sup>$</sup>{estimate.toFixed(2)}</>}</div><p>{planBilled ? `No per-token charge: this run counts toward your ${providerName(form.llm_provider)}'s usage limits. If a limit is reached, the checkpointed run resumes after it resets.` : "Planning guardrail based on typical input/output sizes. Tool use and provider billing can differ."}</p><dl><div><dt>Analyst passes</dt><dd>{form.selected_analysts.length}</dd></div><div><dt>Research debate</dt><dd>{form.max_debate_rounds} round{form.max_debate_rounds !== 1 && "s"}</dd></div><div><dt>Risk viewpoints</dt><dd>{form.max_risk_discuss_rounds * 3}</dd></div><div><dt>Portfolio</dt><dd className={form.use_portfolio ? "positive" : ""}>{form.use_portfolio ? "Against my book" : "None"}</dd></div><div><dt>Checkpointing</dt><dd className="positive">{form.checkpoint_enabled ? "On" : "Off"}</dd></div></dl>{error && <p className="form-error"><AlertCircle size={13} /> {error}</p>}<button className="launch-button" disabled={launching || !provider?.configured || !form.selected_analysts.length || !form.ticker || form.output_language.trim().length < 2 || (form.quick_think_llm === "custom" && !quickCustom.trim()) || (form.deep_think_llm === "custom" && !deepCustom.trim())} onClick={launch}>{launching ? <RefreshCw className="spin" size={17} /> : <Sparkles size={17} />} {launching ? "Starting analysis…" : "Run Analysis"}</button><small>{planBilled ? "Runs may take several minutes and count toward your plan's usage limits." : "Runs may take several minutes and call paid APIs."}</small></aside></section></>;
}

function LiveRun({ runId, onBack, onOpenBacktest }: { runId: string; onBack: () => void; onOpenBacktest: (id: string) => void }) {
  const [run, setRun] = useState<RunDetail | null>(null), [error, setError] = useState(""), [note, setNote] = useState(""), [tags, setTags] = useState(""), [lastActivity, setLastActivity] = useState<string | null>(null), [activeSectionKey, setActiveSectionKey] = useState<string | null>(null), [stopping, setStopping] = useState(false), [actionError, setActionError] = useState("");
  const load = () => api.run(runId).then((value) => { setRun(value); setError(""); const latest = value.events[value.events.length - 1]?.created_at ?? value.started_at; if (latest) setLastActivity((current) => !current || parseTimestamp(latest) > parseTimestamp(current) ? latest : current); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [runId]);
  useEffect(() => { if (!run || !["queued", "running"].includes(run.status)) return; const timer = window.setInterval(load, 15_000); return () => window.clearInterval(timer); }, [runId, run?.status]);
  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    const socket = new WebSocket(streamUrl(runId));
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      setLastActivity(event.created_at ?? new Date().toISOString());
      if (event.event_type === "section") setRun((current) => current ? { ...current, sections: mergeSection(current.sections, event.payload) } : current);
      if (["completed", "failed", "stopped"].includes(event.event_type)) load();
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
  const activeSection = ordered.find((section) => section.section_key === activeSectionKey) ?? ordered[0], completeCount = new Set(run.sections.map((s) => s.section_key)).size, expectedSections = run.selected_analysts.length + 8, progress = Math.min(100, Math.round((completeCount / expectedSections) * 100));
  const strategyRevision = run.sections.filter((section) => ["portfolio_manager", "research_manager", "trader"].includes(section.section_key)).map((section) => section.updated_at).join(":");
  const reasoningSetting = run.llm_provider === "google" ? run.config_snapshot.google_thinking_level : ["openai", "chatgpt"].includes(run.llm_provider) ? run.config_snapshot.openai_reasoning_effort : ["anthropic", "claude"].includes(run.llm_provider) ? run.config_snapshot.anthropic_effort : null;
  const planBilled = isPlanBilled(run.llm_provider), active = ["queued", "running"].includes(run.status), resumable = ["failed", "stopped"].includes(run.status) && !run.backtest_id;
  const stop = async () => { setStopping(true); setActionError(""); try { await api.stopRun(run.id); await load(); } catch (e) { setActionError(e instanceof Error ? e.message : "The run could not be stopped"); } finally { setStopping(false); } };
  const resume = async () => { setActionError(""); try { await api.resume(run.id); await load(); } catch (e) { setActionError(e instanceof Error ? e.message : "The run could not be resumed"); } };
  return <><div className="run-topline"><button className="back-button" onClick={onBack}><ArrowLeft size={15} /> Run history</button><div className="run-actions"><button onClick={() => api.star(run.id, !run.starred).then(() => setRun({ ...run, starred: !run.starred }))}><Star size={15} fill={run.starred ? "currentColor" : "none"} /> {run.starred ? "Starred" : "Star"}</button>{active && !run.backtest_id && <button onClick={stop} disabled={stopping}><CircleStop size={14} /> {stopping ? "Stopping…" : "Stop"}</button>}{resumable && <button onClick={resume}><Play size={14} /> Resume</button>}{run.backtest_id && <button onClick={() => onOpenBacktest(run.backtest_id!)}><ChartLine size={14} /> Backtest</button>}<a href={`/api/runs/${run.id}/report.md`}><Download size={14} /> Markdown</a></div></div>
    <section className="run-hero"><div><span className="eyebrow">{run.asset_type.toUpperCase()} RESEARCH · {formatDate(run.trade_date)}</span><h1>{run.ticker}</h1><p>{run.quick_think_llm} for evidence gathering · {run.deep_think_llm} for synthesis</p></div><div className="decision-panel"><span>Portfolio rating</span><strong className={ratingTone(run.decision?.rating)}>{run.decision?.rating ?? (run.status === "running" ? "Pending" : "—")}</strong><small>{run.decision?.time_horizon ?? "Final synthesis appears after the risk debate"}</small></div></section>
    <section className="run-progress"><div className="progress-head"><Status value={run.status} /><span>{completeCount} of {expectedSections} research sections captured</span>{run.status === "running" && <span className="activity-stamp"><span className="pulse-dot" /> Provider call monitored · {lastActivity ? formatClock(lastActivity) : "starting"}</span>}<strong>{run.usage?.tokens_in.toLocaleString() ?? 0} in / {run.usage?.tokens_out.toLocaleString() ?? 0} out</strong><strong>{planBilled ? `Billed to plan${run.usage?.api_equivalent_usd ? ` · ≈ $${run.usage.api_equivalent_usd.toFixed(3)} at API prices` : ""}` : `$${run.usage?.estimated_cost_usd.toFixed(3) ?? "0.000"} est.`}</strong></div><div className="progress-track"><span style={{ width: `${run.status === "completed" ? 100 : progress}%` }} /></div></section>
    {run.backtest_id && <div className="cell-banner"><ChartLine size={16} /><div><strong>One cell of a backtest</strong><span>Decided as of {formatDate(run.trade_date)} against the backtest's own memory log, without checkpoints. Stop, resume and scores live on the backtest.</span></div><button className="link-button" onClick={() => onOpenBacktest(run.backtest_id!)}>Open backtest <ArrowRight size={12} /></button></div>}
    {(run.error_message || actionError) && <div className="failure-banner"><AlertCircle size={18} /><div><strong>{actionError ? "That did not work" : run.status === "stopped" ? "The run was stopped" : "The run stopped before completion"}</strong><span>{actionError || run.error_message}</span></div></div>}
    <StrategyChart runId={run.id} revision={strategyRevision} />
    <div className="report-layout"><section className="report-flow">{ordered.length ? <><div className="section-tabs" role="tablist" aria-label="Research report sections">{ordered.map((section, index) => { const meta = sectionMeta[section.section_key] ?? { label: section.section_key, group: "Research", tone: "teal", icon: "📄" }; const selected = section.section_key === activeSection?.section_key; return <button key={section.section_key} className={`section-tab ${selected ? "active" : ""}`} role="tab" aria-selected={selected} onClick={() => setActiveSectionKey(section.section_key)}><span>{String(index + 1).padStart(2, "0")} · {meta.group}</span><strong><span className="section-icon" aria-hidden="true">{meta.icon}</span>{meta.label}</strong></button>; })}</div>{activeSection && <ReportSection section={activeSection} index={ordered.indexOf(activeSection)} />}</> : <div className="waiting-card"><RefreshCw className="spin" /><strong>The analyst desk is gathering evidence</strong><span>Sections will appear here as each agent completes.</span></div>}{run.status === "running" && ordered.length > 0 && <div className="next-section"><span className="pulse-dot" /><span>Waiting for the next completed agent step…</span></div>}</section><aside className="run-aside"><div className="record-card"><span className="eyebrow">REPRODUCIBILITY RECORD</span><dl><div><dt>Provider</dt><dd>{providerName(run.llm_provider)}</dd></div><div><dt>Deep model</dt><dd>{run.deep_think_llm}</dd></div><div><dt>Quick model</dt><dd>{run.quick_think_llm}</dd></div><div><dt>Reasoning / thinking</dt><dd>{reasoningSetting ? String(reasoningSetting) : "Provider default"}</dd></div><div><dt>Temperature</dt><dd>{run.temperature ?? "Default"}</dd></div><div><dt>Debate rounds</dt><dd>{run.max_debate_rounds} + {run.max_risk_discuss_rounds} risk</dd></div><div><dt>Checkpoint</dt><dd>{run.checkpoint_enabled ? "Enabled" : "Disabled"}</dd></div><div><dt>Portfolio</dt><dd>{run.used_portfolio ? "Decided against your book" : "No portfolio context"}</dd></div><div><dt>Started</dt><dd>{run.started_at ? formatTimestamp(run.started_at) : "Queued"}</dd></div></dl></div>{run.usage?.data_coverage && <DataCoverageCard coverage={run.usage.data_coverage} />}{run.used_portfolio && <BookCard context={run.portfolio_context} />}<div className="notes-card"><div className="card-heading"><div><MessageSquarePlus size={16} /><strong>Notebook</strong></div><span>{run.notes.length}</span></div><textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="What do you want to remember about this call?" /><input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Tags, comma separated" /><button onClick={saveNote}>Save note</button>{run.notes.map((item) => <article key={item.id}><p>{item.note_text}</p><div>{item.tags.map((tag) => <span key={tag}>#{tag}</span>)}</div><small>{formatTimestamp(item.created_at)}</small></article>)}</div></aside></div></>;
}

// The data tools grouped as a reader thinks of them, in report order.
const coverageGroups: [string, string[]][] = [
  ["Prices & technicals", ["get_stock_data", "get_indicators", "get_verified_market_snapshot"]],
  ["Weekly & monthly structure", ["get_price_structure"]],
  ["Company news", ["get_news"]],
  ["Market news", ["get_global_news"]],
  ["Financial statements", ["get_income_statement", "get_balance_sheet", "get_cashflow"]],
  ["Company profile", ["get_fundamentals"]],
  ["Analyst coverage", ["get_analyst_coverage"]],
  ["Insider trades", ["get_insider_transactions"]],
  ["Macro data", ["get_macro_indicators"]],
  ["Prediction markets", ["get_prediction_markets"]],
];
const coverageVerdict = (counts: Record<DataOutcome, number>) =>
  counts.ok ? (counts.unavailable + counts.error ? ["partial", "Partly served"] : ["ok", "Served"])
    : counts.withheld ? ["withheld", "Withheld for a past date"]
      : counts.empty ? ["empty", "None found"] : ["missing", "Unavailable"];

// Which data the run actually read, so a decision made without its news shows it.
function DataCoverageCard({ coverage }: { coverage: NonNullable<Usage["data_coverage"]> }) {
  const rows = coverageGroups.flatMap(([label, tools]) => {
    const used = tools.filter((tool) => coverage[tool]);
    if (!used.length) return [];
    const counts = used.reduce((sum, tool) => { (Object.keys(sum) as DataOutcome[]).forEach((key) => { sum[key] += coverage[tool][key] ?? 0; }); return sum; }, { ok: 0, empty: 0, withheld: 0, unavailable: 0, error: 0 } as Record<DataOutcome, number>);
    const calls = Object.values(counts).reduce((a, b) => a + b, 0);
    return [{ label, calls, verdict: coverageVerdict(counts) }];
  });
  if (!rows.length) return null;
  const missing = rows.filter((row) => row.verdict[0] === "missing" || row.verdict[0] === "partial");
  return <div className="record-card coverage-card"><span className="eyebrow">DATA COVERAGE</span>{missing.length > 0 && <p className="coverage-warning"><AlertCircle size={13} /> Decided without full {missing.map((row) => row.label.toLowerCase()).join(", ")}. Weigh the call accordingly.</p>}<dl>{rows.map((row) => <div key={row.label}><dt>{row.label}</dt><dd className={`coverage-${row.verdict[0]}`}>{row.verdict[1]} <small>· {row.calls} call{row.calls === 1 ? "" : "s"}</small></dd></div>)}</dl><small className="coverage-foot">The sentiment analyst reads news, StockTwits and Reddit itself; its report says what it found.</small></div>;
}
function mergeSection(sections: RunSection[], payload: Record<string, unknown>) { const section_key = String(payload.section_key), next: RunSection = { section_key, content_md: String(payload.content_md ?? ""), structured_json: (payload.structured_json as Record<string, unknown> | null) ?? null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }; return sections.some((item) => item.section_key === section_key) ? sections.map((item) => item.section_key === section_key ? next : item) : [...sections, next]; }
function ReportSection({ section, index }: { section: RunSection; index: number }) { const meta = sectionMeta[section.section_key] ?? { label: section.section_key, group: "Research", tone: "teal", icon: "📄" }; return <article className={`report-section tone-${meta.tone}`}><div className="report-index">{String(index + 1).padStart(2, "0")}</div><div className="report-body"><div className="report-heading"><div><span>{meta.group}</span><h2><span className="section-icon" aria-hidden="true">{meta.icon}</span>{meta.label}</h2></div><CheckCircle2 size={18} /></div><Report markdown={section.content_md} /></div></article>; }

function CompareRuns({ ids, onOpen, onBack }: { ids: string[]; onOpen: (id: string) => void; onBack: () => void }) {
  const [runs, setRuns] = useState<RunDetail[]>([]), [error, setError] = useState("");
  useEffect(() => { if (ids.length === 2) Promise.all(ids.map(api.run)).then(setRuns).catch((e) => setError(e.message)); }, [ids.join(":")]);
  if (ids.length !== 2) return <EmptyState title="Select two research runs" copy="Use the checkboxes in run history, then bring the full reasoning trails together." action="Choose runs" onAction={onBack} />;
  if (error) return <EmptyState title="Comparison unavailable" copy={error} action="Back to history" onAction={onBack} />;
  if (runs.length !== 2) return <div className="loading-page"><RefreshCw className="spin" /> Building comparison…</div>;
  const keys = sectionOrder.filter((key) => runs.some((run) => run.sections.some((s) => s.section_key === key)));
  return <><PageHeader eyebrow="SIDE-BY-SIDE REVIEW" title={`${runs[0].ticker} / ${runs[1].ticker}`} copy="Compare evidence and assumptions — not just the final label." action={<button className="secondary-button" onClick={onBack}><ArrowLeft size={15} /> History</button>} /><section className="compare-summary">{runs.map((run) => <article key={run.id} onClick={() => onOpen(run.id)}><div><strong>{run.ticker}</strong><span>{formatDate(run.trade_date)}</span></div><span className={`rating large ${ratingTone(run.decision?.rating)}`}>{run.decision?.rating ?? "—"}</span><dl><div><dt>Trader</dt><dd>{run.decision?.action ?? "—"}</dd></div><div><dt>Model</dt><dd>{run.deep_think_llm}</dd></div><div><dt>Temperature</dt><dd>{run.temperature ?? "Default"}</dd></div><div><dt>Estimated cost</dt><dd>${run.usage?.estimated_cost_usd.toFixed(2) ?? "0.00"}</dd></div></dl></article>)}</section><section className="comparison-flow">{keys.map((key) => <div className="comparison-row" key={key}><div className="comparison-label"><span>{sectionMeta[key].group}</span><strong><span className="section-icon" aria-hidden="true">{sectionMeta[key].icon}</span>{sectionMeta[key].label}</strong></div><div className="comparison-docs">{runs.map((run) => { const section = run.sections.find((s) => s.section_key === key); return <article key={run.id}>{section ? <Report markdown={section.content_md} /> : <span className="missing">Not included in this run</span>}</article>; })}</div></div>)}</section></>;
}

function SettingsView() {
  const [secrets, setSecrets] = useState<SecretMask[]>([]), [runtime, setRuntime] = useState<{ data_directory: string } | null>(null), [vendors, setVendors] = useState<Record<string, string>>({}), [editing, setEditing] = useState<string | null>(null), [value, setValue] = useState(""), [message, setMessage] = useState(""), [providerRevision, setProviderRevision] = useState(0);
  const load = () => Promise.all([api.secrets(), api.runtime(), api.vendors()]).then(([secretData, runtimeData, vendorData]) => { setSecrets(secretData); setRuntime(runtimeData); setVendors(vendorData.vendors); });
  useEffect(() => { load().catch((e) => setMessage(e.message)); }, []);
  const save = async (name: string) => { try { await api.saveSecret(name, value); setValue(""); setEditing(null); setMessage(`${name} saved securely.`); await load(); setProviderRevision((current) => current + 1); } catch (e) { setMessage(e instanceof Error ? e.message : "Could not save key"); } };
  return <><PageHeader eyebrow="LOCAL CONFIGURATION" title="Workspace settings" copy="Store several provider credentials, then choose exactly one active provider for new analyses." /><div className="settings-grid"><PlanSettings revision={providerRevision} onChange={() => setProviderRevision((current) => current + 1)} /><section className="settings-card wide"><div className="settings-title"><div><KeyRound size={18} /><div><h2>Provider credentials</h2><p>Multiple keys can stay configured. Only the active provider below is used.</p></div></div><span className="security-chip"><ShieldCheck size={13} /> Encrypted at rest</span></div>{message && <div className="settings-message">{message}</div>}<div className="secret-list">{secrets.map((secret) => <div className="secret-row" key={secret.name}><div className={`connection-dot ${secret.configured ? "connected" : ""}`} /><div><strong>{secret.name}</strong><span>{secret.configured ? secret.masked_value : "Not configured"}</span></div>{editing === secret.name ? <div className="secret-editor"><input autoFocus type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Paste new value" autoComplete="off" /><button onClick={() => save(secret.name)}>Save</button><button onClick={() => { setEditing(null); setValue(""); }}>Cancel</button></div> : <button className="row-action" onClick={() => { setEditing(secret.name); setValue(""); }}>{secret.configured ? "Replace" : "Add"}</button>}</div>)}</div></section><PresetSettings revision={providerRevision} /><section className="settings-card"><div className="settings-title"><div><FileText size={18} /><div><h2>Data directory</h2><p>Self-contained runtime state</p></div></div></div><code>{runtime?.data_directory ?? "Loading…"}</code><ul><li>Run database</li><li>Checkpoint stores</li><li>Memory log</li><li>Markdown report trees</li></ul></section><section className="settings-card"><div className="settings-title"><div><Bot size={18} /><div><h2>Data-vendor chains</h2><p>Inherited from upstream defaults</p></div></div></div><dl className="vendor-list">{Object.entries(vendors).map(([category, chain]) => <div key={category}><dt>{category.replace(/_/g, " ")}</dt><dd>{chain}</dd></div>)}</dl></section></div></>;
}

function PlanSettings({ revision, onChange }: { revision: number; onChange: () => void }) {
  const [plans, setPlans] = useState<Record<string, Subscription | null>>({ claude: null, chatgpt: null }), [checking, setChecking] = useState(true);
  // A new revision (Check again, or a saved credential) asks each plan afresh.
  useEffect(() => { setChecking(true); Promise.all(["claude", "chatgpt"].map((id) => api.subscription(id).catch(() => null))).then(([claude, chatgpt]) => setPlans({ claude, chatgpt })).finally(() => setChecking(false)); }, [revision]);
  const check = onChange;
  const claude = plans.claude, chatgpt = plans.chatgpt;
  return <section className="settings-card wide plans-card"><div className="settings-title"><div><Sparkles size={18} /><div><h2>Subscriptions</h2><p>Run on your own Claude or ChatGPT plan instead of an API key. Calls count toward the plan's usage limits; there is no per-token charge.</p></div></div><button className="row-action" onClick={check} disabled={checking}>{checking ? <RefreshCw className="spin" size={12} /> : <RefreshCw size={12} />} Check again</button></div>
    <div className="plan-grid">
      <article className={`plan-tile ${claude?.ready ? "ready" : ""}`}><div className="plan-head"><i /><strong>Claude Pro or Max</strong><span>{claude ? (claude.ready ? "Ready" : claude.installed ? "Sign-in needed" : "Claude Code needed") : "Checking…"}</span></div>
        <p>{claude?.message ?? "Asking Claude Code…"}{claude?.account ? ` · ${claude.account}` : ""}</p>
        {claude?.installed && <dl><div><dt>Claude Code</dt><dd>{claude.version ? `v${claude.version}` : "installed"}</dd></div><div><dt>Executable</dt><dd title={claude.cli_path}>{claude.cli_path?.split("/").slice(-4).join("/")}</dd></div></dl>}
        {claude?.installed && !claude.ready && <ClaudeSignIn onSignedIn={check} />}
        {claude?.ready && <DefaultPlanButton provider="claude" label="Claude" onSaved={check} />}
        {claude && !claude.installed && <ol><li>Install Claude Code, or set <code>TRADINGAGENTS_CLAUDE_CLI</code> to its path. In Docker, rebuild with <code>WITH_CLAUDE_CODE=1</code>.</li><li>Then press <strong>Check again</strong> and sign in here.</li></ol>}
        {claude?.installed && !claude.ready && <details className="plan-alternatives"><summary>Other ways to sign in</summary><ol><li>In a terminal on this machine run <code>{claude.login_command ?? "tradingagents login claude"}</code>.</li><li>Or run <code>claude setup-token</code> on a machine with a browser and save the token below as <code>CLAUDE_CODE_OAUTH_TOKEN</code>.</li></ol></details>}
        <small><Terminal size={11} /> Every call runs through your own Claude Code; Signal Desk never reads your Claude credentials. For personal use on your own machine: Anthropic does not allow plan access to be shared or resold.</small></article>
      <article className={`plan-tile ${chatgpt?.ready ? "ready" : ""}`}><div className="plan-head"><i /><strong>ChatGPT Plus or Pro</strong><span>{chatgpt ? (chatgpt.ready ? "Ready" : "Sign-in needed") : "Checking…"}</span></div>
        <p>{chatgpt?.message ?? "Checking…"}{chatgpt?.account ? ` · ${chatgpt.account}` : ""}</p>
        {!chatgpt?.ready && <ol><li>In a terminal run <code>{chatgpt?.login_command ?? "tradingagents login chatgpt"}</code> and approve plan usage.</li><li>Choose it below as the active provider.</li></ol>}
        <small><Terminal size={11} /> The sign-in lives in ~/.tradingagents/chatgpt-auth.json on this machine.</small></article>
    </div></section>;
}

// Signs this backend's Claude Code in with its own login: the person approves on
// claude.com in this browser and pastes back the code it shows. Claude Code keeps
// the sign-in (in Docker, in the data volume), so it lasts across restarts.
function ClaudeSignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [link, setLink] = useState<string | null>(null), [code, setCode] = useState(""), [busy, setBusy] = useState<"start" | "finish" | null>(null), [error, setError] = useState("");
  const fail = (reason: unknown) => setError(reason instanceof Error ? reason.message : "That did not work.");
  const start = () => { setBusy("start"); setError(""); setCode(""); api.startClaudeSignIn().then(({ url }) => setLink(url)).catch(fail).finally(() => setBusy(null)); };
  const finish = () => { setBusy("finish"); setError(""); api.finishClaudeSignIn(code.trim()).then(() => { setLink(null); onSignedIn(); }).catch((reason) => { fail(reason); setLink(null); }).finally(() => setBusy(null)); };
  const cancel = () => { api.cancelClaudeSignIn().catch(() => undefined); setLink(null); setCode(""); setError(""); };
  if (!link) return <div className="plan-signin"><button className="primary-button" onClick={start} disabled={busy !== null}>{busy === "start" ? <RefreshCw className="spin" size={14} /> : <KeyRound size={14} />} {busy === "start" ? "Starting Claude Code's sign-in…" : "Sign in with Claude"}</button>{error && <p className="form-error">{error}</p>}<small>Signs in this console's own Claude Code, once: the sign-in is kept and refreshed by Claude Code across restarts.</small></div>;
  return <div className="plan-signin waiting">
    <ol>
      <li><span>Approve on claude.com with your Pro or Max account.</span><a className="secondary-button" href={link} target="_blank" rel="noreferrer noopener">Open claude.com sign-in <ArrowRight size={13} /></a></li>
      <li><span>Paste the code claude.com shows, then finish.</span><div className="plan-code"><input value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && code.trim()) finish(); }} placeholder="Paste the code here" autoComplete="off" spellCheck={false} aria-label="Sign-in code from claude.com" /><button className="primary-button" onClick={finish} disabled={!code.trim() || busy !== null}>{busy === "finish" ? <RefreshCw className="spin" size={14} /> : <Check size={14} />} {busy === "finish" ? "Signing in…" : "Finish sign-in"}</button></div></li>
    </ol>
    {error && <p className="form-error">{error}</p>}
    <button className="link-button" onClick={cancel} disabled={busy === "finish"}>Cancel</button>
  </div>;
}

// Saves a ready plan as the active provider, with the catalog's first models, so
// new analyses and backtests start on it in every later session.
function DefaultPlanButton({ provider, label, onSaved }: { provider: string; label: string; onSaved: () => void }) {
  const [prefs, setPrefs] = useState<Preferences | null>(null), [message, setMessage] = useState("");
  useEffect(() => { api.preferences().then(setPrefs).catch(() => setPrefs(null)); }, []);
  if (!prefs) return null;
  if (prefs.llm_provider === provider) return <p className="plan-default"><Check size={13} /> {label} is the default provider for new analyses and backtests.</p>;
  const save = () => api.providers(true).then((catalog) => {
    const entry = catalog.providers.find((item) => item.id === provider);
    if (!entry?.configured) throw new Error(`${label} is not ready yet; press Check again.`);
    const next = { ...prefs, llm_provider: provider, quick_think_llm: entry.quick_models[0]?.value ?? null, deep_think_llm: entry.deep_models[0]?.value ?? null };
    return api.savePreferences(next).then((value) => { setPrefs(value); onSaved(); });
  }).catch((error) => setMessage(error instanceof Error ? error.message : "The default could not be saved."));
  return <div className="plan-default"><button className="secondary-button" onClick={save}><Star size={13} /> Make {label} the default provider</button>{message && <p className="form-error">{message}</p>}</div>;
}

function PresetSettings({ revision }: { revision: number }) {
  const [prefs, setPrefs] = useState<Preferences | null>(null), [providers, setProviders] = useState<ProviderResponse | null>(null), [saved, setSaved] = useState(""), [newModel, setNewModel] = useState("");
  useEffect(() => { Promise.all([api.preferences(), api.providers(revision > 0)]).then(([p, catalog]) => { setPrefs(p); setProviders(catalog); }); }, [revision]);
  const provider = providers?.providers.find((item) => item.id === prefs?.llm_provider) ?? providers?.providers.find((item) => item.configured);
  const customModels = provider && prefs ? prefs.custom_models[provider.id] ?? [] : [], quickModels = selectableModels(provider, "quick", customModels), deepModels = selectableModels(provider, "deep", customModels);
  const quickSelect = quickModels.some((item) => item.value === prefs?.quick_think_llm) ? prefs?.quick_think_llm ?? "" : "custom", deepSelect = deepModels.some((item) => item.value === prefs?.deep_think_llm) ? prefs?.deep_think_llm ?? "" : "custom";
  const reasoningLevels = sharedReasoningLevels(provider, quickSelect, deepSelect, quickModels, deepModels);
  useEffect(() => { if (!prefs) return; const current = reasoningValue(prefs, provider); if (current && !reasoningLevels.includes(current)) setPrefs((value) => value ? withReasoning(value, provider, "") : value); }, [provider?.id, quickSelect, deepSelect, reasoningLevels.join(":")]);
  if (!prefs || !providers) return <section className="settings-card wide"><div className="loading-rows"><span /></div></section>;
  const changeProvider = (id: string) => { const next = providers.providers.find((item) => item.id === id); if (!next?.configured) return; const quick = selectableModels(next, "quick", prefs.custom_models[id]), deep = selectableModels(next, "deep", prefs.custom_models[id]); setPrefs(withReasoning({ ...prefs, llm_provider: id, quick_think_llm: quick[0]?.value ?? null, deep_think_llm: deep[0]?.value ?? null }, next, "")); setSaved(""); };
  const toggle = (id: string) => setPrefs({ ...prefs, selected_analysts: prefs.selected_analysts.includes(id) ? prefs.selected_analysts.filter((item) => item !== id) : [...prefs.selected_analysts, id] });
  const addModel = () => { const model = newModel.trim(); if (!provider || !model || /\s/.test(model)) { setSaved("Use an exact model ID without spaces."); return; } setPrefs({ ...prefs, custom_models: { ...prefs.custom_models, [provider.id]: [...new Set([...(prefs.custom_models[provider.id] ?? []), model])] } }); setNewModel(""); setSaved("Model ID added. Save defaults to keep it."); };
  const removeModel = (model: string) => { if (!provider) return; const remaining = customModels.filter((item) => item !== model); setPrefs({ ...prefs, custom_models: { ...prefs.custom_models, [provider.id]: remaining }, quick_think_llm: prefs.quick_think_llm === model ? quickModels.find((item) => item.value !== model && item.value !== "custom")?.value ?? null : prefs.quick_think_llm, deep_think_llm: prefs.deep_think_llm === model ? deepModels.find((item) => item.value !== model && item.value !== "custom")?.value ?? null : prefs.deep_think_llm }); };
  const save = () => api.savePreferences(prefs).then((value) => { setPrefs(value); setSaved("Active provider and defaults saved."); }).catch((error) => setSaved(error instanceof Error ? error.message : "Defaults could not be saved."));
  return <section className="settings-card wide"><div className="settings-title"><div><Bot size={18} /><div><h2>Active AI provider & model defaults</h2><p>Credentials may coexist, but every run uses one active provider for both models.</p></div></div>{saved && <span className="security-chip"><Check size={12} /> {saved}</span>}</div><div className="single-provider-note"><ShieldCheck size={16} /><div><strong>Exactly one active provider</strong><span>{provider?.configured ? `${providerName(provider.id)} is active for new runs.` : "Configure a provider before making it active."}</span></div></div><div className="provider-grid">{providers.providers.map((item) => { const active = item.id === provider?.id; return <button key={item.id} className={`provider-choice ${active ? "active" : ""} ${item.configured ? "ready" : ""}`} disabled={!item.configured} onClick={() => changeProvider(item.id)}><div><i /><strong>{providerName(item.id)}</strong>{active && <span>Active</span>}</div><small>{item.availability === "subscription" ? item.configured ? `Signed in${item.subscription?.plan ? ` · ${item.subscription.plan} plan` : ""}` : `Sign in: ${item.subscription?.login_command ?? "see plans above"}` : item.configured ? item.key_env ? `Connected · ${item.key_env}` : "Runtime available" : item.key_env ? `Add ${item.key_env}` : "Setup required"}</small></button>; })}</div>{provider && <><div className="model-defaults-grid"><label><span>Quick model</span><select value={quickSelect} onChange={(e) => setPrefs({ ...prefs, quick_think_llm: e.target.value })}>{quickModels.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>{quickSelect === "custom" && <input className="preset-custom" value={prefs.quick_think_llm === "custom" ? "" : prefs.quick_think_llm ?? ""} onChange={(e) => setPrefs({ ...prefs, quick_think_llm: e.target.value })} placeholder="Exact quick model ID" />}</label><label><span>Deep model</span><select value={deepSelect} onChange={(e) => setPrefs({ ...prefs, deep_think_llm: e.target.value })}>{deepModels.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>{deepSelect === "custom" && <input className="preset-custom" value={prefs.deep_think_llm === "custom" ? "" : prefs.deep_think_llm ?? ""} onChange={(e) => setPrefs({ ...prefs, deep_think_llm: e.target.value })} placeholder="Exact deep model ID" />}</label>{provider.reasoning && reasoningLevels.length > 0 && <label><span>{provider.reasoning.label}</span><select value={reasoningLevels.includes(reasoningValue(prefs, provider)) ? reasoningValue(prefs, provider) : ""} onChange={(e) => setPrefs(withReasoning(prefs, provider, e.target.value))}><option value="">Provider default</option>{reasoningLevels.map((level) => <option key={level} value={level}>{level}{level === provider.reasoning?.default ? " · recommended" : ""}</option>)}</select><small>{provider.reasoning.env_var}</small></label>}</div><div className="custom-model-manager"><div><strong>Add a model by its exact provider code</strong><span>New releases can be used immediately without waiting for a catalog update.</span></div><div><input value={newModel} onChange={(e) => setNewModel(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") addModel(); }} placeholder={provider.id === "google" ? "e.g. gemini-3.5-flash-lite" : "Exact model ID"} /><button onClick={addModel}>Add model</button></div>{customModels.length > 0 && <div className="custom-model-chips">{customModels.map((model) => <span key={model}>{model}<button onClick={() => removeModel(model)} aria-label={`Remove ${model}`}><X size={12} /></button></span>)}</div>}</div></>}<div className="preset-form compact"><div className="preset-analysts"><span>Default desk</span>{analystOptions.map(([id, label]) => <button className={prefs.selected_analysts.includes(id) ? "selected" : ""} key={id} onClick={() => toggle(id)}><Check size={11} /> {label}</button>)}</div><LanguageField value={prefs.output_language} options={providers.output_languages} onChange={(value) => setPrefs({ ...prefs, output_language: value })} /><label className="preset-check"><input type="checkbox" checked={prefs.checkpoint_enabled} onChange={(e) => setPrefs({ ...prefs, checkpoint_enabled: e.target.checked })} /> Checkpoint by default</label><button className="primary-button" disabled={!prefs.selected_analysts.length || !provider?.configured || !prefs.quick_think_llm || !prefs.deep_think_llm || prefs.output_language.trim().length < 2 || prefs.quick_think_llm === "custom" || prefs.deep_think_llm === "custom"} onClick={save}>Save active defaults</button></div></section>;
}

