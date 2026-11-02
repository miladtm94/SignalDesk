---
name: signaldesk-dev
description: Expert working knowledge of this repository — a fork of TauricResearch/TradingAgents (multi-agent LLM trading research on LangGraph) that adds the SignalDesk web console (FastAPI backend + React frontend), a `claude` provider that runs on a Claude Pro/Max plan through Claude Code, browser backtests and portfolio-aware (holdings) runs. Use it for any work in this repo: adding or changing features in the tradingagents package, the CLI, the SignalDesk backend or frontend; anything touching LLM providers, the Claude or ChatGPT plan, backtests, holdings/portfolio, run streaming, checkpoints or the memory log; merging an upstream TradingAgents release; debugging a failed run; or running, testing or demoing the app (terminal or Docker). Consult it before exploring the codebase from scratch, even when the request does not mention SignalDesk by name.
---

# SignalDesk on TradingAgents

Read this first; it saves an hour of re-deriving the project. Deeper material is in
`references/` (listed at the end) and two helper scripts are in `scripts/`.

## What the project is

**Upstream** (`upstream` remote, TauricResearch/TradingAgents, merged at **v0.7.1**):
a LangGraph pipeline that analyses one ticker on one date. Selected analysts
(market, social/sentiment, news, fundamentals) run in parallel subgraphs with
tools → bull/bear debate → Research Manager → Trader → aggressive/conservative/
neutral risk debate → Portfolio Manager, who emits a 5-tier rating (Buy,
Overweight, Hold, Underweight, Sell, or REVIEW when unreadable). A memory log
records each decision and later settles it against the regional benchmark.
Upstream also ships a CLI (`tradingagents`, Typer + questionary + rich),
`run_backtest` over a ticker×date grid, `PortfolioContext` holdings, and a
ChatGPT-plan provider.

**This fork** (`origin` remote, **miladtm94/SignalDesk** — renamed from
TradingAgents on 2026-11-03, still in upstream's fork network — branch `main`) adds:

- **SignalDesk** — local single-user research console: `backend/` (FastAPI,
  SQLAlchemy/SQLite, runs work on threads, WebSocket event stream) and
  `frontend/` (React 18 + Vite + Tailwind base + hand-written CSS).
- **`claude` provider** — Claude Pro/Max plan through the person's own Claude Code
  (`tradingagents/llm_clients/claude_code.py`, `claude_client.py`), wired into the
  CLI, Python API and SignalDesk.
- **SignalDesk Backtests** (upstream's engine, in the browser), **Holdings**
  (decide against the saved book), **Stop/Resume** for runs.

Commit history to know (2026-10-23): `b70a7d2` merge of upstream v0.7.0 with adapter
fixes, `2db658a` claude provider, `b9d5d25` SignalDesk plan + holdings + stop,
`f40eebd` SignalDesk backtests; 2026-11-03: `d44de30` merge of upstream v0.7.1,
then the data-reliability work (Google News vendor, lost-report fix, side-aware
chart levels with outcomes, data coverage). Check
`git log --oneline -10` for anything newer.

## Where things live

| Area | Files |
| --- | --- |
| Graph and lifecycle | `tradingagents/graph/trading_graph.py` (`TradingAgentsGraph`: `create_run_state`, `begin_checkpoint`/`end_checkpoint`, `stream_run`, `record_decision`, `settle_pending`, `save_reports`) |
| Agent state names | `tradingagents/agents/state.py`; decisions are `investment_plan` (Research Manager), `trader_investment_plan`, `final_trade_decision` + `final_rating` (PM) |
| LLM providers | `tradingagents/llm_clients/` — `factory.py` routes providers; `claude_code.py` (CLI bridge) + `claude_client.py` (LangChain model) are fork-owned |
| CLI | `cli/main.py` (commands incl. `backtest`, `login`, `logout`), `cli/prompts.py` (provider menu, `PLAN_PROVIDERS`, sign-in checks), `cli/selections.py` |
| Console ↔ core boundary | `backend/adapters/tradingagents_adapter.py` — the **only** console module that imports `tradingagents` |
| Console API | `backend/app/api/{runs,backtests,portfolio,config,watchlist}.py` |
| Console services | `backend/app/services/{orchestrator,backtests,portfolio,providers,costs,secrets,charts}.py` |
| Console schema | `backend/app/{models,schemas,database,settings}.py` |
| Frontend | `frontend/src/App.tsx` (shell, dashboard, new analysis, run view, settings), `Backtests.tsx`, `Holdings.tsx`, `ModelSetup.tsx`, `Report.tsx` (agent markdown renderer), `shared.tsx`, `api.ts`, `styles.css` |
| Docs | `docs/WEB_CONSOLE.md` (user guide), `docs/ARCHITECTURE.md`, `docs/UPSTREAM_UPGRADE.md` (migration log + every upstream file the fork touches), `docs/TESTING.md`, `KNOWN_UPSTREAM_ISSUES.md` |

`references/architecture.md` has the full map, the run/backtest data flows, the
DB tables and every API route.

## Invariants — and why they exist

1. **Only the adapter imports `tradingagents`.** Upstream renames modules and state
   keys between releases (v0.5.2 moved half the package). One module to repair per
   upgrade keeps merges cheap. Services pass plain dicts/paths across the boundary.
2. **Touch upstream files sparingly, and log it.** Every edit to an upstream file
   is a future merge conflict. Prefer fork-owned modules; when you must edit an
   upstream file, add it to the table in `docs/UPSTREAM_UPGRADE.md`.
3. **The Claude plan only ever runs through Claude Code.** Anthropic keeps plan
   credentials inside Claude Code and claude.ai; using the OAuth token directly
   against the API (as upstream's ChatGPT provider does with OpenAI) is not
   allowed. Never add a direct token flow. Keep stripping every `ANTHROPIC_*`
   variable from the subprocess (an API key makes Claude Code bill the API), and
   keep `SESSION_NOTE` (Claude Code injects today's date; without the note, past
   and backtest runs called their data "weeks stale" and one model even replaced
   the analysis date). Details: `references/claude-plan.md`.
4. **Never run `tradingagents logout claude` or `claude auth logout` while testing** —
   it signs the user's real Claude Code out, including the session you are in.
5. **Point-in-time integrity.** Historical runs must not see later data. Backtest
   cells use a memory log of their own (`results/backtests/<id>/`), never the live
   one, and lessons are filtered to outcomes known by each date (upstream does it;
   don't bypass `create_run_state`/`_memory_step`).
6. **Three portfolio states stay distinct:** no book (`None` → no context), a flat
   book (`positions: []`), a book with positions. Runs store the exact snapshot
   they were launched with; it is part of the checkpoint signature.
7. **Don't regex decision markdown into structured fields.** Upstream keeps only
   rendered text for trader/RM decisions; fields stay null until upstream exposes
   typed values (see `KNOWN_UPSTREAM_ISSUES.md`). The PM rating comes from
   `run_rating()`. Reading the markdown for display is fine (`Report.tsx` tiles,
   `services/charts.py` levels), as long as nothing parsed that way is stored.
8. **Schema changes:** new tables come from `create_all`; new columns on existing
   tables must be **nullable** — `database._add_missing_columns` adds them to
   users' existing SQLite files at start-up and refuses non-nullable ones.
9. **Tests stay offline and isolated.** `tradingagents` loads the developer's
   `.env` on import, so pin config values (debate rounds, provider) in tests. Use
   the fake `claude` executable (`tests/test_claude_plan_provider.py`) and the
   faked analysis/settlement (`backend/tests/test_backtests.py`) instead of real
   calls.

## Environment facts (this machine, verify they still hold)

- Upstream needs **Python ≥ 3.11**. The repo `.venv` was rebuilt on Python 3.13
  on 2026-10-23 (it had been 3.10). If `.venv/bin/python --version` is ever older
  again, the fix is `rm -rf .venv && make install web-install` — the user's call,
  it is their environment; `make venv` refuses an old one. For isolated work build
  a scratch venv: `python3.13 -m venv <scratch>/venv && <scratch>/venv/bin/pip
  install -e ".[dev]" -r backend/requirements-dev.txt` (one pip run).
- The user's **Docker SignalDesk** usually holds `127.0.0.1:8765` (API) and `4173`
  (UI). `make web-dev` would silently reuse that backend. Run your own instance
  with `scripts/isolated_console.sh` (ports 8766/5175, scratch data) instead.
- `claude` may not be on PATH; `claude_code.find_cli()` also finds `~/.local/bin`,
  `~/.claude/local`, the Agent SDK's bundled copy and editor-bundled copies
  (`~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude`).
  `TRADINGAGENTS_CLAUDE_CLI` overrides.
- Since v0.7.1 the whole core suite passes on macOS (the old
  `test_cache_writes.py` failures were fixed upstream).
- Node here is 18; current `playwright-core` needs Node 20, so install
  `playwright-core@1.47.2` for headless screenshots.
- The user's own `make web-backend` may hold `127.0.0.1:8765` with reload on: an
  edit to `backend/` or `tradingagents/` restarts it, so check no run is active
  (`runs.status` queued/running) before editing while it is up.
- Locale is en-AU. The API returns SQLite timestamps without a UTC offset; the
  frontend parses them with `parseTimestamp` (treat as UTC) and builds default
  dates with `localISODate()`. Keep using those helpers.

## Everyday workflows

- **Check everything before you finish:** `scripts/check_all.sh` (core + backend
  tests, ruff, frontend type-check and build). Set `PYTHON=` to a ≥3.11 interpreter
  if `.venv` is old.
- **See the app:** `scripts/isolated_console.sh start` → open
  `http://127.0.0.1:5175`; `scripts/isolated_console.sh stop` when done. Drive it
  headlessly with `playwright-core` + the installed Chrome (recipe in
  `references/testing.md`). Look at the screenshots — a rendered shell can hide
  failing fetches, so also check the console errors and the backend log.
- **Live test on the Claude plan, cheaply:** market analyst only, debate 1/1,
  `claude-haiku-5-5` quick + `claude-sonnet-5-5` deep, effort `low`, a date at
  least ~10 days old. One run is ~11 calls (~100K input tokens); a 2-cell backtest
  ~24 calls. Say so before spending the user's plan usage on more.
- **Merge an upstream release:** follow `references/upstream-sync.md`; the
  checklist there catches the silent regressions earlier merges caused.
- **Add a feature:** recipes for providers, console pages, DB fields, endpoints,
  backtest metrics and upstream config keys are in `references/extending.md`.

## Conventions

- Python: ruff (`E,W,F,I,B,UP,C4,SIM`, line length 100, py311 target) — run
  `ruff check .`; comments explain *why*, briefly, in upstream's plain style.
- Frontend: components are compact (often one-line JSX), new views go in their own
  file, shared helpers in `shared.tsx`, model/provider selection through
  `ModelSetup`, all styling in `styles.css` using the existing palette (paper
  `#faf9f4` cards, ink `#13201d`/`#173f35`, lime `#d9ff7d`, DM Mono figures,
  Newsreader headings). Verify icon names exist in the pinned lucide-react
  (`grep "declare const Name:" node_modules/lucide-react/dist/lucide-react.d.ts`).
- Commits: conventional prefixes (`feat(web):`, `fix(llm):`, ...), a body that
  explains the why, and the attribution line the session's system reminder gives.
  Commit only when the user asks; branch first when on `main`.

## References

- `references/architecture.md` — full file map, run/stop/resume and backtest data
  flows, tables, API routes, section mapping. Read before changing backend flow.
- `references/claude-plan.md` — the `claude` provider end to end: CLI flags,
  tool-call envelope, structured output, errors/retries, discovery, Docker, how to
  probe it live. Read before touching any LLM/provider code.
- `references/testing.md` — suites, fakes, live tests, UI driving, Docker checks,
  last known results.
- `references/extending.md` — step-by-step recipes for common changes.
- `references/upstream-sync.md` — merging an upstream release safely.
- `references/backlog.md` — known issues, open questions and ranked improvement
  ideas. Read when asked "what next" or before proposing improvements.
