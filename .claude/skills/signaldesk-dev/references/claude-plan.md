# The `claude` provider (Claude Pro/Max plan through Claude Code)

## Why it is built this way

Anthropic keeps consumer-plan credentials inside Claude Code and claude.ai.
Upstream's ChatGPT provider signs in itself and calls the API with the plan token;
doing the same with a Claude token is not allowed, so this provider never touches
the credential. It runs the person's own Claude Code headless — a supported, local,
personal use. Documentation says plan access must not be offered to other people
(products should use the `anthropic` provider with an API key). Keep it that way.

## Files

- `tradingagents/llm_clients/claude_code.py` — CLI discovery (`find_cli`,
  `cli_version`, `ensure_supported`, `MIN_VERSION` 2.1.0), sanitized environment
  (`cli_environment`), sign-in (`auth_status` cached 60 s, `forget_status`,
  `on_plan`, `describe`, `require_plan`, `sign_in`, `sign_out`), one call
  (`complete` → `Completion`), error classes and `_failure` classification.
- `tradingagents/llm_clients/claude_client.py` — `ChatClaudeCode(BaseChatModel)`
  (`bind_tools`, `with_structured_output`, `_generate`, `_ask` retries),
  `ClaudeCodeClient(BaseLLMClient)`, `render`, `tool_envelope`,
  `tool_instructions`, `SESSION_NOTE`, `supports_effort`.
- Wiring: `factory.py` (`provider == "claude"`, `EFFORT_PROVIDERS`),
  `api_key_env.py` (`"claude": None`), `validators.py` (any model ID),
  `model_catalog.py` (`MODEL_OPTIONS["claude"]`), CLI (`cli/prompts.py`
  `PLAN_PROVIDERS`/`_ensure_claude_plan`, `cli/main.py` login/logout,
  `cli/selections.py` effort question), console (adapter `claude_plan_status`,
  `_REASONING_CONTROLS["claude"]`, timeout floor; `services/providers.py`;
  `PlanSettings` and `ModelSetup` plan note in the frontend).

## One call

```
claude -p --output-format json --model <id> --system-prompt-file <tmp>/system.md \
  --tools "" --strict-mcp-config --setting-sources "" --disable-slash-commands \
  --no-session-persistence --max-turns 1|4 [--effort <level>] [--json-schema <json>]
```

- Prompt on **stdin**, system prompt in a **file** (analyst prompts exceed the
  ~128 KB per-argument limit on Linux). Run in an empty private cwd
  (`_workdir()`); `start_new_session` so a timeout kills the whole group.
- Verified isolation (stream-json `init`): no tools, no MCP servers, no slash
  commands/skills, only Claude Code's built-in plugins; `apiKeySource: none`.
- `--max-turns 4` only with `--json-schema`: a structured answer is a checked
  StructuredOutput tool call that Claude Code re-asks while invalid.
- Environment: drop all `ANTHROPIC_*`, `CLAUDECODE`, `CLAUDE_PID`,
  `CLAUDE_EFFORT`, `CLAUDE_AGENT_SDK*`, every `CLAUDE_CODE_*` except
  `CLAUDE_CODE_OAUTH_TOKEN`; keep `CLAUDE_CONFIG_DIR`; add
  `DISABLE_AUTOUPDATER=1`; `max_tokens` → `CLAUDE_CODE_MAX_OUTPUT_TOKENS`.
- Concurrency: a process-wide semaphore (`TRADINGAGENTS_CLAUDE_MAX_PARALLEL`,
  default 4) — the analysts run in parallel.
- Result JSON fields used: `is_error`, `subtype`, `result`, `structured_output`,
  `api_error_status`, `errors`, `usage` (input + cache creation + cache read =
  input tokens), `modelUsage` (resolved model), `total_cost_usd` (API-equivalent
  only; the plan bills nothing per call), `duration_ms`, `stop_reason`.
  An invalid model comes back `is_error: true`, `subtype: "success"`,
  `api_error_status: 404` — never trust `subtype` alone.

## How LangChain calls map

- Plain `invoke` (researchers, debaters, managers' fallback, reflection): one
  message → prompt as-is; longer conversations are written as a transcript
  (`<user>`, `<assistant>` with `<tool_call name call_id>`, `<tool_result name
  call_id>`) ending "Write the assistant's next turn".
- `bind_tools` (analysts): `--json-schema` envelope `{content, tool_calls:[{name
  (enum), arguments (tool schema)}]}`; `tool_choice` any/required/name → `minItems`
  and a single allowed tool. The system prompt gets `tool_instructions`, which say
  plainly the tools are **not** callable directly — without that, Claude first
  tried native tool calls ("No such tool available") and wasted a turn. Calls
  come back as `AIMessage.tool_calls` with fresh ids; the graph's ToolNode runs
  them as for any provider.
- `with_structured_output(Schema)` (Research Manager, Trader, PM, Sentiment):
  `--json-schema` of the Pydantic schema (`convert_to_openai_tool` dereferences
  `$ref`), validated with `model_validate`; a failure raises so upstream's
  `invoke_structured` falls back to free text. `include_raw` supported.
- `SESSION_NOTE` is appended to every system prompt: Claude Code attaches the
  machine's environment and **today's date** even under a custom system prompt.

## Errors and retries (`_failure`, `_ask`)

| Class | When | Behaviour |
| --- | --- | --- |
| `ClaudeUsageLimit` | 429 or limit wording ("usage limit", "hit your limit", "resets …") | no retry; run fails resumable; a backtest stops (`sweep_blocker`) |
| `ClaudeSignInError` | 401/403, `/login`, "OAuth token", "expired" … | no retry; message names `tradingagents login claude` |
| `ClaudeCodeError` (model) | 404 / "selected model" | "Choose another model" |
| `ClaudeCodeUnavailable` | ≥500, overloaded, network, timeout, `error_during_execution` without detail | retried with backoff up to `max_retries` (console: 1) |
| `ClaudeStructureError` | `error_max_structured_output_retries`/`error_max_turns`, or no `structured_output` | asked once more |
| `ClaudeCodeNotFound` | no executable / it vanished | re-discovered once (editor updates replace their copy) |

The plan-limit wording has not been seen live yet; when a real limit happens,
check the message is classified and extend `_LIMIT` if not.

## Discovery order (`find_cli`)

`TRADINGAGENTS_CLAUDE_CLI` → `claude` on PATH → `~/.claude/local`,
`~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin` → the Agent SDK's
`claude_agent_sdk/_bundled/claude` (`pip install ".[claude]"`, `make
install-claude`, Docker `WITH_CLAUDE_CODE=1`) → editor-bundled copies
(`~/.vscode*`, `~/.cursor`, `~/.windsurf` extensions), newest first.

Install the SDK **together** with the console's pins (one pip resolution, as the
Dockerfiles and `make install-claude` do): on its own, its `mcp` dependency
upgrades Starlette to 1.x and the pinned FastAPI 0.115.6 crashes at import
(`Router.__init__() got an unexpected keyword argument 'on_startup'`).

## Sign-in

- Local: `tradingagents login claude` → `claude auth login --claudeai` (browser).
  Status: `claude auth status --json` (`loggedIn`, `authMethod` — `claude.ai` for
  a plan; API-billing methods are refused — `email`, `subscriptionType`).
- From the browser (the Docker route): Settings → **Sign in with Claude**.
  `claude_code.start_browser_sign_in()` runs `claude auth login --claudeai` on a
  pty (wide, so the link is one line; `BROWSER=true`), returns the
  `…/oauth/authorize?code=true…` link it prints; `finish_browser_sign_in(code)`
  types the pasted code + CR, waits for exit, then checks `on_plan`. One pending
  login per process (`_login`, `LOGIN_WINDOW` 600 s; a new start replaces it).
  Claude Code stores and refreshes the credential in `CLAUDE_CONFIG_DIR` (the data
  volume in Docker), so it survives rebuilds; the console never sees a token.
  Endpoints: `POST/DELETE /api/config/subscriptions/claude/sign-in`, `POST
  …/sign-in/code`. POSIX only (Windows: terminal login). Tests drive the fake CLI
  on a real pty (`test_a_browser_sign_in_*`).
- Headless/Docker alternative: `claude setup-token` on a machine with a browser (or
  `tradingagents login claude --token`); set `CLAUDE_CODE_OAUTH_TOKEN` in `.env` or
  SignalDesk Settings → Provider credentials. A token in the environment is
  trusted by `require_plan`; the first call reports a bad one.
- `logout claude` signs Claude Code itself out (it confirms at a terminal).

## Models (verified on a Pro plan, 2026-10-23)

`claude-sonnet-5-5`, `claude-opus-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`
all answer; aliases `sonnet`, `opus`, `haiku`, `fable` resolve to the latest.
`--effort` is sent only to models that take it (`supports_effort`: Opus ≥4.5,
Sonnet ≥4.6, Fable, aliases except haiku). To re-check availability cheaply, run
`claude_code.complete("Reply with OK.", system="Answer briefly.", model=m)` for
each model (a few hundred cached tokens each).

## Testing it

- Offline: `tests/test_claude_plan_provider.py` writes a fake `claude` script
  (records argv/stdin/env/system prompt, answers from a JSON queue, simulates
  `auth status/login/logout/setup-token`, `--version`) and points
  `TRADINGAGENTS_CLAUDE_CLI` at it. Add a case there for any behaviour change.
  Note `subprocess.run` patches are global — pass unrelated calls through.
- Live probe from the repo root with a ≥3.11 venv: see `testing.md` ("Live tests
  on the Claude plan").
