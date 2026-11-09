# Known issues, open questions and next improvements (as of 2026-11-09)

Verify an item still applies before acting on it; strike it here when fixed.

## Known issues and limits

1. **Structured decision fields are mostly empty.** Upstream renders the Trader's
   and Research Manager's typed outputs to markdown before they reach state, so
   `decisions.action/entry_price/stop_loss/price_target/time_horizon/confidence`
   stay null (the dashboard's "Trader action" shows "—"). Only the PM rating is
   typed (`final_rating`). The chart's levels come from explicit labels in the
   markdown (`services/charts.py`, display only), named for the rating's side and
   dropped when on the wrong side of the entry. Proper fix: an upstream change
   that keeps `*_structured` state values; the adapter already reads them.
2. **Plan-limit wording is unverified.** `claude_code._LIMIT` was written from
   known Claude Code messages; no real limit has been hit yet. When one happens,
   confirm the run fails as `ClaudeUsageLimit` and a backtest stops with
   "Resume once it resets".
3. **`claude auth status` does not validate `CLAUDE_CODE_OAUTH_TOKEN`.** In Docker
   a dummy token reported "signed in"; a bad token only shows at the first call
   (as a sign-in error). Fine, but the Settings badge can be optimistic.
4. **FastAPI is pinned at 0.115.6**, so Starlette must stay < 0.42. Anything that
   wants Starlette 1.x (the Agent SDK's newest `mcp` does) must be resolved in
   the same pip run. Upgrading FastAPI/uvicorn (and re-running the console suite)
   would remove the trap.
5. **The user's Docker console runs pre-v0.7 code** until rebuilt
   (`docker compose up --build web-backend web-frontend`; add
   `WITH_CLAUDE_CODE=1` and a `CLAUDE_CODE_OAUTH_TOKEN` for the Claude plan).
6. **Backtests run one cell at a time**, and each cell is a full run (2–5 min).
   Capped at 60 cells. No export, no comparison between sweeps.
7. **Holdings is a single book per console** (Docker and `make web-dev` keep
   separate databases; Export/Import moves it). Each position has a currency;
   P/L is per instrument in that currency (no FX conversion, no totals); quotes
   are fetched one ticker at a time (12 holdings take ~20 s).
8. **Usage cost is a midpoint estimate** across quick/deep models (callbacks do
   not say which tier made a call).
9. **The ChatGPT plan's models** are "Custom model ID" only in the console; the
   CLI lists them live with `chatgpt_auth.list_models()`.
10. Cosmetic: no favicon (a 404 on every page load); `pytest-asyncio` warns
    about the unset fixture loop scope in `backend/tests`.
11. Untested: the Claude provider on Windows (code paths exist: `claude.exe`,
    process groups); an in-container `tradingagents login claude` (the token
    route is the documented one).
12. ~~Three macOS-only failures in `tests/test_cache_writes.py`~~ — fixed in v0.7.1.
13. yfinance 1.7.0 still uses pandas `inplace=True`; the `Pandas4Warning`s are hidden by
    `yahoo/common.hide_yfinance_inplace_deprecations()`. Remove it after a yfinance
    release that stops using `inplace` (check `pip index versions yfinance`).

14. **News is headlines only.** Google News RSS gives title, source and date, not
    the article; Yahoo summaries arrive only when Google is down (the chain falls
    back). Google can throttle or block (a server IP more than a home one); the
    vendor retries 429/5xx and raises `VendorUnavailableError` so Yahoo serves.
    Alpha Vantage `NEWS_SENTIMENT` (key, 25 calls/day free) also takes a window:
    add it to the chain for summaries if the key is paid.
15. **No social feed for a past date.** StockTwits and Reddit serve recent posts
    only, so historical runs and backtest cells have news but no social data.
16. **Run-to-run variation on a plan.** Claude Code takes no temperature, so the
    same date can still rate differently; data inputs are now stable (cached past
    news, fixed snapshot). Data coverage counts the tool calls only: the
    sentiment analyst fetches its three sources directly and is not counted.
17. **Statements lag the earnings release.** EDGAR serves a quarter once its
    10-Q is filed (~2 weeks after the 8-K press release), and 10-K years have no
    Q4 column (Q4 = FY − 9M is not derived). Between release and filing only the
    news headlines carry the new quarter.

18. **IBKR is holdings only.** The Flex Web Service gives statements (positions,
    cash), not market data; prices still come from Yahoo. IBKR quotes and bars
    would need TWS or IB Gateway running with the API on (`ib_async`), which this
    Mac did not have on 2026-11-09. The Flex sync is tested against IBKR's
    documented XML only: confirm it on the user's real query (it must include
    Open Positions; Cash Report and Account Information are optional).
19. **Research sites.** StockAnalysis is a fallback vendor only (its terms admit
    robots.txt-following crawlers; robots.txt allows /stocks/ and /quote/). Its
    markup can change: `page_tables` reads h1–h3 + table rows, so a redesign
    shows up as NoMarketDataError, not wrong numbers. Seeking Alpha (Alpha
    Picks) is not read: its terms forbid automated access. A paid API (FMP,
    Finnhub) could add point-in-time estimates for past runs.
20. **The stance rules are a cross-check, not a model.** `price_structure._plan`
    is deterministic and explainable; it has not been backtested. A backtest
    metric "did the stance's buy zone/target get hit first" would calibrate it.

## Ranked ideas

| # | Idea | Why it matters | Sketch |
| --- | --- | --- | --- |
| 0 | **Earnings releases and point-in-time valuation** | A run between an earnings release and its 10-Q misses the quarter; past runs have no P/E. | SEC 8-K Item 2.02 + EX-99.1 excerpt as a fundamentals tool; P/E from EDGAR shares × the as-of close and TTM EPS as filed. |
| 1 | **Outcomes for live runs** (chart part done: outcome since the analysis) | The history shows ratings but never whether they were right. | The live memory log already settles past decisions (alpha vs benchmark). Read it in the adapter (`backtest_outcomes` works on any log), join on ticker+date, show realized alpha/hit in the run table and run view; add a "Settle past decisions" action (`settle_all_pending`). |
| 2 | **Backtest export and comparison** | Results leave the app only as screenshots. | CSV/Markdown export of cells + scores; a compare view of two sweeps (same tickers, different models/depth). |
| 3 | **Run queue with a concurrency limit** | Several runs at once on a Pro plan hit its limits faster and contend for Claude Code slots. | Queue launches in the orchestrator, limit per provider, show queue position. |
| 4 | **Upgrade FastAPI/uvicorn** | Removes the Starlette pin trap (issue 4). | Bump pins, run console suite and a live smoke. |
| 5 | **Parallel backtest cells (opt-in)** | Faster sweeps for API-key providers. | N workers in `_sweep`; keep 1 for plans; cells of one ticker stay ordered (memory lessons). |
| 6 | **Live ChatGPT plan model list** | Parity with the CLI. | Adapter calls `chatgpt_auth.list_models()` with a cache; fall back to custom. |
| 7 | **Multiple named books** | Separate accounts or strategies. | `portfolios` table, picker in forms; snapshot stays per run. |
| 8 | **Notify when a long job ends** | Backtests take many minutes. | Browser Notification API from the polling loop. |
| 9 | **Favicon and small polish** | Clean console. | `frontend/public/favicon.svg` + link in `index.html`. |
