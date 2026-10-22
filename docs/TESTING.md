# Testing this release step by step

How to see the merged upstream v0.7.0, the Claude plan provider, SignalDesk
backtests and holdings working, from a terminal or with Docker. Each step says
what you should see.

**Plan usage.** Steps that call a model run on your Claude plan. The cheap
settings used below (one analyst, one debate round each, Haiku 5.5 for the quick
tier, Sonnet 5.5 for the deep tier, low effort) cost about 11 model calls per
analysis and about 24 for the 2-cell backtest. Everything in "Automated checks"
is offline and free.

---

## 0. Before you start

```bash
git checkout main
git log --oneline -4
```

You should see the three feature commits on top of the v0.7.0 merge:
`feat(web): evaluate decisions over time…`, `feat(web): decide against your
book…`, `feat(llm): run on a Claude Pro or Max plan…`, `Merge upstream v0.7.0…`.

Claude Code must be installed and signed in to your Pro or Max plan for the
Claude steps. TradingAgents finds it on PATH, in `~/.local/bin`, or the copy
bundled with the VS Code extension. Without one, `make install-claude` installs
the copy the Agent SDK ships.

---

## A. From a terminal

### A1. Environment

```bash
.venv/bin/python --version          # must be 3.11 or later
```

If it is older: `rm -rf .venv && make install web-install`.

### A2. Automated checks (offline)

```bash
.claude/skills/signaldesk-dev/scripts/check_all.sh
```

Expect `All checks passed.` It runs the core suite (about 1,390 tests; on a Mac
it skips the three Windows-lock tests that cannot pass there), the console suite
(58 tests), lint, and the frontend type-check and build. The individual targets
are `make test`, `make web-test`, `make lint` and `make web-build`.

### A3. Sign in to the Claude plan

```bash
.venv/bin/tradingagents login claude
```

Expect `Claude Code is already signed in to your Claude plan as … (Pro plan).`
If it is not signed in, the command opens Claude Code's own login in your
browser. (`tradingagents logout claude` signs Claude Code itself out, so don't
run it unless you mean to.)

### A4. One analysis on the Claude plan (CLI)

```bash
export TRADINGAGENTS_LLM_PROVIDER=claude \
  TRADINGAGENTS_QUICK_THINK_LLM=claude-haiku-5-5 \
  TRADINGAGENTS_DEEP_THINK_LLM=claude-sonnet-5-5 \
  TRADINGAGENTS_ANTHROPIC_EFFORT=low \
  TRADINGAGENTS_OUTPUT_LANGUAGE=English \
  TRADINGAGENTS_MAX_DEBATE_ROUNDS=1 TRADINGAGENTS_MAX_RISK_ROUNDS=1
.venv/bin/tradingagents --ticker NVDA --date 2026-09-15 --analysts market --no-save --no-show
```

Expect `✓ Using your Claude plan through Claude Code: … (Pro plan)` before the
run, the live agent display, and a final rating after about three minutes.
Exported variables override the values in your `.env`.

Interactively: run `.venv/bin/tradingagents`, and at "Step 6: LLM Provider"
choose **Subscription (use your ChatGPT or Claude plan)** → **Claude (Pro or Max
plan, through Claude Code)**.

### A5. Decide against your book (CLI)

```bash
cat > /tmp/book.json <<'JSON'
{"cash": 25000, "currency": "USD",
 "positions": [{"ticker": "NVDA", "quantity": 120, "average_price": 150}]}
JSON
.venv/bin/tradingagents --ticker NVDA --date 2026-09-15 --analysts market --no-save --show --portfolio /tmp/book.json
```

In the final report, the Trader and the Portfolio Manager should talk about your
120 shares, your average price of 150 and the 25,000 USD cash.

### A6. A backtest from the terminal

With the A4 variables still exported:

```bash
.venv/bin/tradingagents backtest NVDA --start 2026-09-01 --end 2026-09-15 --every 14 --analysts market
```

Expect `[1/2] NVDA 2026-09-01`, `[2/2] NVDA 2026-09-15`, then a summary such as
`Resolved cells: 2 · pending: 0` with each rating's mean alpha against SPY, and
the `--run-id` to continue the sweep. `make backtest SYMBOL=NVDA DATES="2026-09-01
2026-09-15"` does the same for dates you list, with every analyst.

### A7. Start SignalDesk locally

Your Docker console usually holds ports 8765 and 4173. Either stop it
(`docker compose stop web-backend web-frontend`) and run `make web-dev`, then
open http://127.0.0.1:5174, or run beside it:

```bash
TRADING_CONSOLE_PORT=8766 TRADING_CONSOLE_FRONTEND_PORT=5175 make web-dev
```

Then open http://127.0.0.1:5175. This uses your real console database
(`backend/data/`); its new columns are added automatically at start-up and your
history is kept. For a throwaway database instead, use
`.claude/skills/signaldesk-dev/scripts/isolated_console.sh start` (and `stop`).

### A8. Walk through the new features in the browser

1. **Settings → Subscriptions.** "Claude Pro or Max" shows **Ready**, with your
   masked account, the plan and the Claude Code version. Optionally, under
   "Active AI provider & model defaults", choose **Claude plan**, pick models,
   and save.
2. **Holdings.** Add a position or two (for example NVDA, quantity 120, average
   price 150, and a short such as AAPL −10), set the free cash to 25000 and the
   currency to USD, then press **Save holdings**. Each row gets its latest close
   and unrealized P/L, and **What the agents read** shows the exact text the
   decision agents will receive for the chosen ticker.
3. **New analysis.** Choose provider **Claude plan**. For a cheap run, select
   only the Market analyst, Quick **Haiku 5.5**, Deep **Sonnet 5.5**, effort
   **low**. Under **04 Portfolio context** choose **Decide against my book**; the
   preview shows your book. The estimate card reads **Plan · billed**. Press
   **Run Analysis**.
4. **Run view.** Sections appear as each agent finishes. Press **Stop**: the
   status becomes *stopped*. Press **Resume**: it continues from the last saved
   step. When it completes, the record shows **Portfolio: Decided against your
   book**, the **Portfolio context** card shows what the agents read, the
   Portfolio Manager's decision refers to your position, and usage reads
   **Billed to plan · ≈ $… at API prices**.
5. **Backtests → New backtest.** Keep NVDA, set **First date** 01/09/2026 and
   **Last date** 15/09/2026, choose **14d**. The preview reads **2 dates × 1
   ticker = 2 runs**. Select only Market, Claude plan with Haiku 5.5 / Sonnet 5.5,
   effort low, optionally **Decide against my book**, give it a name, and press
   **Run 2 cells**.
6. **Backtest page.** The first cell pulses while running (**Watch live** opens
   its run view), the second waits. After about five minutes the status is
   **Completed**: scored decisions **2/2**, mean alpha against SPY, the grid
   coloured by alpha, the score by rating, and every decision with its return and
   alpha. Click a grid cell to read its reflection, then **Open the full run**.
7. **Stop and resume a sweep.** Start another backtest, press **Stop** while a
   cell runs (status **stopped**), then **Resume**: the stopped and unstarted
   cells run, and the finished ones are kept.
8. **Settle later.** Cells dated within the last week stay **pending** because
   their holding window has not traded yet. Come back after it has and press
   **Settle N pending**.

Backtest cells don't appear in the Overview history; they live on their
backtest. Reports are saved under `reports/<run id>/`.

---

## B. With Docker

### B1. Give the containers a Claude plan token

Containers have no browser, so they use a long-lived token from Claude Code:

```bash
.venv/bin/tradingagents login claude --token     # runs `claude setup-token`
```

Sign in when the browser opens, copy the printed token and add it to `.env`:

```
CLAUDE_CODE_OAUTH_TOKEN=<the token>
```

You can instead paste it later in SignalDesk under Settings → Provider
credentials → `CLAUDE_CODE_OAUTH_TOKEN`; it is stored encrypted.

### B2. Rebuild and start SignalDesk with Claude Code inside

```bash
WITH_CLAUDE_CODE=1 docker compose up --build -d web-backend web-frontend
```

This replaces the running containers with the new code (the build downloads
Claude Code, about 125 MB). Your history in the `research_console_data` volume is
kept and migrated at start-up.

### B3. Check it

```bash
curl -s http://127.0.0.1:8765/api/health
curl -s http://127.0.0.1:8765/api/config/subscriptions/claude
```

Expect `{"status":"ok","upstream":"TradingAgents v0.7.0"}`, then
`"installed":true` with Claude Code's path and version, and `"ready":true` once
the token is set. If you saved the token in Settings, press **Check again** on
the Subscriptions card.

Open http://127.0.0.1:4173 and repeat the walkthrough in A8.

### B4. The CLI in Docker

```bash
WITH_CLAUDE_CODE=1 docker compose build tradingagents
docker compose run --rm \
  -e TRADINGAGENTS_LLM_PROVIDER=claude \
  -e TRADINGAGENTS_QUICK_THINK_LLM=claude-haiku-5-5 \
  -e TRADINGAGENTS_DEEP_THINK_LLM=claude-sonnet-5-5 \
  -e TRADINGAGENTS_ANTHROPIC_EFFORT=low -e TRADINGAGENTS_OUTPUT_LANGUAGE=English \
  -e TRADINGAGENTS_MAX_DEBATE_ROUNDS=1 -e TRADINGAGENTS_MAX_RISK_ROUNDS=1 \
  tradingagents --ticker NVDA --date 2026-09-15 --analysts market --no-save --no-show
```

The same `-e` options with `tradingagents backtest NVDA --start 2026-09-01 --end
2026-09-15 --every 14 --analysts market` run a backtest in the container.

---

## Troubleshooting

| You see | Do this |
| --- | --- |
| `make venv`: "needs 3.11 or later" | `rm -rf .venv && make install web-install` |
| "Claude Code is not installed, or not on PATH" | install Claude Code, run `make install-claude`, or set `TRADINGAGENTS_CLAUDE_CLI` to its path; in Docker rebuild with `WITH_CLAUDE_CODE=1` |
| "Claude Code is not signed in to a Claude plan" | `tradingagents login claude`; in Docker set `CLAUDE_CODE_OAUTH_TOKEN` |
| "signed in to Anthropic API billing, not a Claude plan" | Claude Code is logged in with a Console account: `claude auth login --claudeai` |
| "Your Claude plan's usage limit is reached" | wait for the reset, then Resume the run or the backtest |
| "Claude Code cannot use this model" | choose another model; your plan may not include it |
| The backend container exits at start-up | `docker compose logs web-backend`, then rebuild with the commands above |
| `make web-dev` shows old pages | the Docker console is answering on 8765: stop it or use the port variables in A7 |
