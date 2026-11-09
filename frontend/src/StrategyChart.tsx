import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries, IChartApi, ISeriesApi,
  LineSeries, LineStyle, PriceScaleMode, SeriesMarker, SeriesType, Time,
} from "lightweight-charts";
import { AlertCircle, RefreshCw } from "lucide-react";
import { AnalysisOutcome, api, ChartCandle, ChartPoint, PositionPlan, RunChart, Stance, StructurePlan, StructureRead, TechnicalRead, TimeframeRead } from "./api";
import { Band, BandsPrimitive } from "./chartZones";
import { formatDate, formatShortDate, formatSignedPercent } from "./shared";

const levelPresentation = {
  entry: { label: "Entry", color: "#2d8b68" },
  take_profit: { label: "Take profit", color: "#3b72c4" },
  stop_loss: { label: "Stop loss", color: "#c4574e" },
} as const;
const directionNames = { long: "Long setup", hold: "Hold", reduce: "Reduce / sell setup" } as const;
const biasNames = { bullish: "Bullish", bearish: "Bearish", neutral: "Neutral" } as const;
const tranchePresentation = { buy: { label: "Buy", color: "#2d8b68" }, sell: { label: "Sell", color: "#3b72c4" } } as const;
const price = (value: number) => value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: value < 1 ? 6 : 2 });
const amount = (value: number, currency: string | null) => `${value.toLocaleString(undefined, { maximumFractionDigits: value < 100 ? 2 : 0 })}${currency ? ` ${currency}` : ""}`;
const pct = (value: number, digits = 2) => `${value.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
const range = (low: number, high: number) => `${price(low)} – ${price(high)}`;

// Swing traders read weekly bars, investors monthly ones; the daily view keeps
// the indicators the market analyst was offered.
type Frame = "daily" | "weekly" | "monthly";
const frames: { key: Frame; label: string }[] = [{ key: "daily", label: "Daily" }, { key: "weekly", label: "Weekly" }, { key: "monthly", label: "Monthly" }];
const FRAME_KEY = "signaldesk.chart.timeframe";
function savedFrame(): Frame {
  try { const saved = localStorage.getItem(FRAME_KEY); if (saved === "daily" || saved === "weekly" || saved === "monthly") return saved; } catch { /* storage blocked */ }
  return "weekly";
}

// What a reader can switch on and off, and on which timeframes each exists. The
// averages are one categorical set (checked for colour-blind separation on the
// paper surface); the plan keeps the decision colours, the zones green for
// buying and blue for taking profit, and the rest stays neutral.
type Layer = "zones" | "plan" | "averages" | "ema10" | "sma50" | "sma200" | "bollinger" | "levels" | "volume" | "rsi" | "macd";
const ALL: Frame[] = ["daily", "weekly", "monthly"];
const layers: { key: Layer; label: string; color: string; frames: Frame[] }[] = [
  { key: "zones", label: "Buy / take-profit zones", color: "#2d8b68", frames: ALL },
  { key: "plan", label: "Decision tranches", color: "#3b72c4", frames: ALL },
  { key: "averages", label: "Trend averages", color: "#4b3f9e", frames: ["weekly", "monthly"] },
  { key: "ema10", label: "10 EMA", color: "#b07619", frames: ["daily"] },
  { key: "sma50", label: "50 SMA", color: "#b04f8a", frames: ["daily"] },
  { key: "sma200", label: "200 SMA", color: "#4b3f9e", frames: ["daily"] },
  { key: "bollinger", label: "Bollinger 20/2", color: "#8f9c97", frames: ["daily"] },
  { key: "levels", label: "Daily swing levels", color: "#6f8f84", frames: ["daily"] },
  { key: "volume", label: "Volume", color: "#9fb3aa", frames: ALL },
  { key: "rsi", label: "RSI 14", color: "#4b3f9e", frames: ALL },
  { key: "macd", label: "MACD 12/26/9", color: "#3b72c4", frames: ["daily"] },
];
const defaultLayers: Layer[] = ["zones", "plan", "averages", "ema10", "sma50", "sma200", "volume", "rsi"];
// Versioned: a choice saved before the zones existed would hide them.
const LAYER_KEY = "signaldesk.chart.layers.v2";
function savedLayers(): Set<Layer> {
  try { const saved = JSON.parse(localStorage.getItem(LAYER_KEY) ?? "null"); if (Array.isArray(saved)) return new Set(saved.filter((key): key is Layer => layers.some((layer) => layer.key === key))); } catch { /* storage blocked: use the defaults */ }
  return new Set(defaultLayers);
}

// The decision's "what to do now" and the chart's stance share one palette:
// green to act, amber to wait, grey to hold, blue to take profit, red to stay out.
const stanceTone: Record<Stance, string> = { buy_zone: "act", wait_pullback: "wait", wait_breakout: "wait", hold: "hold", take_profit: "profit", avoid: "out" };
const actionTone: Record<string, string> = { "Buy now": "act", "Wait for pullback": "wait", "Wait for breakout": "wait", Hold: "hold", "Take profit": "profit", Exit: "out", Avoid: "out" };
const zoneColors = {
  buy: { fill: "rgba(45,139,104,.15)", edge: "#25694f" },
  profit: { fill: "rgba(59,114,196,.13)", edge: "#2f5d9e" },
  sellInto: { fill: "rgba(196,87,78,.11)", edge: "#9b4a42" },
  support: { fill: "rgba(111,143,132,.07)", edge: "rgba(79,111,100,.55)" },
  resistance: { fill: "rgba(168,121,111,.07)", edge: "rgba(138,96,86,.55)" },
};
const same = (a: { low: number; high: number }, b: { low: number; high: number }) => Math.abs(a.low - b.low) < 1e-9 && Math.abs(a.high - b.high) < 1e-9;

/** The plan's zones first (they widen the autoscale), then the other swing zones faintly. */
function zoneBands(read: StructureRead, lens: TimeframeRead): Band[] {
  const plan = lens.plan, down = lens.trend === "down", bands: Band[] = [];
  if (plan.buy_zone) bands.push({ ...plan.buy_zone, ...zoneColors.buy, label: `Buy zone · ${range(plan.buy_zone.low, plan.buy_zone.high)}`, key: true });
  plan.targets.forEach((target, index) => bands.push({ ...target, ...(down ? zoneColors.sellInto : zoneColors.profit), dashed: plan.targets_projected, key: index === 0, label: `${down ? "Sell into strength" : plan.targets_projected ? "Projected target" : "Take profit"} ${index + 1} · ${range(target.low, target.high)}` }));
  read.zones.filter((zone) => !bands.some((band) => same(band, zone))).forEach((zone) => bands.push({ low: zone.low, high: zone.high, ...zoneColors[zone.kind], label: `${zone.kind === "support" ? "Support" : "Resistance"} · ${zone.touches} swing${zone.touches === 1 ? "" : "s"}` }));
  return bands;
}

/** Where a decision's tranche sits against the plan's zones. */
function trancheFit(tranchePrice: number, plan: StructurePlan, close: number) {
  if (plan.buy_zone && tranchePrice >= plan.buy_zone.low && tranchePrice <= plan.buy_zone.high) return "in the buy zone";
  const target = plan.targets.findIndex((zone) => tranchePrice >= zone.low && tranchePrice <= zone.high);
  if (target >= 0) return `in take-profit zone ${target + 1}`;
  if (plan.invalidation && tranchePrice < plan.invalidation.price) return "below the invalidation level";
  if (plan.buy_zone && tranchePrice > plan.buy_zone.high && tranchePrice < close) return `${formatSignedPercent(tranchePrice / plan.buy_zone.high - 1)} above the buy zone`;
  if (plan.buy_zone && tranchePrice < plan.buy_zone.low) return `${formatSignedPercent(tranchePrice / plan.buy_zone.low - 1)} below the buy zone`;
  return "outside the zones";
}

// What the price did after the analysis date, against the close the analysis saw.
function OutcomeStrip({ outcome }: { outcome: AnalysisOutcome }) {
  const move = (value: number) => formatSignedPercent(value / outcome.close - 1);
  if (!outcome.sessions) return <div className="outcome-strip"><span>Analysed on the latest close ({price(outcome.close)} on {formatDate(outcome.date)}). The outcome appears here as new sessions trade.</span></div>;
  return <div className="outcome-strip"><span>Since the analysis · close {price(outcome.close)} on {formatDate(outcome.date)}</span><strong className={outcome.change_pct >= 0 ? "up" : "down"}>Now {price(outcome.latest_close)} ({formatSignedPercent(outcome.change_pct / 100)}) after {outcome.sessions} sessions</strong>{outcome.high !== null && <span>High {price(outcome.high)} on {formatShortDate(outcome.high_date!)} ({move(outcome.high)})</span>}{outcome.low !== null && <span>Low {price(outcome.low)} on {formatShortDate(outcome.low_date!)} ({move(outcome.low)})</span>}{outcome.touches.map((touch) => <span key={touch.kind} className={touch.date ? "reached" : ""}>{touch.label ?? levelPresentation[touch.kind].label} {price(touch.price)}: {touch.date ? `reached ${formatShortDate(touch.date)}` : "not reached"}</span>)}</div>;
}

// The tranches the decision scaled in and out with, what they add up to, and how they would have traded.
function PlanCard({ plan, direction }: { plan: PositionPlan; direction: RunChart["direction"] }) {
  const summary = plan.summary, cash = summary?.basis === "cash", currency = summary?.currency ?? null;
  const of = cash ? "of cash" : "of position value";
  const tiles: [string, string, string?][] = !summary ? [] : cash ? [
    ["Committed", `${pct(summary.committed_pct)} of cash`, summary.cash !== null ? amount(summary.cash * summary.committed_pct / 100, currency) : undefined],
    ["Average entry", summary.average_entry !== null ? price(summary.average_entry) : "—", "Cost-weighted across the Buys"],
    [summary.protective_stop ? "Stop" : "Stop (not protective)", summary.stop !== null ? price(summary.stop) : "Not stated", summary.protective_stop ? "Exits whatever the plan holds" : undefined],
    ["Risk at stop", summary.risk_pct !== null ? `${pct(summary.risk_pct)} of cash` : "—", summary.risk_pct !== null && summary.cash !== null ? amount(summary.cash * summary.risk_pct / 100, currency) : "Every Buy fills, then the stop"],
    ["Reward at targets", summary.reward_pct !== null ? `${pct(summary.reward_pct)} of cash` : "—", summary.reward_pct !== null && summary.cash !== null ? amount(summary.cash * summary.reward_pct / 100, currency) : "Every Sell fills"],
    ["Reward / risk", summary.reward_risk !== null ? `${summary.reward_risk.toFixed(2)}R` : "—", summary.reward_risk !== null && summary.reward_risk < 1.5 ? "Thin for the risk taken" : undefined],
    ["Left to run", summary.remaining_pct !== null ? `${pct(summary.remaining_pct, 1)} of position` : "No Sells set", "To the stop or the time horizon"],
  ] : [
    ["Sold", `${pct(summary.sold_pct, 1)} of position`, summary.held_units !== null ? `of ${summary.held_units.toLocaleString()} units held` : undefined],
    ["Kept", summary.remaining_pct !== null ? `${pct(summary.remaining_pct, 1)} of position` : "—"],
    ["Selling into strength", summary.reward_pct !== null ? `${formatSignedPercent(summary.reward_pct / 100, 2)} ${of}` : "—", "Against selling all at the analysis close"],
    [direction === "reduce" ? "Reassess above" : "Stop", summary.stop !== null ? price(summary.stop) : "Not stated"],
  ];
  const replay = plan.replay;
  return <div className="plan-card"><div className="panel-heading"><span className="eyebrow">POSITION PLAN · SCALED TRANCHES</span><small>{plan.source ?? "Decision"} · Buys sized as a share of available cash, Sells as a share of the position · prices against the {price(plan.reference)} analysis close</small></div>
    {plan.tranches.length > 0 && <div className="plan-table-wrap"><table className="plan-table"><thead><tr><th>#</th><th>Side</th><th>Price</th><th>Size</th><th>From close</th><th>Trigger</th><th>Since analysis</th></tr></thead><tbody>{plan.tranches.map((tranche) => <tr key={tranche.number}><td>{tranche.number}</td><td><span className={`side-chip side-${tranche.side}`}>{tranchePresentation[tranche.side].label}</span></td><td className="figure">{price(tranche.price)}</td><td className="figure">{pct(tranche.size_pct, 1)} {tranche.side === "buy" ? "of cash" : "of position"}{tranche.amount !== null && <small>{amount(tranche.amount, currency)}</small>}{tranche.units !== null && <small>{tranche.units.toLocaleString(undefined, { maximumFractionDigits: 4 })} units</small>}</td><td className="figure">{Math.abs(tranche.distance_pct) < 0.05 ? "At close" : formatSignedPercent(tranche.distance_pct / 100)}</td><td>{tranche.trigger || "—"}</td><td className={tranche.reached ? "reached" : "pending"}>{tranche.reached ? `Reached ${formatShortDate(tranche.reached)}` : "Not reached"}</td></tr>)}</tbody></table></div>}
    {tiles.length > 0 && <div className="plan-metrics">{tiles.map(([label, value, hint]) => <div key={label}><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</div>)}</div>}
    {replay && <div className="plan-replay"><span>Traded on the {replay.sessions} session{replay.sessions === 1 ? "" : "s"} since the analysis</span><span>{replay.filled.length ? `Filled ${replay.filled.map((fill) => `${fill.number} (${formatShortDate(fill.date)})`).join(", ")}` : "No tranche filled yet"}</span>{replay.stopped && <span className="stopped">Stopped out {formatShortDate(replay.stopped.date)} at {price(replay.stopped.price)}</span>}<strong className={replay.result_pct >= 0 ? "up" : "down"}>Plan {formatSignedPercent(replay.result_pct / 100, 2)} {of}</strong><span>{cash ? `Buying the whole ${pct(summary?.committed_pct ?? 0)} at the close` : "Holding the whole position"}: {formatSignedPercent(replay.all_at_close_pct / 100, 2)}</span></div>}
  </div>;
}

function ReadColumn({ label, read }: { label: string; read: TechnicalRead }) {
  const v = read.values;
  return <section><header><div><strong>{label}</strong><span>{formatDate(read.date)} · close {price(read.close)}</span></div><span className={`bias-chip bias-${read.bias}`}>{biasNames[read.bias]} {read.score > 0 ? "+" : ""}{read.score}</span></header>
    <ul className="signal-list">{read.signals.map((signal) => <li key={signal.name} className={`tone-${signal.tone}`}><i aria-hidden="true" /><div><b>{signal.name}</b> <em>{signal.tone}</em><span>{signal.text}</span></div></li>)}</ul>
    <div className="level-columns"><div><small>Resistance above</small>{read.resistances.length ? read.resistances.map((level) => <span key={level.price}>{price(level.price)} <em>{level.touches} swing{level.touches === 1 ? "" : "s"} · last {formatShortDate(level.last_date)}</em></span>) : <span>None from pivots</span>}</div><div><small>Support below</small>{read.supports.length ? read.supports.map((level) => <span key={level.price}>{price(level.price)} <em>{level.touches} swing{level.touches === 1 ? "" : "s"} · last {formatShortDate(level.last_date)}</em></span>) : <span>None from pivots</span>}</div></div>
    <dl className="tech-values">{v.atr14 != null && <div><dt>ATR 14</dt><dd>{price(v.atr14)} ({pct(v.atr_pct ?? 0, 1)})</dd></div>}{v.rsi14 != null && <div><dt>RSI 14</dt><dd>{v.rsi14.toFixed(0)}</dd></div>}{v.high_52w != null && v.low_52w != null && <div><dt>52-week range</dt><dd>{price(v.low_52w)} – {price(v.high_52w)}</dd></div>}</dl>
    {read.notes.map((note) => <p key={note} className="tech-note">{note}</p>)}</section>;
}

// The same indicators read by fixed rules for every run, so runs compare like for like.
function TechnicalCard({ technicals }: { technicals: NonNullable<RunChart["technicals"]> }) {
  const candidates: [string, TechnicalRead | null][] = [["At the analysis", technicals.analysis], [technicals.analysis ? "Now" : "Latest close", technicals.latest]];
  const reads = candidates.filter((entry): entry is [string, TechnicalRead] => entry[1] !== null);
  if (!reads.length) return null;
  return <div className="tech-card"><div className="panel-heading"><span className="eyebrow">TECHNICAL READ · RULE-BASED</span><small>Computed from the candles with fixed rules (trend, swing structure, RSI, MACD, volume flow), each read from the bars up to its date. A cross-check on the agents, not their view.</small></div><div className="tech-grid">{reads.map(([label, read]) => <ReadColumn key={label} label={label} read={read} />)}</div></div>;
}

// What to do now: the final decision's call, and the chart's rule-based stance on each lens.
function StanceBanner({ actionNow, read, frame, onFrame }: { actionNow: string | null; read: StructureRead | null; frame: Frame; onFrame: (frame: Frame) => void }) {
  const active: "weekly" | "monthly" = frame === "monthly" ? "monthly" : "weekly", lens = read?.timeframes[active];
  if (!actionNow && !lens) return null;
  return <div className="stance-banner">
    <div className="stance-row">
      {actionNow && <div className="stance-cell"><span>Final decision · action now</span><strong className={`stance-chip stance-${actionTone[actionNow] ?? "hold"}`}>{actionNow}</strong></div>}
      {read && (["weekly", "monthly"] as const).map((key) => { const tf = read.timeframes[key]; return <button key={key} type="button" className={`stance-cell ${key === active ? "active" : ""}`} aria-pressed={key === active} onClick={() => onFrame(key)}><span>{tf.lens} · {key} chart</span><strong className={`stance-chip stance-${stanceTone[tf.plan.stance]}`}>{tf.plan.label}</strong></button>; })}
    </div>
    {lens && <p className="stance-text"><b>New money:</b> {lens.plan.new_money} <b>Holders:</b> {lens.plan.holders}</p>}
  </div>;
}

// The thresholds the stance rests on, and where the decision's own tranches fall against them.
function KeyLevelsCard({ read, frame, plan, asOf, onAsOf, hasLatest }: { read: StructureRead; frame: Frame; plan: PositionPlan | null; asOf: "analysis" | "latest"; onAsOf: (value: "analysis" | "latest") => void; hasLatest: boolean }) {
  const key: "weekly" | "monthly" = frame === "monthly" ? "monthly" : "weekly", lens = read.timeframes[key], structure = lens.plan, close = read.close;
  const from = (value: number) => formatSignedPercent(value / close - 1);
  const rows: [string, string, string, string, string][] = [];
  if (structure.buy_zone) rows.push(["buy", "Buy zone", range(structure.buy_zone.low, structure.buy_zone.high), close >= structure.buy_zone.low && close <= structure.buy_zone.high ? "Price inside" : `${from(structure.buy_zone.high)} to the top`, structure.buy_zone.why]);
  structure.targets.forEach((target, index) => rows.push([lens.trend === "down" ? "out" : "profit", `${lens.trend === "down" ? "Sell into strength" : "Take profit"} ${index + 1}`, range(target.low, target.high), close >= target.low && close <= target.high ? "Price inside" : `${from(target.low)} to the bottom`, target.why]));
  if (structure.trigger) rows.push(["trigger", lens.trend === "down" ? "Trend turns above" : "Breakout above", price(structure.trigger.price), from(structure.trigger.price), structure.trigger.why]);
  if (structure.invalidation) rows.push(["stop", "Invalidation", price(structure.invalidation.price), from(structure.invalidation.price), structure.invalidation.why]);
  const tranches = plan?.tranches ?? [];
  return <div className="levels-card"><div className="panel-heading levels-heading"><div><span className="eyebrow">KEY THRESHOLDS · {lens.lens.toUpperCase()} ({key.toUpperCase()} BARS)</span><small>Rule-based zones from weekly and monthly swing highs and lows, read at the close of {formatDate(read.as_of)} ({price(close)}). The same rules gave the market analyst its levels.</small></div>{hasLatest && <div className="segmented small" role="group" aria-label="Zones as of"><button type="button" className={asOf === "analysis" ? "on" : ""} aria-pressed={asOf === "analysis"} onClick={() => onAsOf("analysis")}>At the analysis</button><button type="button" className={asOf === "latest" ? "on" : ""} aria-pressed={asOf === "latest"} onClick={() => onAsOf("latest")}>Today</button></div>}</div>
    <div className="plan-table-wrap"><table className="plan-table levels-table"><thead><tr><th>Level</th><th>Price</th><th>From close</th><th>What defines it</th></tr></thead><tbody>{rows.map(([tone, label, value, distance, why]) => <tr key={label}><td><span className={`level-chip level-${tone}`}>{label}</span></td><td className="figure">{value}</td><td className="figure">{distance}</td><td>{why}</td></tr>)}{!rows.length && <tr><td colSpan={4}>No zone stands out on the {key} bars yet.</td></tr>}</tbody></table></div>
    <ul className="structure-notes"><li>{lens.trend_text}</li>{lens.structure_text && <li>{lens.structure_text}</li>}<li>ATR 14 {price(lens.atr)} ({pct(lens.atr / close * 100, 1)} of the close){lens.rsi14 !== null ? ` · RSI 14 ${lens.rsi14.toFixed(0)}` : ""} · {lens.averages}{lens.sma_slow !== null ? ` ${price(lens.sma_fast ?? 0)} / ${price(lens.sma_slow)}` : ""}</li>{structure.targets_projected && <li>No swing high sits above the price in the data, so the take-profit zones are projected from the ATR, not read from the chart.</li>}</ul>
    {tranches.length > 0 && <div className="tranche-fit"><small>The decision's tranches against these zones</small>{tranches.map((tranche) => <span key={tranche.number} className={`side-${tranche.side}`}><b>{tranche.side === "buy" ? "B" : "S"}{tranche.number} {price(tranche.price)}</b> {trancheFit(tranche.price, structure, close)}</span>)}</div>}
  </div>;
}

// The decision's single levels, less those a tranche already stands for: Buys
// replace the entry, and a Sell at the target's price replaces the target.
const shownLevels = (data: RunChart) => {
  const tranches = data.plan?.tranches ?? [], buys = tranches.some((tranche) => tranche.side === "buy");
  return data.levels.filter((level) => !(level.kind === "entry" && buys) && !(level.kind === "take_profit" && tranches.some((tranche) => Math.abs(tranche.price - level.price) < 1e-9)));
};
const line = (points: ChartPoint[] | undefined) => (points ?? []).map((point) => ({ time: point.time as Time, value: point.value }));

export function StrategyChart({ runId, revision }: { runId: string; revision: string }) {
  const container = useRef<HTMLDivElement | null>(null), readout = useRef<HTMLDivElement | null>(null);
  const [data, setData] = useState<RunChart | null>(null), [error, setError] = useState(""), [loading, setLoading] = useState(true);
  const [shown, setShown] = useState<Set<Layer>>(savedLayers), [chosenFrame, setChosenFrame] = useState<Frame>(savedFrame), [asOf, setAsOf] = useState<"analysis" | "latest">("analysis");
  const toggle = (key: Layer) => setShown((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); try { localStorage.setItem(LAYER_KEY, JSON.stringify([...next])); } catch { /* not remembered */ } return next; });
  const chooseFrame = (next: Frame) => { setChosenFrame(next); try { localStorage.setItem(FRAME_KEY, next); } catch { /* not remembered */ } };
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.chart(runId).then((result) => { if (!cancelled) { setData(result); setError(""); } }).catch((reason) => { if (!cancelled) { setData(null); setError(reason instanceof Error ? reason.message : "Chart unavailable"); } }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, revision]);
  const structure = data?.structure ?? null;
  // Without weekly and monthly bars (the structure failed to load) only the daily chart exists.
  const frame: Frame = structure ? chosenFrame : "daily";
  const read = structure ? (asOf === "latest" ? structure.latest : null) ?? structure.analysis ?? structure.latest : null;
  const lens = read ? read.timeframes[frame === "monthly" ? "monthly" : "weekly"] : null;
  useEffect(() => {
    if (!container.current || !data?.candles.length) return;
    const element = container.current, daily = frame === "daily", plan = data.plan;
    const source = daily ? null : structure?.frames[frame] ?? null;
    const bars: ChartCandle[] = daily ? data.candles : source?.candles ?? [];
    if (!bars.length) return;
    const series = daily ? data.technicals?.series ?? {} : source?.series ?? {};
    const on = (key: Layer) => shown.has(key) && layers.find((layer) => layer.key === key)!.frames.includes(frame);
    const panes = (["volume", "rsi", "macd"] as const).filter((key) => on(key) && (key === "volume" ? bars.some((bar) => bar.volume) : series[key === "rsi" ? "rsi14" : "macd"]?.length));
    const chart: IChartApi = createChart(element, {
      width: element.clientWidth,
      height: 400 + panes.length * 96,
      layout: { background: { type: ColorType.Solid, color: "#faf9f4" }, textColor: "#61706b", panes: { separatorColor: "#e1e4dc" } },
      grid: { vertLines: { color: "#eef0ea" }, horzLines: { color: "#e6e9e2" } },
      // Five years of months can span a tenfold move: a log scale keeps its zones readable.
      rightPriceScale: { borderColor: "#d8ddd5", mode: frame === "monthly" ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal },
      timeScale: { borderColor: "#d8ddd5", timeVisible: false, rightOffset: 4 },
      crosshair: { vertLine: { color: "#91a39c" }, horzLine: { color: "#91a39c" } },
    });
    const candles = chart.addSeries(CandlestickSeries, { upColor: "#4e9871", downColor: "#c96b62", borderVisible: false, wickUpColor: "#4e9871", wickDownColor: "#c96b62" });
    candles.setData(bars.map((bar) => ({ ...bar, time: bar.time as Time })));
    const overlay = (key: string, title: string, color: string, width: 1 | 2 | 3, pane = 0, extra: object = {}) => { const added = chart.addSeries(LineSeries, { color, lineWidth: width, title, priceLineVisible: false, crosshairMarkerVisible: false, ...extra }, pane); added.setData(line(series[key])); return added; };
    const readable: [string, ISeriesApi<SeriesType>][] = [];
    if (daily) {
      if (on("bollinger")) { for (const key of ["bb_upper", "bb_lower"]) overlay(key, "", "#8f9c97", 1, 0, { lineStyle: LineStyle.Dotted, lastValueVisible: false }); overlay("bb_middle", "", "#b7c1bc", 1, 0, { lineStyle: LineStyle.Dotted, lastValueVisible: false }); }
      if (on("ema10")) readable.push(["10 EMA", overlay("ema10", "10 EMA", "#b07619", 1)]);
      if (on("sma50")) readable.push(["50 SMA", overlay("sma50", "50 SMA", "#b04f8a", 2)]);
      if (on("sma200")) readable.push(["200 SMA", overlay("sma200", "200 SMA", "#4b3f9e", 3)]);
    } else if (source && on("averages")) {
      readable.push([`${source.fast}-${source.unit}`, overlay("sma_fast", `${source.fast}-${source.unit} SMA`, "#b07619", 2)]);
      readable.push([`${source.slow}-${source.unit}`, overlay("sma_slow", `${source.slow}-${source.unit} SMA`, "#4b3f9e", 3)]);
    }

    if (on("zones") && read && lens) {
      candles.attachPrimitive(new BandsPrimitive(zoneBands(read, lens)));
      const unit = frame === "monthly" ? "monthly" : "weekly";
      if (lens.plan.invalidation) candles.createPriceLine({ price: lens.plan.invalidation.price, color: "#c4574e", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `Invalidation (${unit} close)` });
      if (lens.plan.trigger) candles.createPriceLine({ price: lens.plan.trigger.price, color: "#173f35", lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: true, title: lens.trend === "down" ? "Trend turns above" : `Breakout (${unit} close)` });
    }
    if (on("plan")) {
      (plan?.tranches ?? []).forEach((tranche) => candles.createPriceLine({ price: tranche.price, color: tranchePresentation[tranche.side].color, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: `${tranche.side === "buy" ? "B" : "S"}${tranche.number} · ${pct(tranche.size_pct, 1)}` }));
      shownLevels(data).forEach((level) => { const presentation = levelPresentation[level.kind]; candles.createPriceLine({ price: level.price, color: presentation.color, lineWidth: 2, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: level.label ?? presentation.label }); });
    }
    const dailyRead = data.technicals?.analysis ?? data.technicals?.latest;
    if (daily && on("levels") && dailyRead) {
      dailyRead.supports.forEach((level) => candles.createPriceLine({ price: level.price, color: "#6f8f84", lineWidth: 1, lineStyle: LineStyle.SparseDotted, axisLabelVisible: false, title: `Support ×${level.touches}` }));
      dailyRead.resistances.forEach((level) => candles.createPriceLine({ price: level.price, color: "#a8796f", lineWidth: 1, lineStyle: LineStyle.SparseDotted, axisLabelVisible: false, title: `Resistance ×${level.touches}` }));
    }
    const markers: SeriesMarker<Time>[] = [];
    // On weekly and monthly bars the analysis falls inside the bar that holds its date.
    const holding = (day: string) => [...bars].reverse().find((bar) => bar.time <= day)?.time;
    const analysisBar = data.outcome ? daily ? data.outcome.date : holding(data.outcome.date) : undefined;
    if (analysisBar) markers.push({ time: analysisBar as Time, position: "aboveBar", shape: "arrowDown", color: "#173f35", text: "Analysis" });
    if (daily && on("sma200")) (data.technicals?.crosses ?? []).forEach((cross) => markers.push({ time: cross.time as Time, position: cross.kind === "golden" ? "belowBar" : "aboveBar", shape: "circle", color: "#4b3f9e", text: cross.kind === "golden" ? "Golden cross" : "Death cross" }));
    if (daily && on("plan") && plan?.replay) {
      plan.replay.filled.forEach((fill) => { const tranche = plan.tranches.find((item) => item.number === fill.number); if (tranche) markers.push({ time: fill.date as Time, position: tranche.side === "buy" ? "belowBar" : "aboveBar", shape: tranche.side === "buy" ? "arrowUp" : "arrowDown", color: tranchePresentation[tranche.side].color, text: `${tranche.side === "buy" ? "B" : "S"}${tranche.number}` }); });
      if (plan.replay.stopped) markers.push({ time: plan.replay.stopped.date as Time, position: "belowBar", shape: "square", color: "#c4574e", text: "Stop" });
    }
    createSeriesMarkers(candles, markers.sort((a, b) => String(a.time).localeCompare(String(b.time))));

    panes.forEach((key, offset) => {
      const pane = offset + 1;
      if (key === "volume") {
        const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false, title: "Volume" }, pane);
        volume.setData(bars.map((bar) => ({ time: bar.time as Time, value: bar.volume ?? 0, color: bar.close >= bar.open ? "rgba(78,152,113,.45)" : "rgba(201,107,98,.45)" })));
        if (daily) overlay("volume_sma20", "20-day avg", "#61706b", 1, pane, { lastValueVisible: false, priceFormat: { type: "volume" } });
      } else if (key === "rsi") {
        const rsi = overlay("rsi14", "RSI 14", "#4b3f9e", 2, pane);
        readable.push(["RSI", rsi]);
        [70, 30].forEach((bound) => rsi.createPriceLine({ price: bound, color: "#a9b4af", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: bound === 70 ? "Overbought" : "Oversold" }));
      } else {
        const histogram = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane);
        histogram.setData(line(series.macd_hist).map((point) => ({ ...point, color: point.value >= 0 ? "rgba(78,152,113,.5)" : "rgba(201,107,98,.5)" })));
        readable.push(["MACD", overlay("macd", "MACD", "#3b72c4", 2, pane)]);
        overlay("macd_signal", "Signal", "#b07619", 1, pane);
      }
    });
    // Proportions rather than heights: each new pane re-divides the chart.
    chart.panes().forEach((pane, index) => pane.setStretchFactor(index === 0 ? 4 : 1));
    // Two years of weeks is the swing trader's window; months and days show whole.
    if (frame === "weekly" && bars.length > 104) chart.timeScale().setVisibleLogicalRange({ from: bars.length - 104, to: bars.length + 3 });
    else chart.timeScale().fitContent();

    // The values under the crosshair, written straight into the readout so a
    // mouse move does not re-render the page.
    const write = (time: Time | undefined) => {
      if (!readout.current) return;
      const day = time ? String(time) : bars[bars.length - 1].time, bar = bars.find((candle) => candle.time === day);
      if (!bar) return;
      const values = readable.map(([label, item]) => { const point = item.data().find((entry) => String(entry.time) === day) as { value?: number } | undefined; return point?.value !== undefined ? `<span>${label} <b>${label === "RSI" ? point.value.toFixed(0) : price(point.value)}</b></span>` : ""; }).join("");
      const period = daily ? "" : frame === "weekly" ? "Week of " : "Month of ";
      readout.current.innerHTML = `<span><b>${period}${formatDate(day)}</b></span><span>O <b>${price(bar.open)}</b></span><span>H <b>${price(bar.high)}</b></span><span>L <b>${price(bar.low)}</b></span><span>C <b>${price(bar.close)}</b></span>${values}`;
    };
    write(undefined);
    chart.subscribeCrosshairMove((param) => write(param.time));
    const resize = new ResizeObserver(() => chart.applyOptions({ width: element.clientWidth }));
    resize.observe(element);
    return () => { resize.disconnect(); chart.remove(); };
  }, [data, shown, frame, read, lens, structure]);
  const notes = [...(data?.notes ?? []), ...(data?.plan?.notes ?? [])];
  const frameName = frames.find((item) => item.key === frame)!.label;
  return <section className="strategy-chart-card"><div className="strategy-chart-heading"><div><span className="eyebrow">STRATEGY VISUAL</span><h2>Where to buy, take profit and step aside</h2><p>{data ? `${data.symbol} · ${frameName} candles through ${formatDate(data.as_of)} · Last close ${price(data.current_price)}` : "Current candles with the zones and levels behind the decision."}</p></div><div className="chart-badges">{data?.direction && <span className={`direction-chip direction-${data.direction}`}>{directionNames[data.direction]}</span>}<span className="chart-source">TradingView Lightweight Charts · Yahoo Finance data</span></div></div>
    {loading ? <div className="chart-state"><RefreshCw className="spin" /> Loading current candles…</div> : error ? <div className="chart-state chart-error"><AlertCircle size={17} /> {error}</div> : <>
      {data && <StanceBanner actionNow={data.action_now} read={read} frame={frame} onFrame={chooseFrame} />}
      <div className="chart-controls">{structure && <div className="segmented" role="group" aria-label="Timeframe">{frames.map((item) => <button key={item.key} type="button" className={frame === item.key ? "on" : ""} aria-pressed={frame === item.key} onClick={() => chooseFrame(item.key)}>{item.label}</button>)}</div>}
        {(data?.technicals || structure) && <div className="layer-toggles" role="group" aria-label="Chart layers">{layers.filter((layer) => layer.frames.includes(frame)).map((layer) => <button key={layer.key} type="button" aria-pressed={shown.has(layer.key)} className={shown.has(layer.key) ? "on" : ""} onClick={() => toggle(layer.key)}><i style={{ background: layer.color }} />{layer.key === "averages" && structure && frame !== "daily" ? `${structure.frames[frame].fast}/${structure.frames[frame].slow}-${structure.frames[frame].unit} SMA` : layer.label}</button>)}</div>}</div>
      <div ref={readout} className="chart-readout" aria-live="off" />
      <div ref={container} className="strategy-chart" />
      {data && <div className="strategy-legend">{shownLevels(data).map((level) => <div key={level.kind}><i style={{ background: levelPresentation[level.kind].color }} /><span>{level.label ?? levelPresentation[level.kind].label}</span><strong>{price(level.price)}</strong><small>{level.source}</small></div>)}{!data.levels.length && !data.plan?.tranches.length && <p>No entry, target or stop price that fits this decision was stated in the analysis.</p>}</div>}
      {data?.outcome && <OutcomeStrip outcome={data.outcome} />}
      {read && <KeyLevelsCard read={read} frame={frame} plan={data?.plan ?? null} asOf={asOf} onAsOf={setAsOf} hasLatest={!!structure?.latest && !!structure?.analysis} />}
      {data?.plan && <PlanCard plan={data.plan} direction={data.direction} />}
      {data?.technicals && <TechnicalCard technicals={data.technicals} />}
      {notes.length > 0 && <ul className="chart-notes">{notes.map((note) => <li key={note}>{note}</li>)}</ul>}
    </>}</section>;
}
