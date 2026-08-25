# TradingAgents upstream upgrade procedure

The web adapter is verified against and pinned to **TradingAgents v0.3.1** in
`backend/requirements.txt`.

Before changing that pin:

1. Read the upstream `CHANGELOG.md` between v0.3.1 and the target version.
2. Diff `tradingagents/default_config.py`; update exposed configuration keys and
   provider/vendor discovery without adding a frontend provider catalog.
3. Diff `tradingagents/agents/utils/agent_states.py`; update section mapping for
   any renamed or newly structured accumulated-state fields.
4. Diff `tradingagents/agents/schemas.py` and the manager/trader nodes; update
   structured decision persistence without parsing rendered markdown.
5. Inspect `TradingAgentsGraph`, `Propagator`, and checkpointer signatures. The
   semi-private calls must remain isolated to `run_streaming_analysis()`.
6. Run `backend/tests/test_adapter.py`, then the opt-in local-model integration
   test with `TRADING_CONSOLE_RUN_INTEGRATION=1`.
7. Run one cheap manual analysis with checkpointing on, interrupt it after at
   least one section, resume, and verify the event sequence and report tree.
8. Record the target tag, date, migration notes, and any fallback behavior in
   this document before merging the bump.

If granular streaming breaks, the adapter is required to emit a
`streaming_degraded` event and fall back to blocking `propagate()` rather than
lose the run entirely.
