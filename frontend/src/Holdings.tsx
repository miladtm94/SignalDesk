import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Briefcase, Check, Download, Eraser, Info, Plus, RefreshCw, Sparkles, Trash2, Undo2, Upload, Wallet } from "lucide-react";
import { api, Portfolio, PortfolioPreview, Quote, SavedPortfolio } from "./api";
import { formatDate, formatNumber, formatSignedPercent, formatTimestamp, localISODate, Metric, PageHeader, quoteCurrency } from "./shared";

type DraftPosition = { key: string; ticker: string; quantity: string; average_price: string; currency: string };
type Draft = { cash: string; currency: string; useByDefault: boolean; positions: DraftPosition[] };

let draftKey = 0;
const nextKey = () => `position-${++draftKey}`;

// A first book is used by default: holdings are entered to be decided against.
function toDraft(book: Portfolio | null, useByDefault = book?.use_by_default ?? true): Draft {
  return {
    cash: book?.cash === null || book?.cash === undefined ? "" : String(book.cash),
    currency: book?.currency ?? "",
    useByDefault,
    positions: (book?.positions ?? []).map((p) => ({ key: nextKey(), ticker: p.ticker, quantity: String(p.quantity), average_price: p.average_price === null ? "" : String(p.average_price), currency: p.currency ?? "" })),
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
    // An empty currency cell takes the exchange's, so every saved price says what it is in.
    const currency = (row.currency.trim() || quoteCurrency(ticker)).toUpperCase() || null;
    if (currency && !/^[A-Z0-9]{1,12}$/.test(currency)) return { book: null, problem: `${ticker}'s currency should be a code such as USD or AUD.` };
    positions.push({ ticker, quantity, average_price: price, currency });
  }
  return { book: { cash, currency: draft.currency.trim().toUpperCase() || null, positions, use_by_default: draft.useByDefault }, problem: null };
}

const comparable = (book: Portfolio | null) => book && { ...book, use_by_default: !!book.use_by_default, positions: book.positions.map((p) => ({ ...p, currency: p.currency ?? null })) };
const sameBook = (a: Portfolio | null, b: Portfolio | null) => JSON.stringify(comparable(a)) === JSON.stringify(comparable(b));
const asBook = (saved: SavedPortfolio | null): Portfolio | null => saved?.configured ? { cash: saved.cash, currency: saved.currency, positions: saved.positions, use_by_default: saved.use_by_default } : null;

/** A holdings file as Export writes it, or the upstream CLI's --portfolio file. */
function readBookFile(text: string): Portfolio {
  const data = JSON.parse(text);
  if (!data || typeof data !== "object" || !Array.isArray(data.positions)) throw new Error("That file has no positions list; use a file from Export holdings.");
  return {
    cash: typeof data.cash === "number" ? data.cash : null,
    currency: typeof data.currency === "string" ? data.currency : null,
    positions: data.positions.map((p: Record<string, unknown>) => ({ ticker: String(p.ticker ?? ""), quantity: Number(p.quantity), average_price: typeof p.average_price === "number" ? p.average_price : null, currency: typeof p.currency === "string" ? p.currency : null })),
  };
}

function downloadBook(book: Portfolio) {
  const file = { cash: book.cash, currency: book.currency, positions: book.positions.map(({ ticker, quantity, average_price, currency }) => ({ ticker, quantity, average_price, currency: currency ?? null })) };
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([JSON.stringify(file, null, 2) + "\n"], { type: "application/json" }));
  link.download = `holdings-${localISODate()}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

export function HoldingsView({ onAnalyze }: { onAnalyze: (ticker: string) => void }) {
  const [saved, setSaved] = useState<SavedPortfolio | null>(null), [draft, setDraft] = useState<Draft>(toDraft(null)), [quotes, setQuotes] = useState<Record<string, Quote>>({}), [quotesLoading, setQuotesLoading] = useState(false);
  const [error, setError] = useState(""), [message, setMessage] = useState(""), [saving, setSaving] = useState(false), [syncing, setSyncing] = useState(false), [ibkrNotes, setIbkrNotes] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const loadQuotes = (book: SavedPortfolio) => { if (!book.configured || !book.positions.length) { setQuotes({}); return; } setQuotesLoading(true); api.portfolioQuotes().then((items) => setQuotes(Object.fromEntries(items.map((q) => [q.ticker, q])))).catch(() => setQuotes({})).finally(() => setQuotesLoading(false)); };
  useEffect(() => { api.portfolio().then((book) => { setSaved(book); setDraft(toDraft(asBook(book), book.configured ? book.use_by_default : true)); loadQuotes(book); }).catch((e) => setError(e.message)); }, []);

  const { book: parsed, problem } = fromDraft(draft);
  // A saved book has changes when the draft reads differently; with none saved, anything typed counts.
  const dirty = saved?.configured ? !(parsed && sameBook(parsed, asBook(saved))) : draft.positions.length > 0 || draft.cash.trim() !== "" || draft.currency.trim() !== "";
  const missingCurrencies = !!saved?.configured && saved.positions.some((p) => !p.currency);
  const update = (key: string, field: keyof Omit<DraftPosition, "key">, value: string) => { setMessage(""); setDraft({ ...draft, positions: draft.positions.map((row) => row.key === key ? { ...row, [field]: field === "ticker" || field === "currency" ? value.toUpperCase() : value } : row) }); };
  const save = async () => { if (!parsed) return; setSaving(true); setError(""); try { const book = await api.savePortfolio(parsed); setSaved(book); setDraft(toDraft(asBook(book), book.use_by_default)); setMessage(book.positions.length ? `Holdings saved${book.use_by_default ? ": new analyses and backtests will decide against them" : ""}.` : "Saved as a flat book: no positions, so the agents size a new position from cash."); loadQuotes(book); } catch (e) { setError(e instanceof Error ? e.message : "Holdings could not be saved"); } finally { setSaving(false); } };
  const clear = async () => { if (!window.confirm("Forget the saved holdings? Runs will have no portfolio context until you save a book again.")) return; setError(""); try { await api.clearPortfolio(); const empty = { configured: false, cash: null, currency: null, positions: [], use_by_default: false, updated_at: null }; setSaved(empty); setDraft(toDraft(null)); setQuotes({}); setMessage("Holdings cleared. Runs now have no portfolio context."); } catch (e) { setError(e instanceof Error ? e.message : "Holdings could not be cleared"); } };
  // Reads the Flex Query into the draft only: the book changes when Save is pressed.
  const syncIbkr = async () => { setSyncing(true); setError(""); setMessage(""); setIbkrNotes([]); try { const book = await api.importIbkr(); setDraft(toDraft(book, draft.useByDefault)); setIbkrNotes(book.notes); setMessage(`Read ${book.positions.length} position${book.positions.length === 1 ? "" : "s"}${book.cash !== null ? ` and ${formatNumber(book.cash)} ${book.currency ?? ""} cash` : ""} from IBKR${book.account ? ` account ${book.account}` : ""}${book.as_of ? `, statement of ${formatDate(book.as_of)}` : ""}: review them, then press Save holdings.`); } catch (e) { setError(e instanceof Error ? e.message : "IBKR could not be read"); } finally { setSyncing(false); } };
  const importFile = (file: File | undefined) => { if (!file) return; file.text().then((text) => { const book = readBookFile(text); setDraft(toDraft(book, draft.useByDefault)); setError(""); setMessage(`Imported ${book.positions.length} position${book.positions.length === 1 ? "" : "s"} from ${file.name}: review them, then press Save holdings.`); }).catch((e) => setError(e instanceof SyntaxError ? "That file is not JSON." : e instanceof Error ? e.message : "That file could not be read.")).finally(() => { if (fileInput.current) fileInput.current.value = ""; }); };

  const longs = parsed?.positions.filter((p) => p.quantity > 0).length ?? 0, shorts = parsed?.positions.filter((p) => p.quantity < 0).length ?? 0;
  const currency = draft.currency.trim().toUpperCase();
  return <><PageHeader eyebrow="PORTFOLIO CONTEXT" title="Current holdings" copy="Tell the trader, risk analysts and portfolio manager what you actually hold, so they size against your book. Saved here, it is kept for every later session." action={<button className="primary-button" disabled={!saved?.configured} onClick={() => onAnalyze(saved?.positions[0]?.ticker ?? "")}><Sparkles size={16} /> Analyze with my book</button>} />
    <section className="metric-grid"><Metric label="Book" value={!saved ? "…" : saved.configured ? (saved.positions.length ? "Saved" : "Flat") : "None"} detail={!saved ? "Loading" : saved.configured ? `${saved.positions.length ? `${saved.positions.length} position${saved.positions.length === 1 ? "" : "s"}` : "Saved with no positions"} · ${saved.use_by_default ? "used by default" : "used when chosen"}` : "Runs have no portfolio context"} /><Metric label="Positions" value={String(parsed?.positions.length ?? 0).padStart(2, "0")} detail={`${longs} long · ${shorts} short`} /><Metric label="Free cash" value={parsed?.cash === null || parsed?.cash === undefined ? "—" : formatNumber(parsed.cash, 0)} detail={currency || "No currency label"} /><Metric label="Last saved" value={saved?.updated_at ? formatTimestamp(saved.updated_at).split(",")[0] : "—"} detail={saved?.updated_at ? formatTimestamp(saved.updated_at) : "Not saved yet"} /></section>
    <div className="holdings-layout">
      <section className="table-card holdings-card">
        <div className="table-toolbar"><div><h2>Positions</h2><span>Signed quantities: negative is short. Each average price is in its own currency, filled in from the ticker's exchange (NVDA in USD, NXT.AX in AUD).</span></div><div className="filter-actions"><button className="filter" disabled={syncing} onClick={syncIbkr} title="Read positions and cash from your IBKR Flex Query (add IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID in Settings)"><RefreshCw size={14} className={syncing ? "spin" : ""} /> {syncing ? "Reading IBKR…" : "Sync from IBKR"}</button><button className="filter" onClick={() => fileInput.current?.click()} title="Load holdings from a file saved with Export"><Upload size={14} /> Import</button><button className="filter" disabled={!saved?.configured || dirty} onClick={() => saved && downloadBook(asBook(saved)!)} title={dirty ? "Save your changes first" : "Download the saved holdings as a file"}><Download size={14} /> Export</button><button className="filter" onClick={() => { setMessage(""); setDraft({ ...draft, positions: [...draft.positions, { key: nextKey(), ticker: "", quantity: "", average_price: "", currency: "" }] }); }}><Plus size={14} /> Add position</button><input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={(e) => importFile(e.target.files?.[0])} /></div></div>
        {error && <div className="failure-banner inline"><AlertCircle size={16} /><div><strong>{syncing || error.includes("IBKR") || error.includes("Flex") ? "IBKR could not be read" : "Holdings were not saved"}</strong><span>{error}</span></div></div>}
        {ibkrNotes.length > 0 && <ul className="import-notes">{ibkrNotes.map((note) => <li key={note}><Info size={12} /> {note}</li>)}</ul>}
        {draft.positions.length ? <div className="run-table-wrap"><table className="run-table holdings-table"><thead><tr><th>Ticker</th><th>Quantity</th><th>Average price</th><th>Currency</th><th>Last close</th><th>Unrealized P/L</th><th></th></tr></thead><tbody>{draft.positions.map((row) => {
          const quote = quotes[row.ticker.trim().toUpperCase()], quantity = Number(row.quantity), average = Number(row.average_price), last = quote?.last_close ?? null;
          const unit = (row.currency.trim() || quoteCurrency(row.ticker)).toUpperCase();
          const pnl = last !== null && row.average_price && Number.isFinite(average) && Number.isFinite(quantity) ? (last - average) * quantity : null;
          const pct = pnl !== null && average > 0 ? (last! / average - 1) * Math.sign(quantity) : null;
          return <tr key={row.key}><td><input className="cell-input ticker-input" value={row.ticker} onChange={(e) => update(row.key, "ticker", e.target.value)} placeholder="NVDA" aria-label="Ticker" /></td><td><input className="cell-input" inputMode="decimal" value={row.quantity} onChange={(e) => update(row.key, "quantity", e.target.value)} placeholder="120 or -50" aria-label="Quantity" />{quantity < 0 && <small className="short-tag">Short</small>}</td><td><input className="cell-input" inputMode="decimal" value={row.average_price} onChange={(e) => update(row.key, "average_price", e.target.value)} placeholder="Optional" aria-label="Average price" /></td><td><input className="cell-input currency-input" value={row.currency} maxLength={12} onChange={(e) => update(row.key, "currency", e.target.value)} placeholder={quoteCurrency(row.ticker) || "USD"} aria-label={`Currency of ${row.ticker || "the"} average price`} title="The currency of the average price; empty uses the exchange's" /></td><td>{quotesLoading ? <span className="muted">Loading…</span> : last !== null ? <><strong>{formatNumber(last, 4)} <small className="unit">{unit}</small></strong><small>{quote?.as_of}</small></> : <span className="muted">{quote?.error ?? (dirty ? "Save to load" : "—")}</span>}</td><td>{pnl !== null ? <span className={pnl >= 0 ? "pnl positive" : "pnl negative"}>{pnl >= 0 ? "+" : ""}{formatNumber(pnl, 2)} {unit} <small>{formatSignedPercent(pct)}</small></span> : <span className="muted">—</span>}</td><td className="row-tools"><button className="row-action" disabled={!row.ticker.trim() || dirty} title={dirty ? "Save your changes first" : `Analyze ${row.ticker} against this book`} onClick={() => onAnalyze(row.ticker.trim().toUpperCase())}><Sparkles size={12} /> Analyze</button><button className="remove-run" onClick={() => { setMessage(""); setDraft({ ...draft, positions: draft.positions.filter((item) => item.key !== row.key) }); }} aria-label={`Remove ${row.ticker || "position"}`}><Trash2 size={13} /></button></td></tr>;
        })}</tbody></table></div> : <div className="holdings-empty"><Briefcase size={20} /><strong>{saved?.configured ? "A flat book" : "No positions"}</strong><span>{saved?.configured ? "The book is saved with no positions: the agents know you hold nothing and size any position from cash." : "Add what you hold, or Import a holdings file exported from another console. You can also save only your cash as a flat book. Without a saved book, runs carry no portfolio context at all."}</span></div>}
        <div className="holdings-footer">
          <label><span>Free cash</span><input inputMode="decimal" value={draft.cash} onChange={(e) => { setMessage(""); setDraft({ ...draft, cash: e.target.value }); }} placeholder="e.g. 25000" /></label>
          <label><span>Cash currency</span><input value={draft.currency} maxLength={12} onChange={(e) => { setMessage(""); setDraft({ ...draft, currency: e.target.value.toUpperCase() }); }} placeholder="e.g. AUD" /></label>
          <label className="default-toggle"><input type="checkbox" checked={draft.useByDefault} onChange={(e) => { setMessage(""); setDraft({ ...draft, useByDefault: e.target.checked }); }} /><span><strong>Use by default</strong><small>New analyses and backtests start on deciding against this book; you can still switch it off per run.</small></span></label>
          <div className="holdings-actions">{problem ? <span className="form-problem"><AlertCircle size={13} /> {problem}</span> : message ? <span className="form-ok"><Check size={13} /> {message}</span> : dirty ? <span className="form-hint">{missingCurrencies ? "Currencies filled in from each exchange: save to keep them" : "Unsaved changes"}</span> : null}<button className="secondary-button" disabled={!dirty} onClick={() => { setDraft(toDraft(asBook(saved), saved?.configured ? saved.use_by_default : true)); setMessage(""); }}><Undo2 size={14} /> Discard</button><button className="primary-button" disabled={!saved || !!problem || saving || (!dirty && saved.configured)} onClick={save}>{saving ? <RefreshCw className="spin" size={14} /> : <Wallet size={14} />} {saved?.configured ? "Save holdings" : parsed?.positions.length ? "Save holdings" : "Save as flat book"}</button></div>
        </div>
      </section>
      <aside className="run-aside">
        <AgentView saved={saved} />
        <div className="record-card book-rules"><span className="eyebrow">HOW THE BOOK IS USED</span><ul><li><strong>Saved for later sessions.</strong> The book stays in this console's data folder until you change or forget it. Each console (Docker or <code>make web-dev</code>) keeps its own: use Export and Import to move it.</li><li><strong>Your choice per run.</strong> With <em>Use by default</em> on, the launch forms start on deciding against your book; each run records the exact book it was given.</li><li><strong>A flat book is not "no book".</strong> Saved with no positions, the agents know you hold nothing. With no book saved, they write guidance for any reader.</li><li><strong>Who reads it.</strong> The trader, the three risk analysts and the portfolio manager, with each average price's currency. Analysts and researchers stay position-neutral.</li><li><strong>Broker-neutral, read-only.</strong> Quantities are units and currencies are labels: nothing here can place an order. <em>Sync from IBKR</em> reads your Flex Query (Open Positions and Cash Report sections) with a read-only Flex token saved in Settings, and loads it here for review.</li></ul>{saved?.configured && <button className="clear-book" onClick={clear}><Eraser size={13} /> Forget saved holdings</button>}</div>
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
  // Until the person picks, the saved book's "use by default" sets the choice.
  const touched = useRef(false), choose = (next: boolean) => { touched.current = true; onChange(next); };
  useEffect(() => { api.portfolio().then(setSaved).catch(() => setSaved(null)); }, []);
  useEffect(() => {
    if (!saved) return;
    if (value && !saved.configured) onChange(false);
    else if (!value && !touched.current && saved.configured && saved.use_by_default) onChange(true);
  }, [saved?.configured, saved?.use_by_default]);
  useEffect(() => {
    if (!value || !saved?.configured || !ticker.trim()) { setPreview(null); return; }
    const timer = window.setTimeout(() => api.portfolioPreview(ticker.trim()).then(setPreview).catch(() => setPreview(null)), 350);
    return () => window.clearTimeout(timer);
  }, [value, ticker, saved?.configured]);
  const summary = saved?.configured ? `${saved.positions.length ? `${saved.positions.length} position${saved.positions.length === 1 ? "" : "s"}` : "Flat book"}${saved.cash !== null ? ` · ${formatNumber(saved.cash, 0)} ${saved.currency ?? ""} cash` : ""}` : "No holdings saved";
  return <><div className="portfolio-choice">
    <button className={!value ? "selected" : ""} onClick={() => choose(false)}><span className="check">{!value && "✓"}</span><strong>No portfolio context</strong><small>Guidance is written for any reader, who applies it to their own position.</small></button>
    <button className={value ? "selected" : ""} disabled={!saved?.configured} onClick={() => choose(true)}><span className="check">{value && "✓"}</span><strong>Decide against my book</strong><small>{saved?.configured ? `The trader, risk analysts and portfolio manager size against your holdings. ${summary}.` : "Save your holdings first to decide against them."}</small></button>
  </div>
    {!saved?.configured && <button className="link-button" onClick={onHoldings}><Wallet size={13} /> Set up holdings</button>}
    {value && saved?.configured && <div className="book-preview"><div><span>{backtest ? "The same standing book is given to every cell; positions are not carried between dates." : "Added to the decision agents' prompts:"}</span><button className="link-button" onClick={onHoldings}>Edit holdings</button></div><pre className="portfolio-context">{preview?.context ?? (ticker.trim() ? "…" : "Enter a ticker to preview what the agents read.")}</pre></div>}</>;
}

/** What a finished or running analysis was told about the book. */
export function BookCard({ context }: { context: string | null }) {
  return <div className="record-card"><span className="eyebrow">PORTFOLIO CONTEXT</span>{context ? <><p className="book-used"><Wallet size={13} /> Decided against your book as saved at launch</p><pre className="portfolio-context">{context}</pre></> : <p className="muted">No portfolio context: the guidance is written for any reader.</p>}</div>;
}
