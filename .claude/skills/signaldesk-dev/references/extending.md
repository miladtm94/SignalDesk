# Recipes for common changes

Each recipe lists the touch points in order. Finish every change with
`scripts/check_all.sh`, and look at the result in a browser when the UI changed.

## Add or change an LLM provider

1. Client: an OpenAI-compatible API is one `ProviderSpec` row in
   `tradingagents/llm_clients/openai_client.py`; anything else gets its own
   `BaseLLMClient` subclass (pattern: `claude_client.py`), routed in
   `factory.create_llm_client` before the OpenAI-compatible fallthrough.
2. Register: key env var in `api_key_env.PROVIDER_API_KEY_ENV` (`None` when there
   is no key), models in `model_catalog.MODEL_OPTIONS` (always end with
   `("Custom model ID", "custom")`), `validators._ANY_MODEL_PROVIDERS` if model IDs
   are open-ended, and `build_llm_kwargs` if it takes a reasoning knob.
3. CLI: an entry in `cli/prompts._llm_provider_table()`; plan providers belong in
   `PLAN_PROVIDERS` (shown under "Subscription"), with a sign-in check in
   `ensure_signed_in`; the effort/thinking question in `cli/selections.py`.
4. Console: availability/status in `backend/app/services/providers.py`, a
   reasoning control in the adapter's `_REASONING_CONTROLS`, a display name in
   `frontend/src/shared.tsx::providerName`, zero rates in `services/costs.py` if
   plan-billed (and `planBilledProviders` in `shared.tsx`).
5. Tests: provider behaviour offline (fakes, never the network); update the CLI
   menu tests in `tests/test_chatgpt_provider.py` when the Subscription submenu
   changes. Log any upstream file you touched in `docs/UPSTREAM_UPGRADE.md`.

## Add a SignalDesk page or view

1. A component file under `frontend/src/` (pattern: `Holdings.tsx`), using
   `PageHeader`, `Metric`, `EmptyState` from `shared.tsx`.
2. Types and calls in `api.ts`.
3. Route it in `App.tsx`: extend the `View` union, add the nav button (lucide icon
   that exists in the pinned version), render it in `<main>`.
4. Styles appended to `styles.css` in a commented section, with the existing
   palette and a ≤650px media rule; check mobile width.
5. Backend: a router in `backend/app/api/` included in `app/main.py`, logic in
   `services/`, anything from `tradingagents` behind an adapter function.

## Add a database field or table

- New table: a model in `backend/app/models.py`; `create_all` creates it.
- New column on an existing table: declare it **nullable**; `init_database` adds
  it to existing databases (`_add_missing_columns`) together with its index.
  Extend `test_an_older_database_gains_the_new_columns` (build today's schema,
  drop the new column, migrate, assert it is back).
- Expose it in `schemas.py` and the `_serialize*` helpers; mirror it in `api.ts`.

## Run something new through the graph

- Keep it in the adapter: build config with `_build_config` (allowlisted keys
  only — add new upstream keys to `allowed`), construct
  `ConsoleTradingAgentsGraph`, use public lifecycle helpers.
- Map new state fields to UI sections in `_SECTION_SOURCES` (and `sectionMeta` in
  `App.tsx`); prefer `*_structured` companions over parsing markdown.
- Long work runs on a thread via the orchestrator (`execute`) or
  `asyncio.to_thread`; publish progress through `_record_and_publish` so the
  WebSocket can replay it.

## Extend backtests

- New per-cell outcome fields: read them in `adapter.backtest_outcomes` from
  memory-log entries, store on `BacktestCell` (nullable), copy in
  `BacktestService._refresh`.
- New aggregate metrics: compute in `services/backtests.score_cells` (pure, unit
  testable) and show in `Backtests.tsx`; keep upstream's `summarize` figures as
  the reference numbers.
- Respect the scope note in `tradingagents/backtest.py`: this evaluates
  decisions; it is not a portfolio simulator (no fills, sizes or cash ledger).
- Anything that would fail every remaining cell the same way belongs in
  `adapter.sweep_blocker`, so the sweep stops instead.

## Expose a new upstream config key in the console

Add it to `_build_config`'s `allowed` set, the request schema
(`AnalysisSettings` in `schemas.py`, so runs and backtests share it), the
frontend payload types, the form, and the reproducibility record in `LiveRun`.
Mind the checkpoint signature: any config change starts a checkpointed run
fresh, which is intended.

## Commit

Branch off `main` for anything non-trivial, keep commits topical and green (each
should pass `scripts/check_all.sh`), write the why in the body, and end with the
attribution line the session provides. Push only when the user asks.
