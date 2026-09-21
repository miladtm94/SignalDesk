# TradingAgents upstream upgrade procedure

The web adapter is verified against **TradingAgents v0.5.0**. The backend image
installs the package from this repository so the core and console cannot drift
to different versions.

## v0.5.0 migration (2026-09-22)

- Updated the provider/model catalog to upstream's v0.5.0 lineup while keeping
  web-only reasoning-control metadata inside the adapter.
- Switched streaming runs to `create_run_state()`, `begin_checkpoint()`,
  `checkpoint_input()`, `record_decision()`, and
  `clear_checkpoint_on_success()`. This preserves point-in-time memory context
  and correctly resumes LangGraph checkpoints without duplicating messages.
- Kept the console's output-language catalog and report persistence as
  fork-owned extensions; upstream still stores rendered markdown in graph
  state rather than retaining the typed decision objects.
- Verified the backend suite and production frontend build. The opt-in live
  model/resume exercise remains a release check because it requires provider
  credentials and a deliberate interruption.

Before changing that pin:

1. Read the upstream `CHANGELOG.md` between the currently recorded version and
   the target version.
2. Diff `tradingagents/default_config.py`; update exposed configuration keys and
   provider/vendor discovery without adding a frontend provider catalog.
3. Diff `tradingagents/agents/utils/agent_states.py`; update section mapping for
   any renamed or newly structured accumulated-state fields.
4. Diff `tradingagents/agents/schemas.py` and the manager/trader nodes; update
   structured decision persistence without parsing rendered markdown.
5. Inspect `TradingAgentsGraph`, `Propagator`, and checkpointer signatures. Keep
   version-sensitive calls isolated to `run_streaming_analysis()` and prefer
   the graph's public lifecycle helpers over assembling state manually.
6. Run `backend/tests/test_adapter.py`, then the opt-in local-model integration
   test with `TRADING_CONSOLE_RUN_INTEGRATION=1`.
7. Run one cheap manual analysis with checkpointing on, interrupt it after at
   least one section, resume, and verify the event sequence and report tree.
8. Record the target tag, date, migration notes, and any fallback behavior in
   this document before merging the bump.

If granular streaming breaks, the adapter is required to emit a
`streaming_degraded` event and fall back to blocking `propagate()` rather than
lose the run entirely.
