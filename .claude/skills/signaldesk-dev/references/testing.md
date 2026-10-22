# Testing reference

## Last known results (2026-10-23, `main` at `f40eebd` + skill/docs)

- Core suite (`tests/`): 1394 passed, 1 skipped; the 3 `test_on_windows_*`
  tests in `tests/test_cache_writes.py` fail on macOS only (Windows `errno`).
- Console suite (`backend/tests/`): 58 passed (1 opt-in integration test deselected).
- `ruff check .`: clean. Frontend `tsc -b` and `vite build`: clean.
- Live on a Claude Pro plan: one full analysis (11 calls) and a 2-cell backtest
  through the UI (24 calls, ~202K input / 54K output tokens, ≈ $0.18 at API
  prices), both scored and decided against a saved book.
- Docker: both images built with `WITH_CLAUDE_CODE=1`; the console started, found
  the bundled Claude Code 2.1.308, and accepted `CLAUDE_CODE_OAUTH_TOKEN` from
  Settings (no live call made in the container).

Run `scripts/check_all.sh` (or with `PYTHON=...`) to reproduce the first four.

## The suites

| Command | What it covers |
| --- | --- |
| `.venv/bin/python -m pytest tests` | upstream + fork core: providers (incl. `test_claude_plan_provider.py` with a fake `claude`), CLI, graph end-to-end with scripted models, dataflows |
| `.venv/bin/python -m pytest backend/tests` | console: adapter (mocked graph and `test_adapter_graph.py`, the **real graph offline**), API lifecycle, backtests (faked analysis/settlement), holdings, plans, migration of an old DB, charts, costs, dev launcher |
| `TRADING_CONSOLE_RUN_INTEGRATION=1 ... -m integration` | opt-in live test with a local Ollama model |
| `cd frontend && npx tsc -b && npm run build` | types and bundle |

Isolation rules the suites rely on:
- `tests/conftest.py` blanks `TRADINGAGENTS_*` vars, blocks sockets, puts results
  and caches in temp dirs, and sets dummy API keys (so `ANTHROPIC_API_KEY` is
  present — handy for proving it is stripped).
- `backend/tests/conftest.py` sets `TRADING_CONSOLE_DATA_DIR` and
  `TRADING_CONSOLE_REPORTS_DIR` to temp dirs (otherwise tests write into the
  repo's `reports/`) and resets the launch rate limiter per test.
- The package loads the developer's `.env` on import: pin `max_debate_rounds`,
  provider and the like in any test that counts calls or sections.
- Background work in `TestClient`: poll the API until the job's status changes
  (`_wait` in `test_backtests.py`); don't sleep a fixed time.

## Fakes worth reusing

- **Fake Claude Code** (`tests/test_claude_plan_provider.py::FakeClaude`): a
  Python script with a shebang to the test interpreter, pointed to by
  `TRADINGAGENTS_CLAUDE_CLI`; it logs each call (argv, stdin, system prompt, cwd,
  `ANTHROPIC*`/`CLAUDE*` env) and replays queued JSON results. Helpers:
  `answer()`, `structured()`, `failure(result, status)`.
- **Scripted graph model** (`backend/tests/test_adapter_graph.py::ScriptedModel`
  and the `offline` fixture): every bound tool called once, fixed structured
  outputs, all vendors answered locally — drives the real `TradingAgentsGraph`
  through the adapter in about a second.
- **Faked analysis/settlement** (`backend/tests/test_backtests.py`): writes real
  memory-log entries and outcomes, so the backtest service, scoring and API are
  exercised without a model.

## Live tests on the Claude plan (spend the user's usage deliberately)

From the repo root with the ≥3.11 venv:

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
config = {**DEFAULT_CONFIG, "llm_provider": "claude", "quick_think_llm": "claude-haiku-5-5",
          "deep_think_llm": "claude-sonnet-5-5", "max_debate_rounds": 1, "max_risk_discuss_rounds": 1,
          "anthropic_effort": "low", "checkpoint_enabled": False}
state, rating = TradingAgentsGraph(["market"], config=config).propagate("NVDA", "2026-09-15")
```

Set `TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_CACHE_DIR` and
`TRADINGAGENTS_MEMORY_LOG_PATH` to a scratch dir first so the user's
`~/.tradingagents` memory log is untouched. Check the reports for date leakage:
`grep -iE "stale|today's date|current date"` should find nothing in a past-dated
run. Probing one model costs a few hundred cached tokens:
`claude_code.complete("Reply with OK.", system="Answer briefly.", model=...)`.

## Driving the UI

`chromium-cli` and Playwright browsers are not installed here; use
`playwright-core` with the installed Chrome:

```bash
mkdir -p <scratch>/pw && cd <scratch>/pw && npm init -y >/dev/null && npm i playwright-core
```

```js
import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 1000 } })).newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("response", (r) => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
await page.goto("http://127.0.0.1:5175");
await page.waitForSelector("text=Your signal archive");
await page.click('nav button:has-text("Backtests")');
await page.screenshot({ path: "shot.png" });          // fullPage: true distorts the fixed sidebar
console.log(errors); await browser.close();
```

Selectors that work: nav buttons by text; form fields as
`label:has-text("Quick model") select`; holdings inputs by `aria-label`
(`Ticker`, `Quantity`, `Average price`); `.heat-cell`, `.cell-detail`,
`.plans-card`. Use `fill`/`selectOption` (React controlled inputs). A console 404
for `/favicon.ico` is expected (no favicon). Read the screenshots, and check the
backend log in `STATE_DIR` for tracebacks.

## Docker checks without touching the user's stack

Build under a test tag and run on a spare port with a throwaway volume:

```bash
docker build -f backend/Dockerfile --build-arg WITH_CLAUDE_CODE=1 -t signaldesk-backend:test .
docker run -d --name sd-test -p 127.0.0.1:8767:8000 -e TRADING_CONSOLE_HOST=0.0.0.0 \
  -e CLAUDE_CONFIG_DIR=/app/backend/data/claude-code -v sd-test-data:/app/backend/data signaldesk-backend:test
curl -s http://127.0.0.1:8767/api/health; curl -s http://127.0.0.1:8767/api/config/subscriptions/claude
docker exec sd-test python -c "from tradingagents.llm_clients import claude_code as c; print(c.find_cli())"
docker rm -f sd-test && docker volume rm sd-test-data && docker rmi signaldesk-backend:test
```

A container that exits at start-up: read `docker logs`. The 2026-10-23 failure
(Starlette 1.x pulled in by the Agent SDK's `mcp`) is the pattern to expect when
dependencies are installed in more than one pip resolution.
