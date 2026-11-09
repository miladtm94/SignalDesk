"""The claude provider runs on the person's Claude plan, through their own Claude Code.

A fake ``claude`` executable stands in for Claude Code: it records how it was
called and answers from a script, so these tests pin what TradingAgents sends
(one isolated headless call, no API key, the plan's sign-in) and how it reads
the answers back (text, tool calls, structured output, and every failure).
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
import textwrap
from types import SimpleNamespace

import pytest
import typer
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

import cli.prompts as prompts
from tradingagents import default_config
from tradingagents.agents.schemas import PortfolioRating, ResearchPlan
from tradingagents.agents.structured import invoke_structured
from tradingagents.llm_clients import claude_code, factory
from tradingagents.llm_clients.claude_client import ChatClaudeCode, render, tool_envelope

FAKE = textwrap.dedent('''\
    import json, os, sys, time
    state = os.environ["FAKE_CLAUDE_STATE"]
    args = sys.argv[1:]
    if args == ["--version"]:
        print(open(os.path.join(state, "version")).read() if os.path.exists(os.path.join(state, "version")) else "2.1.304 (Claude Code)")
        sys.exit(0)
    if args[:2] == ["auth", "status"]:
        print(open(os.path.join(state, "status")).read())
        sys.exit(0)
    if args[:2] == ["auth", "login"] and sys.stdin.isatty():
        # Like the real login on a terminal without a browser: the link, then the code.
        print("Opening browser to sign in...")
        print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=s1")
        sys.stdout.write("Paste code here if prompted > ")
        sys.stdout.flush()
        code = sys.stdin.readline().strip()
        with open(os.path.join(state, "commands"), "a") as f:
            f.write(json.dumps(args + [code]) + "\\n")
        accounts = {"good#s1": {"loggedIn": True, "authMethod": "claude.ai", "email": "c@example.com", "subscriptionType": "max"},
                    "api#s1": {"loggedIn": True, "authMethod": "console", "email": "c@example.com"}}
        if code not in accounts:
            print("Login failed: Request failed with status code 400")
            sys.exit(1)
        open(os.path.join(state, "status"), "w").write(json.dumps(accounts[code]))
        print("Login successful.")
        sys.exit(0)
    if args[:2] == ["auth", "login"] or args[:2] == ["auth", "logout"] or args[:1] == ["setup-token"]:
        with open(os.path.join(state, "commands"), "a") as f:
            f.write(json.dumps(args) + "\\n")
        if args[:2] == ["auth", "login"]:
            status = {"loggedIn": True, "authMethod": "claude.ai", "email": "b@example.com", "subscriptionType": "pro"}
        elif args[:2] == ["auth", "logout"]:
            status = {"loggedIn": False}
        else:
            status = None
        if status is not None:
            open(os.path.join(state, "status"), "w").write(json.dumps(status))
        sys.exit(0)
    prompt = sys.stdin.read()
    system = open(args[args.index("--system-prompt-file") + 1]).read()
    keep = ("ANTHROPIC_", "CLAUDE", "DISABLE_AUTOUPDATER")
    with open(os.path.join(state, "calls"), "a") as f:
        f.write(json.dumps({"args": args, "prompt": prompt, "system": system, "cwd": os.getcwd(),
                            "env": {k: v for k, v in os.environ.items() if k.startswith(keep)}}) + "\\n")
    replies = json.load(open(os.path.join(state, "replies")))
    reply = replies.pop(0)
    json.dump(replies, open(os.path.join(state, "replies"), "w"))
    time.sleep(reply.pop("_sleep", 0))
    code = reply.pop("_exit", 0)
    # A stream prints its events (the assistant's messages) before the result.
    for event in reply.pop("_events", []):
        print(json.dumps(event))
    if not reply.pop("_cut", False):
        print(json.dumps(reply))
    sys.exit(code)
''')

SIGNED_IN = {"loggedIn": True, "authMethod": "claude.ai", "email": "a@example.com",
             "subscriptionType": "max", "apiProvider": "firstParty"}
USAGE = {"input_tokens": 10, "cache_creation_input_tokens": 600, "cache_read_input_tokens": 90,
         "output_tokens": 40}


def answer(result="Fine.", **extra):
    return {"type": "result", "subtype": "success", "is_error": False, "result": result,
            "usage": USAGE, "modelUsage": {"claude-sonnet-5-5": {}}, "total_cost_usd": 0.0123,
            "duration_ms": 900, "stop_reason": "end_turn", **extra}


def structured(value):
    return answer(result=json.dumps(value), structured_output=value, stop_reason="tool_use")


def failure(result, status=None, **extra):
    return {"type": "result", "subtype": "success", "is_error": True, "result": result,
            "api_error_status": status, **extra}


class FakeClaude:
    def __init__(self, root):
        self.root = root
        self.state = root / "state"
        self.state.mkdir()
        self.path = root / "claude"
        self.path.write_text(f"#!{sys.executable}\n{FAKE}", encoding="utf-8")
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR)
        self.signed_in(SIGNED_IN)
        self.replies([])

    def signed_in(self, status):
        (self.state / "status").write_text(json.dumps(status), encoding="utf-8")
        claude_code.forget_status()

    def version(self, text):
        (self.state / "version").write_text(text, encoding="utf-8")
        claude_code.cli_version.cache_clear()

    def replies(self, items):
        (self.state / "replies").write_text(json.dumps(items), encoding="utf-8")

    def calls(self):
        path = self.state / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def commands(self):
        path = self.state / "commands"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def fake(tmp_path, monkeypatch):
    claude = FakeClaude(tmp_path)
    monkeypatch.setenv(claude_code.CLI_ENV, str(claude.path))
    monkeypatch.setenv("FAKE_CLAUDE_STATE", str(claude.state))
    monkeypatch.delenv(claude_code.TOKEN_ENV, raising=False)
    claude_code.forget_status()
    claude_code.cli_version.cache_clear()
    yield claude
    claude_code.forget_status()
    claude_code.cli_version.cache_clear()


def _llm(model="claude-sonnet-5-5", **config):
    merged = {**default_config.DEFAULT_CONFIG, "llm_provider": "claude", **config}
    return factory.create_llm_client("claude", model, **factory.build_llm_kwargs(merged)).get_llm()


def _flag(call, name):
    args = call["args"]
    return args[args.index(name) + 1] if name in args else None


# --- one isolated call, on the plan -------------------------------------------------

@pytest.mark.unit
def test_a_plain_call_is_one_isolated_headless_claude_code_call(fake):
    fake.replies([answer("A P/E ratio divides price by earnings.")])
    reply = _llm().invoke([SystemMessage("Be brief."), HumanMessage("What is a P/E ratio?")])

    assert reply.content == "A P/E ratio divides price by earnings."
    (call,) = fake.calls()
    assert call["prompt"] == "What is a P/E ratio?" and call["system"].startswith("Be brief.")
    # Claude Code attaches the machine's date; the run's own date must stay "now".
    assert "the date these instructions give is the present" in call["system"]
    assert _flag(call, "--model") == "claude-sonnet-5-5"
    assert _flag(call, "--output-format") == "json"
    for flag, value in (("--tools", ""), ("--setting-sources", ""), ("--max-turns", "1")):
        assert _flag(call, flag) == value
    for flag in ("-p", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in call["args"]
    assert "--json-schema" not in call["args"]
    # An empty directory of its own: no project's CLAUDE.md or settings apply.
    assert os.listdir(call["cwd"]) == []


@pytest.mark.unit
def test_no_api_key_or_parent_session_variable_reaches_claude_code(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-would-bill-the-api")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://relay.example")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv(claude_code.TOKEN_ENV, "plan-token")
    fake.replies([answer()])
    _llm().invoke("Hi")

    env = fake.calls()[0]["env"]
    assert not any(name.startswith("ANTHROPIC_") for name in env)
    assert {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_USE_BEDROCK"}.isdisjoint(env)
    assert env[claude_code.TOKEN_ENV] == "plan-token"
    assert env["DISABLE_AUTOUPDATER"] == "1"


@pytest.mark.unit
def test_usage_counts_cached_input_and_the_plan_is_what_bills_it(fake):
    fake.replies([answer()])
    reply = _llm().invoke("Hi")

    assert reply.usage_metadata["input_tokens"] == 700 and reply.usage_metadata["output_tokens"] == 40
    assert reply.usage_metadata["input_token_details"] == {"cache_read": 90, "cache_creation": 600}
    assert reply.response_metadata["billed_to"] == "claude_plan"
    assert reply.response_metadata["api_equivalent_cost_usd"] == pytest.approx(0.0123)
    assert reply.response_metadata["model_name"] == "claude-sonnet-5-5"


@pytest.mark.unit
@pytest.mark.parametrize("model, sent", [("claude-opus-5-5", "high"), ("opus", "high"),
                                         ("claude-haiku-5-5", None), ("haiku", None)])
def test_effort_goes_only_to_a_model_that_takes_it(fake, model, sent):
    fake.replies([answer()])
    _llm(model, anthropic_effort="high").invoke("Hi")
    assert _flag(fake.calls()[0], "--effort") == sent


@pytest.mark.unit
def test_sampling_settings_are_dropped_and_max_tokens_caps_the_output(fake, caplog):
    fake.replies([answer()])
    with caplog.at_level(logging.WARNING):
        llm = _llm(temperature=0.2, max_tokens=2048)
    llm.invoke("Hi")
    assert "does not accept temperature" in caplog.text
    assert fake.calls()[0]["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "2048"


# --- tools ----------------------------------------------------------------------------

@tool
def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Retrieve daily prices for a ticker between two dates."""
    return "Date,Close"


@tool
def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Retrieve news for a ticker between two dates."""
    return "headline"


@pytest.mark.unit
def test_a_tool_bound_call_comes_back_as_tool_calls(fake):
    fake.replies([structured({"content": "Fetching prices.", "tool_calls": [
        {"name": "get_stock_data", "arguments": {"symbol": "NVDA", "start_date": "2026-01-02", "end_date": "2026-01-09"}},
        {"name": "get_news", "arguments": {"ticker": "NVDA", "start_date": "2026-01-02", "end_date": "2026-01-09"}},
    ]})])
    reply = _llm().bind_tools([get_stock_data, get_news]).invoke([SystemMessage("Analyse."), HumanMessage("NVDA")])

    assert [call["name"] for call in reply.tool_calls] == ["get_stock_data", "get_news"]
    assert reply.tool_calls[0]["args"]["symbol"] == "NVDA"
    assert len({call["id"] for call in reply.tool_calls}) == 2
    call = fake.calls()[0]
    schema = json.loads(_flag(call, "--json-schema"))
    names = [item["properties"]["name"]["enum"][0] for item in schema["properties"]["tool_calls"]["items"]["anyOf"]]
    assert names == ["get_stock_data", "get_news"]
    assert "not callable directly" in call["system"] and "get_news" in call["system"]


@pytest.mark.unit
def test_an_answer_without_tool_calls_is_the_report(fake):
    fake.replies([structured({"content": "# Report\nPrices rose.", "tool_calls": []})])
    reply = _llm().bind_tools([get_stock_data]).invoke("NVDA")
    assert reply.tool_calls == [] and reply.content == "# Report\nPrices rose."


def _wrote(*texts):
    """Stream events: an assistant message per text, the way Claude Code prints them."""
    return [{"type": "system", "subtype": "init"}] + [
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "plan"},
                                                      {"type": "text", "text": text}]}} for text in texts]


REPORT = "# NVDA fundamentals\n" + "Revenue grew 62% on data-centre demand. " * 40


@pytest.mark.unit
def test_a_report_written_beside_the_structured_answer_is_the_answer(fake):
    # Claude wrote the report as text, then answered content "it is above": only
    # the stream carries that text, so structured calls read the stream.
    fake.replies([{**structured({"content": "I've written the full report in the text above.", "tool_calls": []}),
                   "_events": _wrote(REPORT)}])
    reply = _llm().bind_tools([get_stock_data]).invoke("NVDA")
    assert reply.tool_calls == [] and reply.content == REPORT.strip()
    call = fake.calls()[0]
    assert _flag(call, "--output-format") == "stream-json" and "--verbose" in call["args"]


@pytest.mark.unit
def test_a_short_aside_does_not_replace_the_structured_answer(fake):
    fake.replies([{**structured({"content": "# Report\nPrices rose 4% on volume twice the average.", "tool_calls": []}),
                   "_events": _wrote("Writing it up.")}])
    assert _llm().bind_tools([get_stock_data]).invoke("NVDA").content.startswith("# Report")


@pytest.mark.unit
def test_a_stream_cut_off_before_its_result_is_not_read_as_an_error_message(fake):
    # The model's text mentions a "usage limit"; it must not be taken for Claude Code's.
    fake.replies([{**structured({"content": "", "tool_calls": []}), "_cut": True, "_exit": 1,
                   "_events": _wrote("Margins hit the usage limit of the fab, so supply is short.")}] * 4)
    with pytest.raises(claude_code.ClaudeCodeError) as raised:
        _llm(max_retries=0).bind_tools([get_stock_data]).invoke("NVDA")
    assert not isinstance(raised.value, claude_code.ClaudeUsageLimit)
    assert "fab" not in str(raised.value)


@pytest.mark.unit
def test_tool_results_reach_the_next_call_as_a_transcript():
    system, prompt = render([
        SystemMessage("Analyse."),
        HumanMessage("NVDA"),
        AIMessage("", tool_calls=[{"name": "get_news", "args": {"ticker": "NVDA"}, "id": "call_1"}]),
        ToolMessage("CES keynote", tool_call_id="call_1", name="get_news"),
    ])
    assert system == "Analyse."
    assert prompt.index("<user>\nNVDA") < prompt.index('<tool_call name="get_news" call_id="call_1">')
    assert '<tool_result name="get_news" call_id="call_1">\nCES keynote\n</tool_result>' in prompt
    assert prompt.endswith("continuing from the tool results above.")


@pytest.mark.unit
def test_a_forced_tool_choice_requires_that_tool():
    tools = [{"name": "a", "description": "", "parameters": {"type": "object", "properties": {}}},
             {"name": "b", "description": "", "parameters": {"type": "object", "properties": {}}}]
    forced = tool_envelope(tools, "b")["properties"]["tool_calls"]
    assert forced["minItems"] == 1 and forced["items"]["properties"]["name"]["enum"] == ["b"]
    assert "minItems" not in tool_envelope(tools, "auto")["properties"]["tool_calls"]


# --- structured output ----------------------------------------------------------------

PLAN = {"recommendation": "Overweight", "rationale": "Bull case wins.", "strategic_actions": "Add slowly."}


@pytest.mark.unit
def test_structured_output_is_asked_for_by_schema_and_validated(fake):
    fake.replies([structured(PLAN)])
    plan = _llm().with_structured_output(ResearchPlan).invoke("Weigh the debate.")

    assert isinstance(plan, ResearchPlan) and plan.recommendation is PortfolioRating.OVERWEIGHT
    call = fake.calls()[0]
    schema = json.loads(_flag(call, "--json-schema"))
    assert set(schema["properties"]) == {"recommendation", "rationale", "strategic_actions"}
    assert "Overweight" in json.dumps(schema)
    assert _flag(call, "--max-turns") == "4"


@pytest.mark.unit
def test_a_structured_answer_that_does_not_validate_falls_back_to_free_text(fake):
    fake.replies([structured({"recommendation": "Strong Buy", "rationale": "", "strategic_actions": ""})])
    llm = _llm().with_structured_output(ResearchPlan)
    assert invoke_structured(llm, "Weigh the debate.", "Research Manager") is None


@pytest.mark.unit
def test_include_raw_returns_the_message_and_the_parse(fake):
    fake.replies([structured(PLAN)])
    out = _llm().with_structured_output(ResearchPlan, include_raw=True).invoke("Weigh the debate.")
    assert out["parsing_error"] is None and out["parsed"].rationale == "Bull case wins."
    assert isinstance(out["raw"], AIMessage)


# --- failures ---------------------------------------------------------------------------

@pytest.mark.unit
def test_a_reached_plan_limit_stops_without_retrying(fake):
    fake.replies([failure("You've hit your limit · resets 5pm", 429), answer()])
    with pytest.raises(claude_code.ClaudeUsageLimit, match="usage limit"):
        _llm(llm_max_retries=3).invoke("Hi")
    assert len(fake.calls()) == 1


@pytest.mark.unit
def test_a_busy_claude_is_tried_again(fake, monkeypatch):
    monkeypatch.setattr("tradingagents.llm_clients.claude_client.time.sleep", lambda seconds: None)
    fake.replies([failure("API Error: 529 Overloaded", 529), answer("Back.")])
    assert _llm(llm_max_retries=1).invoke("Hi").content == "Back."
    assert len(fake.calls()) == 2


@pytest.mark.unit
def test_an_unknown_model_names_the_problem(fake):
    fake.replies([failure("There's an issue with the selected model (claude-nope). It may not exist "
                          "or you may not have access to it.", 404)])
    with pytest.raises(claude_code.ClaudeCodeError, match="Choose another model"):
        _llm("claude-nope").invoke("Hi")


@pytest.mark.unit
def test_a_refused_sign_in_says_how_to_sign_in(fake):
    fake.replies([failure("OAuth token has expired. Please run /login", 401)])
    with pytest.raises(claude_code.ClaudeSignInError, match="tradingagents login claude"):
        _llm().invoke("Hi")


@pytest.mark.unit
def test_a_call_that_overruns_its_timeout_is_ended(fake, monkeypatch):
    monkeypatch.setattr("tradingagents.llm_clients.claude_client.time.sleep", lambda seconds: None)
    fake.replies([{**answer(), "_sleep": 30}])
    llm = _llm()
    llm = llm.model_copy(update={"timeout": 1, "max_retries": 0})
    with pytest.raises(claude_code.ClaudeCodeUnavailable, match="within 1 seconds"):
        llm.invoke("Hi")


# --- signing in -------------------------------------------------------------------------

@pytest.mark.unit
def test_a_run_without_a_plan_sign_in_stops_before_any_call(fake):
    fake.signed_in({"loggedIn": False})
    with pytest.raises(claude_code.ClaudeSignInError, match="tradingagents login claude"):
        _llm()
    assert fake.calls() == []


@pytest.mark.unit
def test_a_sign_in_that_bills_the_api_is_not_a_plan(fake):
    fake.signed_in({"loggedIn": True, "authMethod": "api_key"})
    with pytest.raises(claude_code.ClaudeSignInError, match="API billing"):
        _llm()


@pytest.mark.unit
def test_a_plan_token_in_the_environment_needs_no_local_sign_in(fake, monkeypatch):
    fake.signed_in({"loggedIn": False})
    monkeypatch.setenv(claude_code.TOKEN_ENV, "plan-token")
    assert isinstance(_llm(), ChatClaudeCode)


@pytest.mark.unit
def test_an_old_claude_code_is_refused(fake):
    fake.version("1.0.80 (Claude Code)")
    with pytest.raises(claude_code.ClaudeCodeError, match="claude update"):
        _llm()


@pytest.mark.unit
def test_a_named_executable_that_is_not_there_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv(claude_code.CLI_ENV, str(tmp_path / "missing" / "claude"))
    with pytest.raises(claude_code.ClaudeCodeNotFound, match=claude_code.CLI_ENV):
        claude_code.find_cli()


needs_pty = pytest.mark.skipif(sys.platform == "win32", reason="the browser sign-in drives a POSIX terminal")


@pytest.fixture
def browser_login(fake):
    fake.signed_in({"loggedIn": False})
    yield fake
    claude_code.cancel_browser_sign_in()


@pytest.mark.unit
@needs_pty
def test_a_browser_sign_in_relays_the_code_to_claude_code_s_own_login(browser_login):
    url = claude_code.start_browser_sign_in()
    assert url == "https://claude.com/cai/oauth/authorize?code=true&state=s1"

    status = claude_code.finish_browser_sign_in("  good#s1\n")

    assert claude_code.on_plan(status) and status["email"] == "c@example.com"
    assert browser_login.commands() == [["auth", "login", "--claudeai", "good#s1"]]
    assert claude_code._login is None


@pytest.mark.unit
@needs_pty
def test_a_rejected_code_says_why_and_needs_a_new_sign_in(browser_login):
    claude_code.start_browser_sign_in()

    with pytest.raises(claude_code.ClaudeSignInError, match="status code 400"):
        claude_code.finish_browser_sign_in("wrong#s1")
    with pytest.raises(claude_code.ClaudeSignInError, match="expired"):
        claude_code.finish_browser_sign_in("good#s1")
    assert not claude_code.on_plan(claude_code.auth_status(fresh=True))


@pytest.mark.unit
@needs_pty
def test_an_api_billing_account_is_not_taken_for_a_plan(browser_login):
    claude_code.start_browser_sign_in()

    with pytest.raises(claude_code.ClaudeSignInError, match="API billing"):
        claude_code.finish_browser_sign_in("api#s1")


@pytest.mark.unit
@needs_pty
def test_a_code_is_checked_before_it_is_sent(browser_login):
    with pytest.raises(claude_code.ClaudeSignInError, match="expired"):
        claude_code.finish_browser_sign_in("good#s1")       # nothing started
    claude_code.start_browser_sign_in()
    with pytest.raises(claude_code.ClaudeSignInError, match="whole code"):
        claude_code.finish_browser_sign_in("good #s1")
    assert claude_code.finish_browser_sign_in("good#s1")["loggedIn"]


@pytest.mark.unit
@needs_pty
def test_starting_again_replaces_the_waiting_sign_in(browser_login):
    claude_code.start_browser_sign_in()
    first = claude_code._login.process
    claude_code.start_browser_sign_in()

    assert first.poll() is not None
    assert claude_code.finish_browser_sign_in("good#s1")["loggedIn"]
    assert browser_login.commands() == [["auth", "login", "--claudeai", "good#s1"]]


@pytest.mark.unit
@needs_pty
def test_a_sign_in_left_waiting_too_long_expires(browser_login, monkeypatch):
    claude_code.start_browser_sign_in()
    monkeypatch.setattr(claude_code, "LOGIN_WINDOW", 0.0)

    with pytest.raises(claude_code.ClaudeSignInError, match="expired"):
        claude_code.finish_browser_sign_in("good#s1")
    assert claude_code._login is None


@pytest.mark.unit
def test_signing_in_is_claude_code_s_own_login(fake):
    fake.signed_in({"loggedIn": False})
    status = claude_code.sign_in()

    assert fake.commands() == [["auth", "login", "--claudeai"]]
    assert claude_code.describe(status) == "b@example.com (Pro plan)"


@pytest.mark.unit
def test_an_already_signed_in_claude_code_is_not_signed_in_again(fake):
    assert claude_code.describe(claude_code.sign_in()) == "a@example.com (Max plan)"
    assert fake.commands() == []


@pytest.mark.unit
def test_a_plan_token_is_printed_by_claude_code_never_read_here(fake):
    assert claude_code.sign_in(setup_token=True) == {}
    assert fake.commands() == [["setup-token"]]


@pytest.mark.unit
def test_an_effort_setting_reaches_a_claude_tier():
    kwargs = factory.build_llm_kwargs({"llm_provider": "claude", "anthropic_effort": "medium"})
    assert kwargs["effort"] == "medium"


# --- the CLI ----------------------------------------------------------------------------

@pytest.mark.unit
def test_a_signed_in_claude_plan_is_named_before_the_run(fake, capsys):
    prompts.ensure_signed_in("claude")
    out = " ".join(capsys.readouterr().out.split())
    assert "Using your Claude plan through Claude Code" in out and "a@example.com" in out


@pytest.mark.unit
def test_an_unattended_run_without_a_claude_sign_in_says_how(fake, monkeypatch, capsys):
    fake.signed_in({"loggedIn": False})
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(typer.Exit):
        prompts.ensure_signed_in("claude")
    assert "tradingagents login claude" in " ".join(capsys.readouterr().out.split())


@pytest.mark.unit
def test_an_interactive_run_signs_in_through_the_login_command(fake, monkeypatch, capsys):
    fake.signed_in({"loggedIn": False})
    ran, original = [], prompts.subprocess.run

    def run(argv, **kwargs):
        if argv[1:4] != ["-P", "-m", "cli.main"]:   # Claude Code itself, through the fake
            return original(argv, **kwargs)
        ran.append(argv)
        fake.signed_in(SIGNED_IN)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(prompts.subprocess, "run", run)
    prompts.ensure_signed_in("claude")
    assert ran[0][1:] == ["-P", "-m", "cli.main", "login", "claude"]
    assert "a@example.com" in capsys.readouterr().out


@pytest.mark.unit
def test_login_and_logout_take_the_claude_plan(fake, monkeypatch):
    from typer.testing import CliRunner

    from cli.main import app

    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    result = CliRunner().invoke(app, ["login", "claude"])
    assert result.exit_code == 0 and "signed in to your Claude plan" in result.output

    result = CliRunner().invoke(app, ["logout", "claude"])
    assert result.exit_code == 0 and "signs Claude Code itself out" in " ".join(result.output.split())
    assert fake.commands() == [["auth", "logout"]]


@pytest.mark.unit
def test_an_answer_that_never_fits_its_structure_is_asked_for_once_more(fake):
    fake.replies([
        {"type": "result", "subtype": "error_max_structured_output_retries", "is_error": True},
        structured({"content": "# Report", "tool_calls": []}),
    ])
    reply = _llm().bind_tools([get_stock_data]).invoke("NVDA")
    assert reply.content == "# Report" and len(fake.calls()) == 2


@pytest.mark.unit
def test_a_structure_failure_twice_is_reported(fake):
    miss = {"type": "result", "subtype": "error_max_turns", "is_error": True}
    fake.replies([miss, miss, answer()])
    with pytest.raises(claude_code.ClaudeStructureError):
        _llm().bind_tools([get_stock_data]).invoke("NVDA")
    assert len(fake.calls()) == 2


@pytest.mark.unit
def test_a_claude_code_replaced_during_a_run_is_found_again(fake, monkeypatch, tmp_path):
    fake.replies([answer("Still here.")])
    llm = _llm().model_copy(update={"cli_path": str(tmp_path / "gone" / "claude")})
    assert llm.invoke("Hi").content == "Still here."
    assert len(fake.calls()) == 1
