# Signal Desk web research console

Signal Desk is a single-user, local-first web layer over TradingAgents. It is a
research notebook, not an execution engine: it starts analyses and backtests,
preserves each agent section as it arrives, records the exact model
configuration, and makes past calls easier to annotate, compare and score.

## Local setup

The web console has its own dependencies so the upstream package remains
untouched. TradingAgents needs Python 3.11 or later; `make venv` refuses an
older `.venv` and says how to recreate it.

```bash
make install web-install
make web-dev
```

The single `make web-dev` process starts and supervises both the backend and
frontend. Press Ctrl+C once to stop both. Open `http://127.0.0.1:5174`. The API listens only on
`http://127.0.0.1:8765` by default. API keys can be supplied through the
existing environment variables or saved from Settings; saved values are
encrypted in the console data directory and only their final four characters
are returned.

For backend-only development, run `make web-backend`. When the Docker console
already holds the default ports, move the development pair:
`TRADING_CONSOLE_PORT=8766 TRADING_CONSOLE_FRONTEND_PORT=5175 make web-dev`
(the frontend's `/api` proxy follows the backend port).

For a containerized local deployment:

```bash
docker compose up --build web-backend web-frontend
```

Open `http://127.0.0.1:4173`.

## Running on a Claude or ChatGPT plan

Settings → Subscriptions shows whether each plan is ready, and the provider
picker lists them as "Claude plan" and "ChatGPT plan". A plan-billed run shows
"Billed to plan" instead of a dollar estimate; for the Claude plan the run also
records what its tokens would have cost at API list prices.

- **Claude Pro or Max.** Every model call runs through the Claude Code installed
  on the backend's machine (headless, with its tools, MCP servers, plugins,
  hooks and settings off); the console never reads the Claude credential.
  Sign in once in a terminal on that machine with `tradingagents login claude`
  (or `make login-claude`), then press "Check again". `TRADINGAGENTS_CLAUDE_CLI`
  names the executable when it is not on PATH; editors' bundled copies and the
  Agent SDK's (`make install-claude`) are found automatically. Install the SDK
  only through that target: on its own, `pip install ".[claude]"` lets its `mcp`
  dependency upgrade Starlette past what the console's pinned FastAPI supports.
- **In Docker.** Build with `WITH_CLAUDE_CODE=1 docker compose up --build -d
  web-backend web-frontend` to add Claude Code to the image. Then, in Settings →
  Subscriptions, press **Sign in with Claude**, approve on claude.com, and paste
  the code claude.com shows. This is Claude Code's own login running in the
  container: it keeps and refreshes the sign-in in the data volume
  (`CLAUDE_CONFIG_DIR`), so it lasts across restarts and rebuilds, and the console
  only passes on the one-time code. Press **Make Claude the default provider** to
  start every new analysis and backtest on it. Alternatively, save a `claude
  setup-token` token as `CLAUDE_CODE_OAUTH_TOKEN` in Settings → Provider
  credentials or `.env`.
- **ChatGPT Plus or Pro.** Sign in with `tradingagents login chatgpt` on the
  backend's machine.

Plan access is for your own use: Anthropic does not allow Claude plan access to
be shared or resold, so do not expose a plan-backed console to other people.

## Holdings

The Holdings page keeps one book: positions (signed quantities, negative for a
short, with an optional average price, the currency it is in and the account
that holds it), free cash per account, and the book currency. A position's currency is filled in from its ticker's
exchange (NVDA in USD, NXT.AX in AUD) and can be changed; the agents read it
("average price 180.00 USD", plus a note when it differs from the cash
currency). The page shows each holding's latest close and unrealized P/L in that
currency, and exactly what the decision agents will read for any ticker.

**Several accounts.** Name each row's account (IBKR, Betashares, CommSec…);
the same ticker may appear once per account. Cash is entered per account in its
own currency, and the book's cash is their total in the **book currency**,
converted at the day's Yahoo exchange rate when the book is saved (the page
shows the rate used; a rate Yahoo cannot give stops the save and says so). The
agents read the total, each account's part ("Cash by account: IBKR 170.00 USD
(about 245.51 AUD); Betashares 100,000.00 AUD") and where each holding of the
analysed ticker sits ("held at IBKR"). A book without accounts is stored and
fingerprinted as before, so earlier runs resume.

The book is saved in the console's database and kept for every later session.
With **Use by default** on (the default for a new book), New analysis and New
backtest start on "Decide against my book"; each launch can still switch to no
portfolio context. Each console keeps its own database (the Docker console in
its data volume, `make web-dev` in `backend/data/`), so **Export** downloads the
book as a JSON file and **Import** loads one into the editor (press Save to keep
it). The same file works with the CLI's `--portfolio`.

**Sync from IBKR** reads the book from Interactive Brokers through the Flex Web
Service, with no TWS or gateway running. In IBKR's Client Portal, open
Performance & Reports > Flex Queries, create an Activity Flex Query with the
**Open Positions** section (and **Cash Report** for your cash; **Account
Information** names the base currency), and turn on the Flex Web Service to get
a token. Save the token and the query's ID in Settings as `IBKR_FLEX_TOKEN` and
`IBKR_FLEX_QUERY_ID`. In the query, select all fields of each section, keep
the format XML, and prefer the Date Format yyyyMMdd (a MM/dd or dd/MM date whose
day and month cannot be told apart is left out of the sync message). The sync
loads stocks, ETFs and crypto into the editor
under Yahoo's symbols (NXT on the ASX becomes NXT.AX, BRK B becomes BRK-B, 700
on SEHK becomes 0700.HK), with each position's cost basis per unit in its own
currency and the cash in the base currency; options, futures, bonds and the
like are left out and listed in a note. Everything it reads is marked with the
account IBKR, and a later sync replaces only those rows and IBKR's cash: rows of
other accounts stay. Rows with no account (from a sync before accounts existed)
are replaced when IBKR lists the same ticker, an unmarked cash figure in IBKR's
currency is replaced by IBKR's cash, and any unmarked row IBKR no longer lists is
named in a note so you can remove it or name its account. Nothing is saved
until you press Save holdings. The token is read-only: it fetches statements
and cannot trade.

Choosing "Decide against my book" when launching an analysis or a backtest
gives the book to the trader, the risk analysts and the portfolio manager; the
analysts and researchers stay position-neutral, as upstream designs them. Three
states stay distinct: no book (no portfolio context), a flat book (saved with no
positions) and a book with positions. Each run stores the exact book it was
launched with, and that snapshot is part of its checkpoint signature, so editing
the book later never changes or resumes a past run with different holdings.

## Backtests

Backtests → New backtest takes up to 12 tickers, a first and last date and a
step in days. The grid stops at today (as upstream's `iter_grid` does) and is
capped at `TRADING_CONSOLE_BACKTEST_MAX_CELLS` cells (60 by default), because
every cell is a complete multi-agent run.

- Cells run one after another, ticker by ticker and date by date. Each is an
  ordinary console run, without checkpoints, whose live sections, report and
  notes open from the backtest; backtest cells stay out of the analysis history.
- All cells share a memory log of the backtest's own, so the console's live
  memory log is never written to, and a later cell learns only from the sweep's
  earlier decisions whose outcome was known by its date.
- After the last cell, every decision whose holding window has traded is
  settled: entered at the first close after its date, held for
  `holding_period_days` trading days, and scored on alpha against the
  instrument's regional benchmark. A cell too recent to score stays pending;
  "Settle" scores it once the market has traded through its window.
- Stop ends the running cell at its next step; Resume re-runs the stopped,
  failed and unstarted cells and settles again. A backtest interrupted by a
  backend restart becomes resumable.
- Scores come from upstream's `summarize` (decisions, hit rate and mean alpha
  by rating), with the overall and per-ticker figures beside them. They are
  indicative: one sampling per cell, and StockTwits and Reddit serve only
  recent posts, so past cells have no social feed (news comes from Google News
  search, which covers past windows). It is not a portfolio simulation.

## Where the data comes from

Prices and indicators come from Yahoo Finance, statements from SEC EDGAR as
filed (US companies, then Yahoo), macro series from FRED. Company and market
news come from **Google News search**, which takes a date range, so an analysis
dated weeks back reads the headlines published in its own week (an earnings
beat, a guidance cut, analyst actions); Yahoo's news feed is the fallback when
Google does not answer. Headlines are kept to the analysis window by their
publication date, and a past window is cached, so re-running the same date reads
the same news.

The fundamentals analyst also reads **analyst coverage** from Yahoo Finance, the
data research sites show on a stock's forecast and statistics pages: rating
upgrades and downgrades with each firm's price-target change, a consensus target
built from each firm's latest target in the six months to the analysis date,
the earnings record (estimate, reported EPS, surprise) and the next report
date, and for a run dated today the consensus target and rating, the
recommendation trend, EPS and revenue estimates with their 30- and 90-day
revisions, short interest, ownership and EV multiples. The market analyst reads
the **weekly and monthly price structure** (see the chart below): the same zones
the chart draws, so the trader's and portfolio manager's tranches are set
against them.

**StockAnalysis.com is the backup.** When SEC EDGAR and Yahoo both fail for the
company overview or a statement, or Yahoo cannot serve analyst coverage, the
same data is read from StockAnalysis's statistics, financials and forecast pages
(US listings and ASX, LSE, TSX and other exchanges). Its terms allow automated
access only to crawlers that follow its robots.txt, so SignalDesk checks
robots.txt before every request, names itself in the User-Agent, waits three
seconds between requests and caches each page for six hours; cells behind its
paid plan stay dashes. Its pages are today's, so a past-dated run never uses
it. Seeking Alpha (including Alpha Picks) is not read: its terms forbid
automated access, and Alpha Picks is subscription content.

Some data exists only as of today and is withheld from a past date rather than
leaked into it: Yahoo's company profile and valuation, the analyst consensus,
estimates, short interest and ownership, insider trades without a filing date,
and prediction-market odds. Dated records are served as of the analysis date:
rating changes made by then, and quarters reported before that day's close. StockTwits and Reddit serve only recent
posts, so the sentiment analyst has no social feed for a past date and says so.

**Scoring past decisions.** Every analysis also scores the console's earlier
decisions, of any ticker, whose five-session holding window has traded since:
it compares each one's return with its benchmark and writes the lesson to the
memory log, so the next Portfolio Manager reads it. That is why an NVDA run's
log can mention RKLB. A request that times out or loses its connection is
retried twice. If Yahoo still cannot be reached, the backend logs "The RKLB
decision of 2026-10-23 could not be scored yet (prices unavailable: ...); it
stays pending for the next run". The rest of that pass's due decisions are not
asked for. Nothing is lost: the next analysis scores them, and the current one
goes on with the lessons already in the log.

## Reading a report

Each agent's section is shown as formatted text, with the model's wording left
as it is:

- Tables render as tables, and in columns that interpret a figure
  (Interpretation, Signal, Bias…) each cell gets 🟢, 🔴 or 🟡 for bullish,
  bearish, or mixed and cautious wording.
- The decision fields (Rating, Action Now, Action, Entry Price, Stop Loss,
  Price Target, Time Horizon) appear as tiles at the top of the section, and the trader's
  `FINAL TRANSACTION PROPOSAL` appears as a banner at the end.
- Longer fields (Executive Summary, Investment Thesis, Strategic Actions…) are
  labelled blocks, and headings get an emoji for their topic (📈 trend,
  ⚡ momentum, 🏦 balance sheet, 📋 summary table…).
- Debates with more than one round show a header for each turn, such as
  🐂 Bull analyst · Round 2.

The markers and emojis are reading aids picked from the words used, not extra
analysis. The Markdown download is the agents' text unchanged.

The chart above the report shows the price from before the analysis date to
today, with an **Analysis** marker on the date (or the week or month) the run
used. **Daily**, **Weekly** and **Monthly** switch the candles; weekly is the
default, since a swing trade or an investment is held for weeks to months. The
choice is remembered in this browser.

- **What to do now.** A banner above the chart puts the portfolio manager's
  **Action Now** (Buy now, Wait for pullback, Wait for breakout, Hold, Take
  profit, Exit or Avoid) beside the chart's own stance on the weekly (swing) and
  monthly (investor) bars. A Buy rating can still be "Wait for pullback": the
  rating says which way the evidence points, the action whether the price is
  where to act. Under it, what new money and what holders should do.
- **Buy and take-profit zones.** Shaded on every timeframe: the buy zone in
  green, take-profit zones in blue (red "sell into strength" zones in a
  downtrend; dashed when projected from the ATR because no swing high sits above
  the price), other support and resistance faintly, the invalidation level as a
  red dashed line and the breakout (or trend-turn) trigger as a dark one. They
  come from fixed rules: swing highs and lows of the weekly and monthly bars
  clustered into zones at least half a weekly ATR wide, the trend from the
  40-week (or 20-month) average and its slope, and the stance from trend and
  location (support in an uptrend is a buy, resistance in a range a sale, the
  middle of a range no edge, an extended price a wait for the pullback, a
  downtrend no new buying). Below the 40-week line, support is bought only once
  momentum has turned (the 10-week average rising and the close back above it);
  until then the stance waits for that weekly close. "Just above" a zone means
  within half a weekly ATR and at most 3% of the price. Daily and weekly views draw the swing plan, the
  monthly view the investor plan (on a log scale).
- **Key thresholds** under the chart list the buy zone, take-profit zones,
  trigger and invalidation with their distance from the close and what defines
  each, the trend and swing pattern, and where each of the decision's tranches
  falls against them (in the buy zone, below the invalidation level...). Read
  them at the analysis date (what the market analyst was given) or today.

- **Levels read for the decision's side.** The entry, target and stop that the
  portfolio manager, research manager or trader labelled are named for what the
  rating asks: a Buy or Overweight is a long setup (Entry, Take profit, Stop
  loss); an Underweight or Sell is a reduce setup (Sell / trim at, Downside
  target, Reassess above); a Hold names levels by where they sit (Exit below,
  Upside target). A level on the wrong side of the entry, such as a long's target
  below its entry, or far from the price (a misread "2 ATR"), is not drawn, and a
  note under the chart says why.
- **The outcome since the analysis**: the close the analysis saw, the latest
  close and the change, the highest and lowest prices since, and the date each
  level was first reached. This is how to judge a call after the fact.
- **Trend and momentum layers**, switched on and off with the chips above the
  chart (the choice is remembered in this browser): the 10-day EMA, 50-day and
  200-day SMAs (golden and death crosses are marked), Bollinger bands (20, 2),
  support and resistance from swing pivots, and panes for volume with its
  20-day average, RSI 14 with its 70/30 bounds, and MACD 12/26/9. These are the
  indicators the market analyst is offered, computed over enough earlier history
  that the 200-day average is defined from the chart's first day. Hovering shows
  each value on that day.
- **The position plan.** The trader and the portfolio manager plan entries and
  exits in tranches, each with a price, a size and the level behind it: Buys as
  a share of the available cash (the book's cash when the run used it), Sells as
  a share of the position. The tranches are drawn as B1, B2… and S1, S2… lines
  with their sizes, and the table under the chart lists each one with its
  distance from the analysis close and when it was first reached. Beneath it:
  how much cash the Buys commit, the cost-weighted average entry, the loss if
  every Buy fills and the stop is then hit, the gain if every Sell fills,
  reward-to-risk, and what is left to run. The plan is then traded on the
  sessions since the analysis (a tranche fills when a bar trades through it; a
  bar that touched both a tranche and the stop is read as the stop coming last)
  and compared with putting the whole allocation on at the analysis close.
  Figures are percentages of the available cash, or of the position's value at
  the close for a plan that only sells; amounts in your currency appear where
  the run's book gives them (the cash a Buy commits, the units a Sell takes off
  a holding). The final decision's plan is used, else the trader's.
- **A rule-based technical read** at the analysis date and now: trend (the close
  against the 50- and 200-day averages), swing structure (higher highs and lows,
  lower, a narrowing or widening range, or a breakout from it), RSI, MACD, and
  whether volume is heavier on up days or down days, each scored and summed into
  a bias; the nearest support and resistance with how many swings formed them;
  ATR; the 52-week range; and a note on a Bollinger squeeze. The same rules read
  every run from the candles alone, using only bars up to each read's date, so
  it is a consistent cross-check on what the agents concluded, not their view.

The **Data coverage** card beside the report lists what each kind of data
returned during the run: served, partly served, none found, withheld for a past
date, or unavailable. A run that decided without its company news or statements
says so at the top of the card; weigh its call accordingly.

## Stopping and resuming an analysis

A running analysis has a Stop button. It ends at the next agent step; with
checkpointing on, Resume continues from the last completed step instead of
starting over. Runs that failed (a provider error, a reached plan limit, a
backend restart) resume the same way.

## Runtime state

All console-owned state is redirected under `backend/data/` (or
`TRADING_CONSOLE_DATA_DIR`):

- `console.sqlite3` — runs, sections, events, decisions, usage, notes, keys,
  holdings and backtests. Columns added by a newer release are added to an
  existing database at start-up.
- `checkpoints/` — LangGraph per-ticker checkpoint databases
- `memory/` — the live TradingAgents decision memory log
- `results/` — internal JSON state logs; `results/backtests/<id>/` holds each
  backtest's own memory log and state logs
- `claude-code/` — Claude Code's settings in the Docker image (`CLAUDE_CONFIG_DIR`)

Completed markdown report trees are written to the repository-level
`reports/` directory (or `TRADING_CONSOLE_REPORTS_DIR`). Docker bind-mounts
that directory, so reports remain available on the host after containers stop.

The directory is git-ignored. `POST /api/runs` and `POST /api/backtests` are
rate-limited in process to three launches per minute per client. Set
`TRADING_CONSOLE_DAILY_CAP_USD` to a positive value to turn on the optional
estimated daily spend cap; plan-billed runs count as zero toward it.

## Security boundary

This release deliberately has no login and binds to localhost. Do not expose it
to a public network: it can hold paid API credentials, run on your Claude or
ChatGPT plan, and trigger costly model calls. If the scope changes beyond a
private, single-user console, authentication, CSRF protection, a shared rate
limiter, and a real task queue become release requirements.
