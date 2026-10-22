# TradingAgents upstream upgrade procedure

The web adapter is verified against **TradingAgents v0.7.0**. The backend image
installs the package from this repository so the core and console cannot drift
to different versions.

## v0.7.0 migration (2026-10-23)

- Merged v0.5.2, v0.6.0 and v0.7.0. Upstream now needs Python 3.11+ and pandas 3;
  `make venv` refuses an older `.venv`, and the backend image runs Python 3.13.
- Imports moved: `load_ohlcv` to `dataflows.vendors.yahoo.ohlcv`,
  `normalize_symbol` to `dataflows.symbols`.
- Streaming moved to `stream_run()`: analysts now run in parallel subgraphs and
  each analyst's report arrives on its own, so chunks are merged as the CLI does.
- The debate states no longer carry `judge_decision`. The Research Manager
  section now reads `investment_plan` and the Portfolio Manager section
  `final_trade_decision`; without this both sections silently disappeared.
- `process_signal()` was removed; the rating comes from `run_rating()` and
  `record_decision()` writes the state log itself.
- `_get_provider_kwargs()` was removed; the console's request timeout is applied
  by wrapping the tier-client factory (`create_tier_client`).
- The console's JSON state logs went to `reports/<TICKER>/` because
  `results_dir` was derived from the report directory; they now go to the data
  directory's `results/` as documented.
- `pydantic` is pinned to 2.12.5, which upstream's `google-genai` requires.
- New console features: the `claude` (Claude plan) provider, holdings, run
  stop/resume, and backtests. `backend/tests/test_adapter_graph.py` runs the real
  graph through the adapter offline and catches state-name regressions like the
  one above.

## Upstream files this fork changes

Expect merge conflicts here; keep both sides.

| File | Why |
| --- | --- |
| `tradingagents/llm_clients/claude_code.py`, `claude_client.py` | The `claude` provider (fork-owned files) |
| `tradingagents/llm_clients/factory.py` | Routes `claude`; `EFFORT_PROVIDERS` sends `anthropic_effort` to it |
| `tradingagents/llm_clients/api_key_env.py`, `validators.py`, `model_catalog.py` | Registers `claude` (no key, any model ID, its model list) |
| `cli/prompts.py` | `PLAN_PROVIDERS`, the Claude entry under "Subscription", its pre-run sign-in check; the shared output-language list |
| `cli/main.py` | `login claude` / `logout claude` |
| `cli/selections.py` | The effort question for `claude` |
| `tests/test_chatgpt_provider.py`, `tests/test_cli_commands.py` | The Subscription submenu lists Claude; `claude` is now a valid plan, so the invalid-plan example is `gemini` |
| `tradingagents/output_languages.py` | Shared CLI/console language list (fork-owned file) |
| `tradingagents/dataflows/vendors/yahoo/common.py` | `hide_yfinance_inplace_deprecations()`: silences pandas 3.1's `inplace` deprecation raised inside yfinance 1.7.0; drop it once yfinance stops using `inplace` |
| `pyproject.toml` | The `claude` extra (Agent SDK's bundled Claude Code) |
| `Dockerfile`, `docker-compose.yml` | `WITH_CLAUDE_CODE` build arg, `CLAUDE_CONFIG_DIR`, console services |
| `README.md`, `.env.example`, `Makefile` | Fork documentation and targets |

## Before changing the pin

1. Read the upstream `CHANGELOG.md` between the currently recorded version and
   the target version.
2. Diff `tradingagents/default_config.py`; update exposed configuration keys and
   provider/vendor discovery without adding a frontend provider catalog.
3. Diff `tradingagents/agents/state.py`; update `_SECTION_SOURCES` in the adapter
   for any renamed or newly structured state fields.
4. Diff `tradingagents/agents/schemas.py` and the manager/trader nodes; update
   structured decision persistence without parsing rendered markdown.
5. Inspect `TradingAgentsGraph`, `Propagator`, `stream_run`, the checkpointer,
   `tradingagents/backtest.py`, `tradingagents/portfolio.py` and
   `tradingagents/memory/`. Keep version-sensitive calls inside the adapter and
   prefer the graph's public lifecycle helpers over assembling state manually.
6. Diff the LLM client registry (`factory.py`, `openai_client.py`'s
   `SIGNED_IN_PROVIDERS`) and the CLI provider menu; keep `claude` registered.
7. Run the upstream suite, `backend/tests` (including the offline real-graph
   test), the frontend build, then the opt-in local-model integration test with
   `TRADING_CONSOLE_RUN_INTEGRATION=1`.
8. Run one cheap manual analysis with checkpointing on, stop it after at least
   one section, resume, and verify the event sequence and report tree. Run a
   two-cell backtest and check that it settles and scores.
9. Record the target tag, date, migration notes, and any fallback behavior in
   this document before merging the bump.

If granular streaming breaks, the adapter is required to emit a
`streaming_degraded` event and fall back to blocking `propagate()` rather than
lose the run entirely.
