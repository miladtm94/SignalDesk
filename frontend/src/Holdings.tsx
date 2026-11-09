import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Briefcase, Check, Download, Eraser, Info, Plus, RefreshCw, Sparkles, Trash2, Undo2, Upload, Wallet } from "lucide-react";
import { api, CashBalance, IbkrBook, Portfolio, PortfolioPreview, Position, Quote, SavedPortfolio } from "./api";
import { formatDate, formatNumber, formatSignedPercent, formatTimestamp, localISODate, Metric, PageHeader, quoteCurrency } from "./shared";

type DraftPosition = { key: string; ticker: string; quantity: string; average_price: string; currency: string; account: string };
type DraftCash = { key: string; account: string; amount: string; currency: string };
type Draft = { currency: string; useByDefault: boolean; positions: DraftPosition[]; cash: DraftCash[] };

let draftKey = 0;
const nextKey = () => `position-${++draftKey}`;
const CODE = /^[A-Z0-9]{1,12}$/;
const IBKR = "IBKR";
const atIbkr = (account: string) => account.trim().toUpperCase() === IBKR;

const toDraftPosition = (p: Position): DraftPosition => ({ key: nextKey(), ticker: p.ticker, quantity: String(p.quantity), average_price: p.average_price === null ? "" : String(p.average_price), currency: p.currency ?? "", account: p.account ?? "" });
const toDraftCash = (b: CashBalance): DraftCash => ({ key: nextKey(), account: b.account ?? "", amount: String(b.amount), currency: b.currency ?? "" });

// A first book is used by default: holdings are entered to be decided against.
function toDraft(book: Portfolio | null, useByDefault = book?.use_by_default ?? true): Draft {
  // A book saved before accounts has one cash figure: it becomes one cash row without an account.
  const balances: CashBalance[] = book?.cash_balances?.length ? book.cash_balances : book?.cash === null || book?.cash === undefined ? [] : [{ account: null, amount: book.cash, currency: book.currency }];
  return { currency: book?.currency ?? "", useByDefault, positions: (book?.positions ?? []).map(toDraftPosition), cash: balances.map(toDraftCash) };
}

/** The draft as the API takes it, or the first problem that stops it being saved. */
function fromDraft(draft: Draft): { book: Portfolio | null; problem: string | null } {
  const fail = (problem: string) => ({ book: null, problem });
  const bookCurrency = draft.currency.trim().toUpperCase();
  if (bookCurrency && !CODE.test(bookCurrency)) return fail("The book currency should be a code such as AUD or USD.");
  const balances: CashBalance[] = [];
  for (const row of draft.cash) {
    const label = row.account.trim() || "Cash without an account";
    const amount = Number(row.amount);
    if (!row.amount.trim()) return fail(`${label} needs an amount, or remove that cash row.`);
    if (!Number.isFinite(amount) || amount < 0) return fail(`${label}: cash must be a number of zero or more.`);
    const currency = (row.currency.trim() || bookCurrency).toUpperCase() || null;
    if (currency && !CODE.test(currency)) return fail(`${label}: the cash currency should be a code such as AUD.`);
    balances.push({ account: row.account.trim() || null, amount, currency });
  }
  const currencies = [...new Set(balances.map((b) => b.currency).filter((c): c is string => !!c))];
  if (!bookCurrency && currencies.length > 1) return fail(`Cash is held in ${currencies.join(" and ")}: name the book currency to total it in.`);
  const currency = bookCurrency || (currencies.length === 1 ? currencies[0] : null);
  const seen = new Set<string>(), positions: Position[] = [];
  for (const row of draft.positions) {
    const ticker = row.ticker.trim().toUpperCase(), account = row.account.trim();
    if (!ticker || /\s/.test(ticker)) return fail("Every position needs a compact ticker, e.g. NVDA or 0700.HK.");
    // The same instrument may be held at two brokers, but once per account.
    const key = `${ticker}|${account.toLowerCase()}`;
    if (seen.has(key)) return fail(`${ticker} is listed twice${account ? ` in ${account}` : ""}; combine it into one position.`);
    seen.add(key);
    const quantity = Number(row.quantity);
    if (!row.quantity.trim() || !Number.isFinite(quantity) || quantity === 0) return fail(`${ticker} needs a non-zero quantity (negative for a short).`);
    const price = row.average_price.trim() === "" ? null : Number(row.average_price);
    if (price !== null && (!Number.isFinite(price) || price <= 0)) return fail(`${ticker}'s average price must be above zero, or left empty.`);
    // An empty currency cell takes the exchange's, so every saved price says what it is in.
    const priceCurrency = (row.currency.trim() || quoteCurrency(ticker)).toUpperCase() || null;
    if (priceCurrency && !CODE.test(priceCurrency)) return fail(`${ticker}'s currency should be a code such as USD or AUD.`);
    positions.push({ ticker, quantity, average_price: price, currency: priceCurrency, account: account || null });
  }
  // One cash figure in the book's currency with no account is the plain book it always was.
  const plain = balances.length === 1 && !balances[0].account && balances[0].currency === currency;
  // Cash in other currencies is totalled on save, at the day's exchange rate.
  const cash = !balances.length ? null : balances.every((b) => b.currency === currency) ? Math.round(balances.reduce((sum, b) => sum + b.amount, 0) * 100) / 100 : null;
  return { book: { cash, currency, positions, cash_balances: plain ? [] : balances, use_by_default: draft.useByDefault }, problem: null };
}

const comparable = (book: Portfolio | null) => book && {
  currency: book.currency ?? null, use_by_default: !!book.use_by_default,
  positions: book.positions.map((p) => ({ ticker: p.ticker, quantity: p.quantity, average_price: p.average_price, currency: p.currency ?? null, account: p.account ?? null })),
  // With balances, the total is worked out on save; the balances are what was entered.
  cash: book.cash_balances?.length ? null : book.cash,
  cash_balances: (book.cash_balances ?? []).map((b) => ({ account: b.account ?? null, amount: b.amount, currency: b.currency ?? null })),
};
const sameBook = (a: Portfolio | null, b: Portfolio | null) => JSON.stringify(comparable(a)) === JSON.stringify(comparable(b));
const asBook = (saved: SavedPortfolio | null): Portfolio | null => saved?.configured ? { cash: saved.cash, currency: saved.currency, positions: saved.positions, cash_balances: saved.cash_balances ?? [], use_by_default: saved.use_by_default } : null;

/** IBKR's positions and cash in place of the draft's IBKR rows; every other account's rows stay. */
function mergeIbkr(draft: Draft, book: IbkrBook): { draft: Draft; notes: string[] } {
  const listed = new Set(book.positions.map((p) => p.ticker.toUpperCase())), notes: string[] = [];
  // A row with no account whose ticker IBKR lists is the same holding, read by a sync before accounts.
  const replaced = draft.positions.filter((row) => !row.account.trim() && listed.has(row.ticker.trim().toUpperCase()));
  const kept = draft.positions.filter((row) => !atIbkr(row.account) && !replaced.includes(row));
  if (replaced.length) notes.push(`Replaced ${replaced.length} position${replaced.length === 1 ? "" : "s"} with no account by IBKR's (${replaced.map((row) => row.ticker.trim().toUpperCase()).join(", ")}). Give rows from other brokers an account, e.g. Betashares, so a sync never touches them.`);
  const ibkrCash = book.cash_balances ?? [];
  let keptCash = draft.cash.filter((row) => !atIbkr(row.account));
  if (ibkrCash.length && !draft.cash.some((row) => atIbkr(row.account))) {
    const currency = (ibkrCash[0].currency ?? "").toUpperCase();
    const earlier = keptCash.filter((row) => !row.account.trim() && (row.currency.trim() || draft.currency).toUpperCase() === currency);
    if (earlier.length === 1) {
      keptCash = keptCash.filter((row) => row !== earlier[0]);
      notes.push(`Replaced the ${earlier[0].amount} ${currency} cash with no account by IBKR's cash.`);
    }
  }
  // Unmarked and no longer listed: sold at IBKR since the last sync, or held elsewhere.
  const gone = kept.filter((row) => !row.account.trim()).map((row) => row.ticker.trim().toUpperCase()).filter(Boolean);
  if (gone.length) notes.push(`${gone.join(", ")} ${gone.length === 1 ? "has" : "have"} no account and IBKR does not list ${gone.length === 1 ? "it" : "them"}: remove ${gone.length === 1 ? "it" : "them"} if sold at IBKR, or name the account ${gone.length === 1 ? "it is" : "they are"} held at.`);
  if (kept.length || keptCash.length) notes.push(`Kept ${kept.length} position${kept.length === 1 ? "" : "s"} and ${keptCash.length} cash balance${keptCash.length === 1 ? "" : "s"} from other accounts.`);
  return { draft: { ...draft, currency: draft.currency || book.currency || "", positions: [...book.positions.map(toDraftPosition), ...kept], cash: [...ibkrCash.map(toDraftCash), ...keptCash] }, notes };
}

/** A holdings file as Export writes it, or the upstream CLI's --portfolio file. */
function readBookFile(text: string): Portfolio {
  const data = JSON.parse(text);
  if (!data || typeof data !== "object" || !Array.isArray(data.positions)) throw new Error("That file has no positions list; use a file from Export holdings.");
  const asText = (value: unknown) => typeof value === "string" ? value : null;
  return {
    cash: typeof data.cash === "number" ? data.cash : null,
    currency: asText(data.currency),
    positions: data.positions.map((p: Record<string, unknown>) => ({ ticker: String(p.ticker ?? ""), quantity: Number(p.quantity), average_price: typeof p.average_price === "number" ? p.average_price : null, currency: asText(p.currency), account: asText(p.account) })),
    cash_balances: Array.isArray(data.cash_balances) ? data.cash_balances.map((b: Record<string, unknown>) => ({ account: asText(b.account), amount: Number(b.amount), currency: asText(b.currency) })) : [],
  };
}

function downloadBook(book: Portfolio) {
  const file = { cash: book.cash, currency: book.currency, positions: book.positions.map(({ ticker, quantity, average_price, currency, account }) => ({ ticker, quantity, average_price, currency: currency ?? null, ...(account ? { account } : {}) })), ...(book.cash_balances?.length ? { cash_balances: book.cash_balances.map(({ account, amount, currency }) => ({ account, amount, currency })) } : {}) };
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
  const dirty = saved?.configured ? !(parsed && sameBook(parsed, asBook(saved))) : draft.positions.length > 0 || draft.cash.length > 0 || draft.currency.trim() !== "";
  const missingCurrencies = !!saved?.configured && saved.positions.some((p) => !p.currency);
  const update = (key: string, field: keyof Omit<DraftPosition, "key">, value: string) => { setMessage(""); setDraft({ ...draft, positions: draft.positions.map((row) => row.key === key ? { ...row, [field]: field === "ticker" || field === "currency" ? value.toUpperCase() : value } : row) }); };
  const save = async () => { if (!parsed) return; setSaving(true); setError(""); try { const book = await api.savePortfolio(parsed); setSaved(book); setDraft(toDraft(asBook(book), book.use_by_default)); setMessage(book.positions.length ? `Holdings saved${book.use_by_default ? ": new analyses and backtests will decide against them" : ""}.` : "Saved as a flat book: no positions, so the agents size a new position from cash."); loadQuotes(book); } catch (e) { setError(e instanceof Error ? e.message : "Holdings could not be saved"); } finally { setSaving(false); } };
  const clear = async () => { if (!window.confirm("Forget the saved holdings? Runs will have no portfolio context until you save a book again.")) return; setError(""); try { await api.clearPortfolio(); const empty = { configured: false, cash: null, currency: null, positions: [], use_by_default: false, updated_at: null }; setSaved(empty); setDraft(toDraft(null)); setQuotes({}); setMessage("Holdings cleared. Runs now have no portfolio context."); } catch (e) { setError(e instanceof Error ? e.message : "Holdings could not be cleared"); } };
  // Reads the Flex Query into the draft only: the book changes when Save is pressed.
  const syncIbkr = async () => { setSyncing(true); setError(""); setMessage(""); setIbkrNotes([]); try { const book = await api.importIbkr(); const merged = mergeIbkr(draft, book); setDraft(merged.draft); setIbkrNotes([...book.notes, ...merged.notes]); setMessage(`Read ${book.positions.length} position${book.positions.length === 1 ? "" : "s"}${book.cash !== null ? ` and ${formatNumber(book.cash)} ${book.currency ?? ""} cash` : ""} from IBKR${book.account ? ` account ${book.account}` : ""}${book.as_of ? `, statement of ${formatDate(book.as_of)}` : ""}: review them, then press Save holdings.`); } catch (e) { setError(e instanceof Error ? e.message : "IBKR could not be read"); } finally { setSyncing(false); } };
  const importFile = (file: File | undefined) => { if (!file) return; file.text().then((text) => { const book = readBookFile(text); setDraft(toDraft(book, draft.useByDefault)); setError(""); setMessage(`Imported ${book.positions.length} position${book.positions.length === 1 ? "" : "s"} from ${file.name}: review them, then press Save holdings.`); }).catch((e) => setError(e instanceof SyntaxError ? "That file is not JSON." : e instanceof Error ? e.message : "That file could not be read.")).finally(() => { if (fileInput.current) fileInput.current.value = ""; }); };

  const longs = parsed?.positions.filter((p) => p.quantity > 0).length ?? 0, shorts = parsed?.positions.filter((p) => p.quantity < 0).length ?? 0;
  const currency = draft.currency.trim().toUpperCase();
  const accounts = [...new Set([IBKR, ...draft.positions.map((row) => row.account.trim()), ...draft.cash.map((row) => row.account.trim())].filter(Boolean))];
  // Cash in several currencies is totalled on save; until then the saved total stands.
  const freeCash = parsed?.cash ?? (!dirty && saved?.configured ? saved.cash : null);
  const totalLater = freeCash === null && !!parsed?.cash_balances?.length;
  const updateCash = (key: string, field: keyof Omit<DraftCash, "key">, value: string) => { setMessage(""); setDraft({ ...draft, cash: draft.cash.map((row) => row.key === key ? { ...row, [field]: field === "currency" ? value.toUpperCase() : value } : row) }); };
  return <><PageHeader eyebrow="PORTFOLIO CONTEXT" title="Current holdings" copy="Tell the trader, risk analysts and portfolio manager what you actually hold, so they size against your book. Saved here, it is kept for every later session." action={<button className="primary-button" disabled={!saved?.configured} onClick={() => onAnalyze(saved?.positions[0]?.ticker ?? "")}><Sparkles size={16} /> Analyze with my book</button>} />
    <section className="metric-grid"><Metric label="Book" value={!saved ? "…" : saved.configured ? (saved.positions.length ? "Saved" : "Flat") : "None"} detail={!saved ? "Loading" : saved.configured ? `${saved.positions.length ? `${saved.positions.length} position${saved.positions.length === 1 ? "" : "s"}` : "Saved with no positions"} · ${saved.use_by_default ? "used by default" : "used when chosen"}` : "Runs have no portfolio context"} /><Metric label="Positions" value={String(parsed?.positions.length ?? 0).padStart(2, "0")} detail={`${longs} long · ${shorts} short`} /><Metric label="Free cash" value={freeCash === null ? "—" : formatNumber(freeCash, 0)} detail={totalLater ? `Totalled in ${currency || "the book currency"} on save` : `${currency || "No currency label"}${(parsed?.cash_balances?.length ?? 0) > 1 ? ` · ${parsed!.cash_balances!.length} accounts` : ""}`} /><Metric label="Last saved" value={saved?.updated_at ? formatTimestamp(saved.updated_at).split(",")[0] : "—"} detail={saved?.updated_at ? formatTimestamp(saved.updated_at) : "Not saved yet"} /></section>
    <div className="holdings-layout">
      <section className="table-card holdings-card">
        <div className="table-toolbar"><div><h2>Positions</h2><span>Signed quantities: negative is short. Each average price is in its own currency, filled in from the ticker's exchange (NVDA in USD, NXT.AX in AUD).</span></div><div className="filter-actions"><button className="filter" disabled={syncing} onClick={syncIbkr} title="Read positions and cash from your IBKR Flex Query (add IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID in Settings)"><RefreshCw size={14} className={syncing ? "spin" : ""} /> {syncing ? "Reading IBKR…" : "Sync from IBKR"}</button><button className="filter" onClick={() => fileInput.current?.click()} title="Load holdings from a file saved with Export"><Upload size={14} /> Import</button><button className="filter" disabled={!saved?.configured || dirty} onClick={() => saved && downloadBook(asBook(saved)!)} title={dirty ? "Save your changes first" : "Download the saved holdings as a file"}><Download size={14} /> Export</button><button className="filter" onClick={() => { setMessage(""); setDraft({ ...draft, positions: [...draft.positions, { key: nextKey(), ticker: "", quantity: "", average_price: "", currency: "", account: "" }] }); }}><Plus size={14} /> Add position</button><input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={(e) => importFile(e.target.files?.[0])} /></div></div>
        {error && <div className="failure-banner inline"><AlertCircle size={16} /><div><strong>{syncing || error.includes("IBKR") || error.includes("Flex") ? "IBKR could not be read" : "Holdings were not saved"}</strong><span>{error}</span></div></div>}
        {ibkrNotes.length > 0 && <ul className="import-notes">{ibkrNotes.map((note) => <li key={note}><Info size={12} /> {note}</li>)}</ul>}
        {draft.positions.length ? <div className="run-table-wrap"><table className="run-table holdings-table"><thead><tr><th>Ticker</th><th>Quantity</th><th>Average price</th><th>Currency</th><th>Account</th><th>Last close</th><th>Unrealized P/L</th><th></th></tr></thead><tbody>{draft.positions.map((row) => {
          const quote = quotes[row.ticker.trim().toUpperCase()], quantity = Number(row.quantity), average = Number(row.average_price), last = quote?.last_close ?? null;
          const unit = (row.currency.trim() || quoteCurrency(row.ticker)).toUpperCase();
          const pnl = last !== null && row.average_price && Number.isFinite(average) && Number.isFinite(quantity) ? (last - average) * quantity : null;
          const pct = pnl !== null && average > 0 ? (last! / average - 1) * Math.sign(quantity) : null;
          return <tr key={row.key}><td><input className="cell-input ticker-input" value={row.ticker} onChange={(e) => update(row.key, "ticker", e.target.value)} placeholder="NVDA" aria-label="Ticker" /></td><td><input className="cell-input" inputMode="decimal" value={row.quantity} onChange={(e) => update(row.key, "quantity", e.target.value)} placeholder="120 or -50" aria-label="Quantity" />{quantity < 0 && <small className="short-tag">Short</small>}</td><td><input className="cell-input" inputMode="decimal" value={row.average_price} onChange={(e) => update(row.key, "average_price", e.target.value)} placeholder="Optional" aria-label="Average price" /></td><td><input className="cell-input currency-input" value={row.currency} maxLength={12} onChange={(e) => update(row.key, "currency", e.target.value)} placeholder={quoteCurrency(row.ticker) || "USD"} aria-label={`Currency of ${row.ticker || "the"} average price`} title="The currency of the average price; empty uses the exchange's" /></td><td><input className="cell-input account-input" list="book-accounts" value={row.account} maxLength={40} onChange={(e) => update(row.key, "account", e.target.value)} placeholder="Manual" aria-label={`Account holding ${row.ticker || "the position"}`} title={atIbkr(row.account) ? "Read from IBKR: Sync from IBKR replaces it" : "Where it is held, e.g. Betashares. Sync from IBKR replaces only IBKR's rows."} /></td><td>{quotesLoading ? <span className="muted">Loading…</span> : last !== null ? <><strong>{formatNumber(last, 4)} <small className="unit">{unit}</small></strong><small>{quote?.as_of}</small></> : <span className="muted">{quote?.error ?? (dirty ? "Save to load" : "—")}</span>}</td><td>{pnl !== null ? <span className={pnl >= 0 ? "pnl positive" : "pnl negative"}>{pnl >= 0 ? "+" : ""}{formatNumber(pnl, 2)} {unit} <small>{formatSignedPercent(pct)}</small></span> : <span className="muted">—</span>}</td><td className="row-tools"><button className="row-action" disabled={!row.ticker.trim() || dirty} title={dirty ? "Save your changes first" : `Analyze ${row.ticker} against this book`} onClick={() => onAnalyze(row.ticker.trim().toUpperCase())}><Sparkles size={12} /> Analyze</button><button className="remove-run" onClick={() => { setMessage(""); setDraft({ ...draft, positions: draft.positions.filter((item) => item.key !== row.key) }); }} aria-label={`Remove ${row.ticker || "position"}`}><Trash2 size={13} /></button></td></tr>;
        })}</tbody></table></div> : <div className="holdings-empty"><Briefcase size={20} /><strong>{saved?.configured ? "A flat book" : "No positions"}</strong><span>{saved?.configured ? "The book is saved with no positions: the agents know you hold nothing and size any position from cash." : "Add what you hold, or Import a holdings file exported from another console. You can also save only your cash as a flat book. Without a saved book, runs carry no portfolio context at all."}</span></div>}
        <datalist id="book-accounts">{accounts.map((account) => <option key={account} value={account} />)}</datalist>
        <div className="holdings-footer">
          <div className="cash-editor"><div className="cash-head"><span>Free cash by account</span><button className="filter" onClick={() => { setMessage(""); setDraft({ ...draft, cash: [...draft.cash, { key: nextKey(), account: "", amount: "", currency: "" }] }); }}><Plus size={13} /> Add cash</button></div>
            {draft.cash.length ? draft.cash.map((row) => <div className="cash-row" key={row.key}><input className="cell-input" list="book-accounts" value={row.account} maxLength={40} onChange={(e) => updateCash(row.key, "account", e.target.value)} placeholder="Account, e.g. Betashares" aria-label="Cash account" /><input className="cell-input" inputMode="decimal" value={row.amount} onChange={(e) => updateCash(row.key, "amount", e.target.value)} placeholder="e.g. 25000" aria-label={`Cash in ${row.account || "this account"}`} /><input className="cell-input currency-input" value={row.currency} maxLength={12} onChange={(e) => updateCash(row.key, "currency", e.target.value)} placeholder={currency || "AUD"} aria-label={`Currency of the cash in ${row.account || "this account"}`} title="Empty is the book currency" /><button className="remove-run" onClick={() => { setMessage(""); setDraft({ ...draft, cash: draft.cash.filter((item) => item.key !== row.key) }); }} aria-label={`Remove the cash in ${row.account || "this account"}`}><Trash2 size={13} /></button></div>) : <small className="cash-empty">No cash entered: the agents are told nothing about your cash.</small>}
            {saved?.cash_balances?.some((b) => b.rate) && !dirty && <small className="cash-empty">Totalled at {saved.cash_balances.filter((b) => b.rate).map((b) => `${formatNumber(b.rate!, 4)} ${saved.currency} per ${b.currency}`).join(", ")}, the rate when saved.</small>}
          </div>
          <label><span>Book currency</span><input value={draft.currency} maxLength={12} onChange={(e) => { setMessage(""); setDraft({ ...draft, currency: e.target.value.toUpperCase() }); }} placeholder="e.g. AUD" title="Cash in every account is totalled in this currency" /></label>
          <label className="default-toggle"><input type="checkbox" checked={draft.useByDefault} onChange={(e) => { setMessage(""); setDraft({ ...draft, useByDefault: e.target.checked }); }} /><span><strong>Use by default</strong><small>New analyses and backtests start on deciding against this book; you can still switch it off per run.</small></span></label>
          <div className="holdings-actions">{problem ? <span className="form-problem"><AlertCircle size={13} /> {problem}</span> : message ? <span className="form-ok"><Check size={13} /> {message}</span> : dirty ? <span className="form-hint">{missingCurrencies ? "Currencies filled in from each exchange: save to keep them" : "Unsaved changes"}</span> : null}<button className="secondary-button" disabled={!dirty} onClick={() => { setDraft(toDraft(asBook(saved), saved?.configured ? saved.use_by_default : true)); setMessage(""); }}><Undo2 size={14} /> Discard</button><button className="primary-button" disabled={!saved || !!problem || saving || (!dirty && saved.configured)} onClick={save}>{saving ? <RefreshCw className="spin" size={14} /> : <Wallet size={14} />} {saved?.configured ? "Save holdings" : parsed?.positions.length ? "Save holdings" : "Save as flat book"}</button></div>
        </div>
      </section>
      <aside className="run-aside">
        <AgentView saved={saved} />
        <div className="record-card book-rules"><span className="eyebrow">HOW THE BOOK IS USED</span><ul><li><strong>Saved for later sessions.</strong> The book stays in this console's data folder until you change or forget it. Each console (Docker or <code>make web-dev</code>) keeps its own: use Export and Import to move it.</li><li><strong>Your choice per run.</strong> With <em>Use by default</em> on, the launch forms start on deciding against your book; each run records the exact book it was given.</li><li><strong>A flat book is not "no book".</strong> Saved with no positions, the agents know you hold nothing. With no book saved, they write guidance for any reader.</li><li><strong>Who reads it.</strong> The trader, the three risk analysts and the portfolio manager, with each average price's currency. Analysts and researchers stay position-neutral.</li><li><strong>Broker-neutral, read-only.</strong> Quantities are units and currencies are labels: nothing here can place an order. <em>Sync from IBKR</em> reads your Flex Query (Open Positions and Cash Report sections) with a read-only Flex token saved in Settings, and loads it here for review.</li><li><strong>Several accounts.</strong> Name each row's account (IBKR, Betashares…). A sync replaces only the IBKR rows and IBKR's cash; everything else is kept. Cash in other currencies is totalled in the book currency at the day's rate.</li></ul>{saved?.configured && <button className="clear-book" onClick={clear}><Eraser size={13} /> Forget saved holdings</button>}</div>
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
