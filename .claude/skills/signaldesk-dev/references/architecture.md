# Architecture reference

Contents: 1. Core package · 2. CLI · 3. Console backend · 4. Data flows ·
5. Database · 6. API routes · 7. Frontend · 8. On-disk state

## 1. Core package (`tradingagents/`, upstream unless noted)

- `default_config.py` — `DEFAULT_CONFIG`, built at import from defaults plus
  `TRADINGAGENTS_*` env overrides (`_ENV_OVERRIDES`). `.env` is loaded by
  `tradingagents/__init__.py` (`load_dotenv(find_dotenv(usecwd=True))`), so the
  developer's `.env` changes defaults in any process started in the repo.
- `graph/trading_graph.py` — `TradingAgentsGraph(selected_analysts, config, callbacks)`.
  Builds the quick/deep LLMs with `create_tier_client(config, "quick"|"deep")`
  (the console wraps this name to inject its timeout). Lifecycle:
  `create_run_state(ticker, date, asset_type, portfolio)` →
  `begin_checkpoint(...)` → `stream_run(checkpoint_input(init), **args)` yields
  `(messages, chunk)`; chunk is a whole top-level state, one analyst's filed
  report, or `None` → `record_decision(...)` (state log + memory log) →
  `clear_checkpoint_on_success(...)` → `save_reports(state, ticker, save_path)`.
  `propagate()` does all of it in one call. The checkpoint signature includes
  analysts, depth, asset type, the portfolio fingerprint and a digest of config.
- `agents/state.py` — `AgentState`. Report keys: `market_report`,
  `sentiment_report`, `news_report`, `fundamentals_report`; debate containers
  `investment_debate_state` (`bull_history`, `bear_history`, ...) and
  `risk_debate_state` (`aggressive_history`, ...); decisions `investment_plan`,
  `trader_investment_plan`, `final_trade_decision`, `final_rating`; plus
  `portfolio_context`, `past_context`, `memory_note`.
- `agents/structured.py` — `bind_structured` / `invoke_structured` /
  `invoke_structured_or_freetext`: structured output with a free-text fallback.
  `agents/analysts/turn.py` — an analyst's tool turn (`llm.bind_tools`) and the
  wrap-up turn once tool rounds are spent (no tools; history written as text).
- `agents/rating.py` — `run_rating(state)`, `parse_rating`, `RATING_REVIEW`.
- `llm_clients/` — `factory.create_llm_client(provider, model, base_url, **kw)`,
  `build_llm_kwargs(config)` (effort, temperature, retries, max_tokens),
  `create_tier_client`. `openai_client.py` holds the OpenAI-compatible registry
  incl. `chatgpt` (`SIGNED_IN_PROVIDERS`); `chatgpt_auth.py` its sign-in.
  **Fork-owned:** `claude_code.py`, `claude_client.py` (see `claude-plan.md`);
  registrations in `factory.py` (`EFFORT_PROVIDERS`), `api_key_env.py`,
  `validators.py`, `model_catalog.py`.
- `backtest.py` — `iter_grid(start, end, every)` (never past today),
  `run_backtest(tickers, dates, config, ...)` (own memory log under
  `results_dir/backtest/<run_id>`), `summarize(result|log_path)` →
  `BacktestSummary(resolved, pending, unscored, holding, by_rating)`.
- `portfolio.py` — `PortfolioContext(cash, currency, positions[Position])`,
  `render(ticker)`, `fingerprint()`, `load_portfolio(path)`.
- `memory/` — `TradingMemoryLog` (`store_decision`, `load_entries` → dicts with
  `date, ticker, rating, pending, raw, alpha, holding, resolved, decision,
  reflection`), `settlement.py` (`fetch_returns` from the first close after the
  date over `holding_period_days`, `resolve_benchmark`, `settle_pending`),
  `reflection.py` (one quick-LLM call per settled decision).
- `dataflows/` — vendor router and vendors (Yahoo, SEC EDGAR, Alpha Vantage,
  FRED, Polymarket, Reddit, StockTwits); `vendors/yahoo/ohlcv.load_ohlcv` feeds
  the console chart and quotes. **Fork-owned** `vendors/google_news.py`: Google
  News RSS search with `after:`/`before:` for `get_news` (ticker query first,
  company name tops up; `company_queries`, `search_name`) and `get_global_news`
  (one search per `global_news_queries` topic). Default `news_data` chain is
  `google_news,yfinance`. Results cached in `data_cache_dir/google_news/`: a
  window that ended 4+ days ago for good, a live one for 30 min; a thin answer
  (<10 items) is asked again, since Google sometimes answers 6 then 100.
- `output_languages.py` — fork-owned shared language list.

## 2. CLI (`cli/`)

`main.py`: bare `tradingagents` = analysis (flags `--ticker --date --analysts
--save/--no-save --show/--no-show --portfolio file.json --checkpoint`),
`backtest TICKERS --start --end --every --analysts --portfolio --run-id`,
`login {chatgpt|claude} [--token]`, `logout {chatgpt|claude}`.
`prompts.py`: `_llm_provider_table()` (the menu), `select_llm_provider` (plan
providers are grouped under "Subscription"), `ensure_signed_in` →
`_ensure_claude_plan`, `ensure_api_key`. `selections.py`: the step flow;
`_check_tier_providers` checks every tier's sign-in/key before a run. `run.py`:
the Live display over `stream_run`. `prefs.py`: remembered answers.

## 3. Console backend (`backend/`)

- `adapters/tradingagents_adapter.py` — the boundary. Key pieces:
  `_console_tier_client` (timeout; Claude-plan tiers ≥ 600 s),
  `ConsoleTradingAgentsGraph`, `_SECTION_SOURCES` (UI section → state key, in
  pipeline order), `run_streaming_analysis(..., portfolio, results_dir,
  memory_log_path, should_stop)` (raises `RunStopped`; falls back to
  `propagate()` and emits `streaming_degraded` only for interface errors before
  any section), `StatsCallbackHandler` (calls, tokens, `api_equivalent_usd`, and
  `data`: each tool's outcomes via `data_outcome()` — ok/empty/withheld/
  unavailable/error — stored as `usage.data_coverage`), `core_version()`,
  `upstream_catalog()` + `_REASONING_CONTROLS` (provider/model metadata for the
  UI), portfolio helpers (`normalize_portfolio`, `portfolio_context`), plan
  status (`claude_plan_status`, `chatgpt_plan_status`), backtest helpers
  (`backtest_dates`, `backtest_benchmark`, `backtest_outcomes`, `backtest_scores`,
  `settle_backtest`, `RATING_DIRECTION`, `sweep_blocker`),
  `load_current_market_chart(symbol, since=trade_date, warmup=)` (keeps the
  analysis date on the chart; candles carry volume, `history` holds `warmup`
  earlier bars for indicators), `book_position(snapshot, ticker)` (cash,
  currency, units held under any spelling), `load_price_structure(symbol,
  trade_date)` (weekly/monthly bars to today from the 5-year cache plus
  `price_structure.read_structure` at the analysis date and now — the read the
  market analyst's `get_price_structure` gave the run). `data_outcome` reads
  "No analyst coverage" as empty.
- `app/services/charts.py` — display-only levels: `strategy_levels` (labelled
  lines in PM → RM → trader), `decision_direction` (rating, else the trader's
  proposal → long/hold/reduce), `plan_levels` (names levels for the side, drops
  ones on the wrong side of the entry or >3×/<0.3× the analysis close, with
  notes), `analysis_outcome` (close seen, change since, high/low, first touch of
  each level), `plan_tranches` (the first Side/Price/Size table in PM, else
  trader — display only) and `position_plan` (drops implausible tranches,
  first touch of each, committed/average entry/risk at a protective stop/reward
  at the Sells/R, all in % of cash or of position value; `_replay` trades it on
  the bars since, Sells sized from the peak position held, stop last on a shared
  bar; amounts only where currencies cannot mix), `decision_stance` (the PM's
  `**Action Now**` line, display only). `/api/runs/{id}/chart` returns all of
  it plus `structure` (frames, analysis/latest reads, stance labels) and
  `action_now`; a structure failure leaves `structure: null`, not a 503.
  `backend/tests/conftest.py` stubs `load_price_structure` to `None` for every
  test (it reads Yahoo); a test that wants it patches `adapter.load_ohlcv`.
- `app/services/technicals.py` — `chart_technicals(history, candles, trade_date)`:
  indicator series (10 EMA, 50/200 SMA, Bollinger 20/2, RSI 14 Wilder, MACD
  12/26/9, ATR 14, volume 20-day) for the visible bars, 50/200 crosses, and a
  `technical_read` at the analysis date and the latest bar (trend, swing
  structure with breakouts, RSI, MACD, up/down volume → bias score; pivot S/R
  clustered within ½ ATR; squeeze; 52-week range). Pivots count only once the
  bars confirming them are ≤ the read's date.
- `app/settings.py` — env-driven paths (`TRADING_CONSOLE_DATA_DIR`,
  `..._REPORTS_DIR`, `BACKTESTS_DIR`), host/port, CORS, daily cap, LLM timeout
  (120 s) and retries (1), heartbeat, `BACKTEST_MAX_CELLS` (60). It sets
  `TRADINGAGENTS_RESULTS_DIR/CACHE_DIR/MEMORY_LOG_PATH` before the adapter
  imports the package — import `settings` first.
- `app/services/orchestrator.py` — `RunOrchestrator`: `create(payload,
  portfolio)`, `start` → `execute(run_id)` (thread + heartbeats),
  `_execute_sync` (status, adapter call, sections, usage, decision, events),
  `request_stop`, `resume`, `recover_interrupted_runs`, `backtest_paths`.
  `RunBroadcaster` fans events out to WebSocket subscribers.
- `app/services/backtests.py` — `BacktestService`: `create`, `_sweep` (one cell at
  a time via `runs.execute`), `_prepare_run` (cell run, no checkpoint, reset on
  retry), `_finish_cell` (returns a sweep blocker), `_settle_sync`, `_refresh`
  (outcomes + scores from the sweep's log), `request_stop`, `resume`, `settle`,
  `delete`, `recover_interrupted`; `score_cells` (overall/per-ticker figures).
- `app/services/portfolio.py` — the one saved book in `AppPreference("portfolio")`.
- `app/services/ibkr_flex.py` — IBKR Flex Web Service (SendRequest → poll
  GetStatement on 1019 & co. with `WAITS`), `book_from_statement` (Open Positions
  SUMMARY rows of STK/CRYPTO, cost basis per unit, Yahoo symbols via
  `yahoo_symbol` — exchange suffix map, `BRK B`→`BRK-B`, SEHK zero-padding,
  crypto `SYM-CCY`; Cash Report `BASE_SUMMARY` cash, negative → 0 with a note;
  several accounts merged). Token kept out of errors. `POST /api/portfolio/ibkr`
  returns the book for review and saves nothing; secrets `IBKR_FLEX_TOKEN`,
  `IBKR_FLEX_QUERY_ID` are in `EXTRA_SECRET_NAMES`.
- `app/services/providers.py` — provider list with `configured`/`availability`;
  plan providers get a `subscription` status. `costs.py` — rough per-token
  estimates; plan-billed providers rate 0. `secrets.py` — Fernet-encrypted keys
  in SQLite, loaded into `os.environ` at start-up (allowed names come from the
  catalog plus `EXTRA_SECRET_NAMES`, incl. `CLAUDE_CODE_OAUTH_TOKEN`).
- `app/dev.py`, `app/dev_all.py` — `make web-backend` / `make web-dev`
  supervisors (reuse a healthy backend on the port; ports follow env).

## 4. Data flows

**Run:** `POST /api/runs` (rate limit, provider configured, book if
`use_portfolio`, daily cap) → `orchestrator.create` stores `Run` (+ `Usage`,
`config_snapshot`, `portfolio_snapshot`) → task `execute` → `_execute_sync` on a
thread → `run_streaming_analysis` → each changed section is upserted
(`RunSection`) and published as a `section` event (`RunEvent` with a sequence) →
WebSocket `/api/runs/{id}/stream` replays persisted events then streams live,
closing on `completed|failed|stopped`. The run view also polls every 15 s.

**Stop/Resume:** `POST /api/runs/{id}/stop` adds the id to a stop set; the adapter
polls it between graph steps and raises `RunStopped`; status `stopped` keeps the
checkpoint. `POST /api/runs/{id}/resume` requeues stopped/failed/completed runs.
A backend restart turns queued/running runs into resumable failures.

**Backtest:** `POST /api/backtests` (grid via `iter_grid`, cap, provider, book,
daily cap) → `Backtest` + ordered `BacktestCell`s (ticker-major) → `_sweep`: for
each retryable cell, a `Run` with `backtest_id` (checkpoint off, sweep log and
results dir) runs through the normal orchestrator → `_finish_cell` → `_refresh`
→ after the last cell, status `settling` → `settle_backtest` (graph
`settle_pending` per ticker) → `completed`. A plan-limit/sign-in failure stops
the sweep (`sweep_blocker`); Stop stops the running cell; restart → `stopped`.

## 5. Database (SQLite via SQLAlchemy, `backend/data/console.sqlite3`)

`runs` (config, status, `portfolio_snapshot`, `backtest_id`), `run_sections`,
`decisions`, `usage` (`api_equivalent_usd`, `data_coverage`), `run_notes`, `run_events`,
`watchlist`, `secrets`, `app_preferences` (`defaults`, `portfolio`), `backtests`
(grid, config, `portfolio_snapshot`, status, `summary` JSON with `upstream`,
`console`, `settlement`), `backtest_cells` (status, `run_id`, rating, pending,
returns, holding, resolved date, benchmark, reflection, error).
`init_database()` → `create_all` + `_add_missing_columns` (nullable only) +
missing indexes.

## 6. API routes

- `/api/runs`: `POST ""`, `GET ""` (filters; `include_backtests=false` hides
  cells), `GET /{id}`, `GET /{id}/chart`, `PATCH /{id}` (star),
  `POST /{id}/resume`, `POST /{id}/stop`, `GET /{id}/report.md`,
  `DELETE /{id}`, `GET|POST /{id}/notes`, `WS /{id}/stream`. Cell runs refuse
  resume/stop/delete (409): manage them from their backtest.
- `/api/backtests`: `GET /grid?start_date&end_date&every_n_days&tickers`,
  `POST ""`, `GET ""`, `GET /{id}`, `POST /{id}/stop|resume|settle`,
  `DELETE /{id}`.
- `/api/portfolio`: `GET ""`, `PUT ""`, `DELETE ""`, `GET /preview?ticker`,
  `GET /quotes`.
- `/api/config`: `GET /providers?fresh`, `GET /subscriptions/{claude|chatgpt}?fresh`,
  `GET /vendors`, `GET /costs`, `GET /runtime`, `GET|PUT /preferences`,
  `GET /secrets`, `PUT|DELETE /secrets/{name}`.
- `/api/watchlist`, `GET /api/health` (reports the installed core version).

## 6b. Fork-owned data tools (core)

- `tradingagents/agents/signaldesk_tools.py` — `get_price_structure` (market
  analyst) and `get_analyst_coverage` (fundamentals analyst); both clamp the date
  with `as_of` and turn vendor errors into `DATA_UNAVAILABLE`/`NO_DATA_AVAILABLE`.
  They call their modules directly, not via `router.py`, so
  `backend/tests/test_adapter_graph.py::offline` patches `price_structure.load_ohlcv`
  and `street._fetch` as well as the router.
- `tradingagents/dataflows/price_structure.py` — weekly (W-FRI) and monthly bars
  labelled by first session; 10/40-week and 10/20-month SMAs, RSI/ATR 14 (Wilder);
  swing pivots (3 weeks / 2 months each side, newest bar treated as forming)
  clustered within 0.6 weekly ATR (span ≤ 1.5 ATR, ≥ ½ ATR wide); a zone the
  price sits inside is support if the last 4 closed weeks came from above;
  trend from the slow SMA and its 4-bar slope; `_plan` → buy zone, targets
  (ATR projections when none), trigger, invalidation, stance (`STANCES`) from
  trend × location, with new-money and holder text. `read_structure(daily,
  as_of)`, `chart_bars`, `render_price_structure`, `build_price_structure`.
- `tradingagents/dataflows/vendors/yahoo/street.py` — `get_analyst_coverage`:
  dated rating changes ≤ as_of (120 days listed; each firm's latest target over
  180 days = dated consensus), earnings record released before as_of's 16:00,
  next date (today only); consensus/recommendations/estimates/revisions/short
  interest/ownership only for a run dated today. Sections fail independently.
- `tradingagents/dataflows/vendors/stockanalysis.py` — last vendor in the
  `fundamental_data` chain (`get_fundamentals` from /statistics/, statements from
  /financials/{income-statement,balance-sheet,cash-flow-statement}/?p=quarterly,
  latest 8 periods) and the analyst-coverage backup (/forecast/) that
  `signaldesk_tools.get_analyst_coverage` uses when Yahoo raises, says nothing,
  or misses sections. `page_path` maps Yahoo suffixes to /quote/{asx,lon,tsx…}/.
  robots.txt via `RobotFileParser` (re-read daily; unreadable = unavailable),
  `MIN_INTERVAL` 3 s under a lock, disk cache `CACHE_SECONDS` 6 h under
  `data_cache_dir/stockanalysis/`; "Pro"/"Upgrade" cells → "—", all-paid columns
  dropped; past dates withheld with no request. Stdlib `HTMLParser`, no bs4.
- `PortfolioDecision.action_now` (`ExecutionStance`: Buy now / Wait for pullback
  / Wait for breakout / Hold / Take profit / Exit / Avoid), read leniently
  (`_read_stance`), rendered as `**Action Now**` after the rating.

## 7. Frontend (`frontend/src`)

- `App.tsx` — shell + navigation (`View` union), `Dashboard`, `NewAnalysis`,
  `LiveRun` (sections tabs, chart, notebook, Stop/Resume, book card, cell
  banner, `DataCoverageCard`), `StrategyChart` (analysis marker via
  `createSeriesMarkers`, side-named levels, `OutcomeStrip`, notes) moved to
  `StrategyChart.tsx` (layer chips saved in localStorage, panes for volume/RSI/MACD
  with stretch factors, crosshair readout written to a ref, `PlanCard`,
  `TechnicalCard`; Daily/Weekly/Monthly switch saved in localStorage, weekly
  default, monthly on a log scale; layers carry the frames they exist on and
  are saved under `signaldesk.chart.layers.v2`; `StanceBanner` (PM Action Now +
  weekly/monthly stance), `KeyLevelsCard` (thresholds at the analysis or today,
  `trancheFit`), zones drawn by `chartZones.ts`'s `BandsPrimitive` — a series
  primitive painting bands in `drawBackground`, key bands widen autoscale),
  `CompareRuns`, `SettingsView` + `PlanSettings` +
  `PresetSettings`. Launch intents (`analyzeWithBook`) prefill New analysis.
- `Backtests.tsx` — `BacktestsView`, `NewBacktest`, `BacktestView`, `CellDetail`.
- `Holdings.tsx` — `HoldingsView` (editor with per-position currency from
  `shared.quoteCurrency()` suffix map, **Use by default**, Export/Import JSON in
  the CLI `--portfolio` format, **Sync from IBKR** into the draft with notes), `AgentView`, `PortfolioPicker` (starts on the
  book when `use_by_default` and the person has not picked), `BookCard`. The
  book row is `AppPreference("portfolio") = {"book": …, "use_by_default": bool}`;
  the run snapshot is the book alone. `ConsolePortfolio` (adapter) adds
  `Position.currency` and renders it; without currencies it renders and
  fingerprints exactly as upstream's `PortfolioContext`.
- `ModelSetup.tsx` — `loadModelDefaults`, `estimateRunCost`, `ModelSetup`.
- `Report.tsx` — `Report`, the one renderer for agent markdown (run view and
  compare view). `react-markdown` + `remark-gfm` (tables) + a local rehype pass
  (`decorate`): heading emojis by topic (`HEADING_EMOJI`, first match wins), a
  🟢/🔴/🟡 marker on table columns named like Interpretation/Signal/Bias, and
  `**Label**:` paragraphs grouped with what follows into `.report-field` blocks.
  `prepare()` lifts short typed fields (Rating, Action, Entry Price, Stop Loss,
  Price Target, Time Horizon…) into tiles and the `FINAL TRANSACTION PROPOSAL`
  line into a banner; `splitTurns()` turns upstream's `Bull Analyst:` prefixes
  into round headers. Display only: nothing parsed here is stored. Section
  emojis live in `sectionMeta.icon` (`App.tsx`). Tailwind's preflight strips list
  markers, so `.markdown` restores them in `styles.css`.
- `shared.tsx` — formatters (`parseTimestamp`, `localISODate`, percent/number),
  `providerName`, model/reasoning helpers, small components.
- `api.ts` — every type and endpoint; FastAPI 422 messages are flattened.
- `vite.config.ts` — proxy target `TRADING_CONSOLE_API_URL`, port
  `TRADING_CONSOLE_FRONTEND_PORT`, vendor chunks.

## 8. On-disk state

Console: `backend/data/` (`console.sqlite3`, `.secrets.key`, `checkpoints/`,
`memory/` live log, `results/` state logs, `results/backtests/<id>/`), reports in
repo `reports/<run_id>/`. CLI: `~/.tradingagents/` (`logs/`, `cache/`, `memory/`,
`chatgpt-auth.json`, `cli_prefs.json`). Docker: named volumes
`research_console_data`, `tradingagents_data`; `./reports` bind mount;
`CLAUDE_CONFIG_DIR` inside each data volume.
