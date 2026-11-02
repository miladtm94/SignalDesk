# Merging an upstream TradingAgents release

The fork follows `upstream/main` (TauricResearch). Earlier merges broke the
console silently (sections stopped appearing, a helper vanished), so treat a
merge as a migration, not a pull.

## Procedure

1. `git fetch upstream --tags` and `git log --oneline main..upstream/main`; read
   upstream `CHANGELOG.md` for the range.
2. Branch: `git checkout -b signaldesk/vX.Y main`, then
   `git merge --no-ff --no-commit upstream/main`.
3. Resolve conflicts keeping both sides. Usual hotspots: `README.md` (fork
   sections vs upstream news/usage), `cli/prompts.py` (fork's `PLAN_PROVIDERS`,
   Claude menu entry, shared output-language list), `cli/main.py` (login/logout
   for claude), `docker-compose.yml` (console services, `WITH_CLAUDE_CODE`,
   `CLAUDE_CONFIG_DIR`, `./reports` mount), `Dockerfile`, `pyproject.toml`
   (`claude` extra), the two upstream tests the fork edited
   (`tests/test_chatgpt_provider.py`, `tests/test_cli_commands.py`). The full list
   is in `docs/UPSTREAM_UPGRADE.md`.
4. Before committing, check the adapter against the new release — the things that
   changed in past releases:
   - module moves (imports at the top of `tradingagents_adapter.py`);
   - state names (`agents/state.py`) → `_SECTION_SOURCES`;
   - `TradingAgentsGraph` lifecycle (`create_run_state`, `begin_checkpoint`,
     `stream_run`, `record_decision`, `clear_checkpoint_on_success`) and the
     `create_tier_client` name the console wraps;
   - `backtest.py` (`iter_grid`, `summarize`), `memory/` (`load_entries` fields,
     `settle_pending`), `portfolio.py`;
   - LLM registry changes that could shadow or reorder `claude` (`factory.py`,
     `SIGNED_IN_PROVIDERS`), and model catalog refreshes (update
     `MODEL_OPTIONS["claude"]` if Anthropic released models);
   - dependency pins: upstream may require newer pydantic/FastAPI-adjacent
     packages than `backend/requirements.txt` pins (v0.7.0 needed pydantic
     2.12.5). Resolve everything in one pip run and import the app.
5. Run `scripts/check_all.sh`. `backend/tests/test_adapter_graph.py` runs the
   real graph offline through the adapter and asserts every section appears —
   it is the test that catches state renames.
6. Commit the merge with the adapter fixes (message pattern: "Merge upstream
   vX.Y and update web adapter", listing each adaptation and why).
7. Smoke it live: one cheap analysis with checkpointing on, stop it after a
   section, resume; a 2-cell backtest that settles. Then record the version,
   date and notes in `docs/UPSTREAM_UPGRADE.md`.
8. Fast-forward `main` when the user agrees; push only when asked.

## History

- v0.5.0 (2026-09-22): adapter moved to `create_run_state`/checkpoint helpers.
- v0.7.0 (2026-10-23): parallel analysts (`stream_run`), `judge_decision` removed
  (sections now from `investment_plan`/`final_trade_decision`), `process_signal`
  removed (`run_rating`), `_get_provider_kwargs` removed (timeout via the tier
  factory), module moves, Python ≥3.11, pydantic 2.12.5; state logs fixed to land
  in `results/`.
- v0.7.1 (2026-11-03): clean merge; `ConsolePortfolio.render` adapted to one line
  per holding (broker aliases); health reads `tradingagents.__version__` (editable
  installs keep stale metadata); macOS suite passes. Fork fixes to upstream files
  made alongside are listed in `docs/UPSTREAM_UPGRADE.md`.
