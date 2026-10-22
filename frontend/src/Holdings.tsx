import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Briefcase, Check, Eraser, Info, Plus, RefreshCw, Sparkles, Trash2, Undo2, Wallet } from "lucide-react";
import { api, Portfolio, PortfolioPreview, Quote, SavedPortfolio } from "./api";
import { formatNumber, formatSignedPercent, formatTimestamp, Metric, PageHeader } from "./shared";

type DraftPosition = { key: string; ticker: string; quantity: string; average_price: string };
type Draft = { cash: string; currency: string; positions: DraftPosition[] };

let draftKey = 0;
const nextKey = () => `position-${++draftKey}`;

function toDraft(book: Portfolio | null): Draft {
  return {
    cash: book?.cash === null || book?.cash === undefined ? "" : String(book.cash),
    currency: book?.currency ?? "",
    positions: (book?.positions ?? []).map((p) => ({ key: nextKey(), ticker: p.ticker, quantity: String(p.quantity), average_price: p.average_price === null ? "" : String(p.average_price) })),
  };
}

/** The draft as the API takes it, or the first problem that stops it being saved. */
function fromDraft(draft: Draft): { book: Portfolio | null; problem: string | null } {
  const cash = draft.cash.trim() === "" ? null : Number(draft.cash);
  if (cash !== null && (!Number.isFinite(cash) || cash < 0)) return { book: null, problem: "Cash must be a number of zero or more." };
  const seen = new Set<string>(), positions = [];
  for (const row of draft.positions) {
    const ticker = row.ticker.trim().toUpperCase();
    if (!ticker || /\s/.test(ticker)) return { book: null, problem: "Every position needs a compact ticker, e.g. NVDA or 0700.HK." };
    if (seen.has(ticker)) return { book: null, problem: `${ticker} is listed twice; combine it into one position.` };
    seen.add(ticker);
    const quantity = Number(row.quantity);
    if (!row.quantity.trim() || !Number.isFinite(quantity) || quantity === 0) return { book: null, problem: `${ticker} needs a non-zero quantity (negative for a short).` };
    const price = row.average_price.trim() === "" ? null : Number(row.average_price);
    if (price !== null && (!Number.isFinite(price) || price <= 0)) return { book: null, problem: `${ticker}'s average price must be above zero, or left empty.` };
    positions.push({ ticker, quantity, average_price: price });
  }
  return { book: { cash, currency: draft.currency.trim().toUpperCase() || null, positions }, problem: null };
}

const sameBook = (a: Portfolio | null, b: Portfolio | null) => JSON.stringify(a) === JSON.stringify(b);
const asBook = (saved: SavedPortfolio | null): Portfolio | null => saved?.configured ? { cash: saved.cash, currency: saved.currency, positions: saved.positions } : null;

export function HoldingsView({ onAnalyze }: { onAnalyze: (ticker: string) => void }) {
  const [saved, setSaved] = useState<SavedPortfolio | null>(null), [draft, setDraft] = useState<Draft>(toDraft(null)), [quotes, setQuotes] = useState<Record<string, Quote>>({}), [quotesLoading, setQuotesLoading] = useState(false);
  const [error, setError] = useState(""), [message, setMessage] = useState(""), [saving, setSaving] = useState(false);
  const loadQuotes = (book: SavedPortfolio) => { if (!book.configured || !book.positions.length) { setQuotes({}); return; } setQuotesLoading(true); api.portfolioQuotes().then((items) => setQuotes(Object.fromEntries(items.map((q) => [q.ticker, q])))).catch(() => setQuotes({})).finally(() => setQuotesLoading(false)); };
  useEffect(() => { api.portfolio().then((book) => { setSaved(book); setDraft(toDraft(asBook(book))); loadQuotes(book); }).catch((e) => setError(e.message)); }, []);

  const { book: parsed, problem } = fromDraft(draft);
  // A saved book has changes when the draft reads differently; with none saved, anything typed counts.
  const dirty = saved?.configured ? !(parsed && sameBook(parsed, asBook(saved))) : draft.positions.length > 0 || draft.cash.trim() !== "" || draft.currency.trim() !== "";
  const update = (key: string, field: keyof Omit<DraftPosition, "key">, value: string) => { setMessage(""); setDraft({ ...draft, positions: draft.positions.map((row) => row.key === key ? { ...row, [field]: field === "ticker" ? value.toUpperCase() : value } : row) }); };
  const save = async () => { if (!parsed) return; setSaving(true); setError(""); try { const book = await api.savePortfolio(parsed); setSaved(book); setDraft(toDraft(asBook(book))); setMessage(book.positions.length ? "Holdings saved." : "Saved as a flat book: no positions, so the agents size a new position from cash."); loadQuotes(book); } catch (e) { setError(e instanceof Error ? e.message : "Holdings could not be saved"); } finally { setSaving(false); } };
  const clear = async () => { if (!window.confirm("Forget the saved holdings? Runs will have no portfolio context until you save a book again.")) return; setError(""); try { await api.clearPortfolio(); const empty = { configured: false, cash: null, currency: null, positions: [], updated_at: null }; setSaved(empty); setDraft(toDraft(null)); setQuotes({}); setMessage("Holdings cleared. Runs now have no portfolio context."); } catch (e) { setError(e instanceof Error ? e.message : "Holdings could not be cleared"); } };

  const longs = parsed?.positions.filter((p) => p.quantity > 0).length ?? 0, shorts = parsed?.positions.filter((p) => p.quantity < 0).length ?? 0;
  const currency = draft.currency.trim().toUpperCase();
  return <><PageHeader eyebrow="PORTFOLIO CONTEXT" title="Current holdings" copy="Tell the trader, risk analysts and portfolio manager what you actually hold, so they size against your book. Runs use it only when you choose to decide against it." action={<button className="primary-button" disabled={!saved?.configured} onClick={() => onAnalyze(saved?.positions[0]?.ticker ?? "")}><Sparkles size={16} /> Analyze with my book</button>} />
    <section className="metric-grid"><Metric label="Book" value={!saved ? "…" : saved.configured ? (saved.positions.length ? "Saved" : "Flat") : "None"} detail={!saved ? "Loading" : saved.configured ? (saved.positions.length ? `${saved.positions.length} position${saved.positions.length === 1 ? "" : "s"}` : "Saved with no positions") : "Runs have no portfolio context"} /><Metric label="Positions" value={String(parsed?.positions.length ?? 0).padStart(2, "0")} detail={`${longs} long · ${shorts} short`} /><Metric label="Free cash" value={parsed?.cash === null || parsed?.cash === undefined ? "—" : formatNumber(parsed.cash, 0)} detail={currency || "No currency label"} /><Metric label="Last saved" value={saved?.updated_at ? formatTimestamp(saved.updated_at).split(",")[0] : "—"} detail={saved?.updated_at ? formatTimestamp(saved.updated_at) : "Not saved yet"} /></section>
    <div className="holdings-layout">
      <section className="table-card holdings-card">
        <div className="table-toolbar"><div><h2>Positions</h2><span>Signed quantities: negative is short. Average prices are in each instrument's quote currency.</span></div><div className="filter-actions"><button className="filter" onClick={() => { setMessage(""); setDraft({ ...draft, positions: [...draft.positions, { key: nextKey(), ticker: "", quantity: "", average_price: "" }] }); }}><Plus size={14} /> Add position</button></div></div>
        {error && <div className="failure-banner inline"><AlertCircle size={16} /><div><strong>Holdings were not saved</strong><span>{error}</span></div></div>}
        {draft.positions.length ? <div className="run-table-wrap"><table className="run-table holdings-table"><thead><tr><th>Ticker</th><th>Quantity</th><th>Average price</th><th>Last close</th><th>Unrealized P/L</th><th></th></tr></thead><tbody>{draft.positions.map((row) => {
          const quote = quotes[row.ticker.trim().toUpperCase()], quantity = Number(row.quantity), average = Number(row.average_price), last = quote?.last_close ?? null;
          const pnl = last !== null && row.average_price && Number.isFinite(average) && Number.isFinite(quantity) ? (last - average) * quantity : null;
          const pct = pnl !== null && average > 0 ? (last! / average - 1) * Math.sign(quantity) : null;
          return <tr key={row.key}><td><input className="cell-input ticker-input" value={row.ticker} onChange={(e) => update(row.key, "ticker", e.target.value)} placeholder="NVDA" aria-label="Ticker" /></td><td><input className="cell-input" inputMode="decimal" value={row.quantity} onChange={(e) => update(row.key, "quantity", e.target.value)} placeholder="120 or -50" aria-label="Quantity" />{quantity < 0 && <small className="short-tag">Short</small>}</td><td><input className="cell-input" inputMode="decimal" value={row.average_price} onChange={(e) => update(row.key, "average_price", e.target.value)} placeholder="Optional" aria-label="Average price" /></td><td>{quotesLoading ? <span className="muted">Loading…</span> : last !== null ? <><strong>{formatNumber(last, 4)}</strong><small>{quote?.as_of}</small></> : <span className="muted">{quote?.error ?? (dirty ? "Save to load" : "—")}</span>}</td><td>{pnl !== null ? <span className={pnl >= 0 ? "pnl positive" : "pnl negative"}>{pnl >= 0 ? "+" : ""}{formatNumber(pnl, 2)} <small>{formatSignedPercent(pct)}</small></span> : <span className="muted">—</span>}</td><td className="row-tools"><button className="row-action" disabled={!row.ticker.trim() || dirty} title={dirty ? "Save your changes first" : `Analyze ${row.ticker} against this book`} onClick={() => onAnalyze(row.ticker.trim().toUpperCase())}><Sparkles size={12} /> Analyze</button><button className="remove-run" onClick={() => { setMessage(""); setDraft({ ...draft, positions: draft.positions.filter((item) => item.key !== row.key) }); }} aria-label={`Remove ${row.ticker || "position"}`}><Trash2 size={13} /></button></td></tr>;
        })}</tbody></table></div> : <div className="holdings-empty"><Briefcase size={20} /><strong>{saved?.configured ? "A flat book" : "No positions"}</strong><span>{saved?.configured ? "The book is saved with no positions: the agents know you hold nothing and size any position from cash." : "Add what you hold, or save only your cash as a flat book. Without a saved book, runs carry no portfolio context at all."}</span></div>}
        <div className="holdings-footer">
          <label><span>Free cash</span><input inputMode="decimal" value={draft.cash} onChange={(e) => { setMessage(""); setDraft({ ...draft, cash: e.target.value }); }} placeholder="e.g. 25000" /></label>
          <label><span>Currency label</span><input value={draft.currency} maxLength={12} onChange={(e) => { setMessage(""); setDraft({ ...draft, currency: e.target.value.toUpperCase() }); }} placeholder="e.g. USD" /></label>
          <div className="holdings-actions">{problem ? <span className="form-problem"><AlertCircle size={13} /> {problem}</span> : message ? <span className="form-ok"><Check size={13} /> {message}</span> : dirty ? <span className="form-hint">Unsaved changes</span> : null}<button className="secondary-button" disabled={!dirty} onClick={() => { setDraft(toDraft(asBook(saved))); setMessage(""); }}><Undo2 size={14} /> Discard</button><button className="primary-button" disabled={!saved || !!problem || saving || (!dirty && saved.configured)} onClick={save}>{saving ? <RefreshCw className="spin" size={14} /> : <Wallet size={14} />} {saved?.configured ? "Save holdings" : parsed?.positions.length ? "Save holdings" : "Save as flat book"}</button></div>
        </div>
      </section>
      <aside className="run-aside">
        <AgentView saved={saved} />
        <div className="record-card book-rules"><span className="eyebrow">HOW THE BOOK IS USED</span><ul><li><strong>Off by default.</strong> A run decides against your book only when you choose it at launch; it records the exact book it was given.</li><li><strong>A flat book is not "no book".</strong> Saved with no positions, the agents know you hold nothing. With no book saved, they write guidance for any reader.</li><li><strong>Who reads it.</strong> The trader, the three risk analysts and the portfolio manager. Analysts and researchers stay position-neutral.</li><li><strong>Broker-neutral.</strong> Quantities are units and the currency is only a label: nothing here can place an order.</li></ul>{saved?.configured && <button className="clear-book" onClick={clear}><Eraser size={13} /> Forget saved holdings</button>}</div>
      </aside>
    </div></>;
}

function AgentView({ saved }: { saved: SavedPortfolio | null }) {
  const [ticker, setTicker] = useState(""), [preview, setPreview] = useState<PortfolioPreview | null>(null), [error, setError] = useState("");
  const options = useMemo(() => saved?.positions.map((p) => p.ticker) ?? [], [saved]);
  useEffect(() => { if (!ticker && options[0]) setTicker(options[0]); }, [options.join(":")]);
  useEffect(() => {
    if (!saved?.configured || !ticker.trim()) { setPreview(null); return; }
    const timer = window.setTimeout(() => api.portfolioPreview(ticker.trim()).then((value) => { setPreview(value); setError(""); }).catch((e) => setError(e.message)), 300);
    return () => window.clearTimeout(timer);
  }, [ticker, saved?.updated_at, saved?.configured]);
  return <div className="record-card agent-view"><span className="eyebrow">WHAT THE AGENTS READ</span>{saved?.configured ? <><label><span>For ticker</span><input list="book-tickers" value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="Any ticker" /><datalist id="book-tickers">{options.map((item) => <option key={item} value={item} />)}</datalist></label>{error ? <p className="muted">{error}</p> : <pre className="portfolio-context">{preview?.context ?? "…"}</pre>}<small><Info size={11} /> The saved book, exactly as it is added to the trader's, risk analysts' and portfolio manager's prompts.</small></> : <p className="muted">No book is saved, so runs carry no portfolio context.</p>}</div>;
}

/** The launch-form choice between no portfolio context and the saved book. */
export function PortfolioPicker({ ticker, value, onChange, onHoldings, backtest = false }: { ticker: string; value: boolean; onChange: (value: boolean) => void; onHoldings: () => void; backtest?: boolean }) {
  const [saved, setSaved] = useState<SavedPortfolio | null>(null), [preview, setPreview] = useState<PortfolioPreview | null>(null);
  useEffect(() => { api.portfolio().then(setSaved).catch(() => setSaved(null)); }, []);
  useEffect(() => { if (value && saved && !saved.configured) onChange(false); }, [saved?.configured]);
  useEffect(() => {
    if (!value || !saved?.configured || !ticker.trim()) { setPreview(null); return; }
    const timer = window.setTimeout(() => api.portfolioPreview(ticker.trim()).then(setPreview).catch(() => setPreview(null)), 350);
    return () => window.clearTimeout(timer);
  }, [value, ticker, saved?.configured]);
  const summary = saved?.configured ? `${saved.positions.length ? `${saved.positions.length} position${saved.positions.length === 1 ? "" : "s"}` : "Flat book"}${saved.cash !== null ? ` · ${formatNumber(saved.cash, 0)} ${saved.currency ?? ""} cash` : ""}` : "No holdings saved";
  return <><div className="portfolio-choice">
    <button className={!value ? "selected" : ""} onClick={() => onChange(false)}><span className="check">{!value && "✓"}</span><strong>No portfolio context</strong><small>Guidance is written for any reader, who applies it to their own position.</small></button>
    <button className={value ? "selected" : ""} disabled={!saved?.configured} onClick={() => onChange(true)}><span className="check">{value && "✓"}</span><strong>Decide against my book</strong><small>{saved?.configured ? `The trader, risk analysts and portfolio manager size against your holdings. ${summary}.` : "Save your holdings first to decide against them."}</small></button>
  </div>
    {!saved?.configured && <button className="link-button" onClick={onHoldings}><Wallet size={13} /> Set up holdings</button>}
    {value && saved?.configured && <div className="book-preview"><div><span>{backtest ? "The same standing book is given to every cell; positions are not carried between dates." : "Added to the decision agents' prompts:"}</span><button className="link-button" onClick={onHoldings}>Edit holdings</button></div><pre className="portfolio-context">{preview?.context ?? (ticker.trim() ? "…" : "Enter a ticker to preview what the agents read.")}</pre></div>}</>;
}

/** What a finished or running analysis was told about the book. */
export function BookCard({ context }: { context: string | null }) {
  return <div className="record-card"><span className="eyebrow">PORTFOLIO CONTEXT</span>{context ? <><p className="book-used"><Wallet size={13} /> Decided against your book as saved at launch</p><pre className="portfolio-context">{context}</pre></> : <p className="muted">No portfolio context: the guidance is written for any reader.</p>}</div>;
}
