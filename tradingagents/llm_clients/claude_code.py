"""Run Claude on the person's own Claude Pro or Max plan, through their Claude Code.

Anthropic keeps Claude plan credentials inside its own apps: Claude Code and
claude.ai. TradingAgents therefore never signs in to Anthropic, and never reads,
stores or forwards a plan credential. Each model call is one headless Claude Code
invocation (``claude -p``) that answers a single prompt with its own login, with
every tool, MCP server, plugin, hook, skill and settings file turned off and an
empty working directory, so nothing from the person's coding setup reaches the
prompt and nothing is left behind (no session is saved).

Signing in is Claude Code's own: ``claude auth login`` opens claude.ai in a
browser. A machine without a browser, such as a container, can relay that login
to a browser elsewhere (``start_browser_sign_in``: the web console's Sign in
button), or use the long-lived token ``claude setup-token`` prints, set as
``CLAUDE_CODE_OAUTH_TOKEN``.

An Anthropic API key in the environment would make Claude Code bill the API
instead of the plan, so it is never passed on; nor are the variables a parent
Claude Code session sets for its own children.

This is for a person's own research on their own machine. A product or service
offering Claude to other people must use an API key (the ``anthropic`` provider):
Anthropic does not allow plan access to be resold or shared.
"""

from __future__ import annotations

import contextlib
import glob
import importlib.util
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROVIDER = "claude"
CLI_ENV = "TRADINGAGENTS_CLAUDE_CLI"            # path to the claude executable, when not on PATH
PARALLEL_ENV = "TRADINGAGENTS_CLAUDE_MAX_PARALLEL"
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"            # from `claude setup-token`, for machines without a browser
MIN_VERSION = (2, 1, 0)                          # --tools, --json-schema, --effort and --no-session-persistence
STATUS_TTL = 60.0                                # seconds a sign-in check is trusted
DEFAULT_TIMEOUT = 600.0                          # the read timeout the other providers use

INSTALL_HINT = (
    "Claude Code is not installed, or not on PATH. Install it (https://claude.com/claude-code), "
    f"or point {CLI_ENV} at the claude executable, then run `tradingagents login claude`."
)
LOGIN_HINT = "Claude Code is not signed in to a Claude plan; run `tradingagents login claude`."

# The sign-ins that bill the Anthropic API rather than a plan.
_API_BILLED = {"api_key", "apikey", "api-key", "console", "anthropic_console"}


class ClaudeCodeError(RuntimeError):
    """A call through Claude Code failed; the message says what to do."""


class ClaudeCodeNotFound(ClaudeCodeError):
    """There is no usable Claude Code on this machine."""


class ClaudeSignInError(ClaudeCodeError):
    """Claude Code is not signed in to a Claude plan, or its sign-in was refused."""


class ClaudeUsageLimit(ClaudeCodeError):
    """The plan's usage limit is reached; trying again before it resets cannot help."""


class ClaudeCodeUnavailable(ClaudeCodeError):
    """Claude could not answer this time (busy, unreachable, too slow); a retry may."""


class ClaudeStructureError(ClaudeCodeError):
    """Claude's answer never matched the requested structure; asking again may."""


# --- finding Claude Code ---------------------------------------------------------------

def _version_key(path: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(part) for part in match.groups()) if match else (0,)


def _install_locations() -> list[str]:
    """Where Claude Code's installers put it, the copy the Agent SDK ships, then editors' copies.

    ``pip install "tradingagents[claude]"`` installs claude-agent-sdk, whose wheel
    bundles the Claude Code executable: the simplest install for a container.
    """
    home = Path.home()
    names = ["claude.exe", "claude"] if os.name == "nt" else ["claude"]
    fixed = [home / ".claude" / "local", home / ".local" / "bin", Path("/opt/homebrew/bin"),
             Path("/usr/local/bin")]
    found = [str(directory / name) for directory in fixed for name in names]
    try:
        sdk = importlib.util.find_spec("claude_agent_sdk")
    except (ImportError, ValueError):
        sdk = None
    for location in (sdk.submodule_search_locations or []) if sdk else []:
        found += [str(Path(location) / "_bundled" / name) for name in names]
    bundled = []
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        for name in names:
            bundled += glob.glob(str(home / editor / "extensions" / "anthropic.claude-code-*"
                                     / "resources" / "native-binary" / name))
    return found + sorted(bundled, key=_version_key, reverse=True)


def _executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_cli() -> str:
    """The claude executable: ``TRADINGAGENTS_CLAUDE_CLI``, then PATH, then the usual places."""
    explicit = os.environ.get(CLI_ENV, "").strip()
    if explicit:
        resolved = shutil.which(explicit) or os.path.expanduser(explicit)
        if _executable(resolved):
            return resolved
        raise ClaudeCodeNotFound(f"{CLI_ENV} is set to {explicit!r}, which is not an executable claude.")
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    for candidate in _install_locations():
        if _executable(candidate):
            return candidate
    raise ClaudeCodeNotFound(INSTALL_HINT)


@lru_cache(maxsize=16)
def cli_version(cli: str) -> tuple[int, ...] | None:
    """The version ``claude --version`` reports, or None when it reports none."""
    try:
        done = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30,
                              env=cli_environment(), cwd=_workdir(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", done.stdout or "")
    return tuple(int(part) for part in match.groups()) if match else None


def ensure_supported(cli: str) -> None:
    """Refuse a Claude Code too old for the flags every call relies on."""
    version = cli_version(cli)
    if version is not None and version < MIN_VERSION:
        wanted = ".".join(str(part) for part in MIN_VERSION)
        have = ".".join(str(part) for part in version)
        raise ClaudeCodeError(f"Claude Code {have} is too old; {wanted} or later is needed. Run `claude update`.")


# --- the environment and working directory ---------------------------------------------

def cli_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """This process's environment, minus what would take Claude Code off the plan.

    Every ``ANTHROPIC_*`` variable goes (an API key would be billed instead of the
    plan, a base URL would send the plan's login elsewhere), and so does every
    ``CLAUDE_CODE_*`` variable except the plan token: a parent Claude Code session
    sets those for its own children, and the provider switches among them would
    move the call to Bedrock, Vertex or Foundry.
    """
    env = {}
    for key, value in os.environ.items():
        if key.startswith("ANTHROPIC_") or key in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT"):
            continue
        if key.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK")) and key != TOKEN_ENV:
            continue
        env[key] = value
    # A headless call has no business updating the person's install.
    env["DISABLE_AUTOUPDATER"] = "1"
    env.update(extra or {})
    return env


_workdir_lock = threading.Lock()
_workdir_path: str | None = None


def _workdir() -> str:
    """An empty private directory to run in, so no project's CLAUDE.md or settings apply."""
    global _workdir_path
    with _workdir_lock:
        if _workdir_path is None or not os.path.isdir(_workdir_path):
            _workdir_path = tempfile.mkdtemp(prefix="tradingagents-claude-")
        return _workdir_path


# --- signing in ------------------------------------------------------------------------

_status_lock = threading.Lock()
_status_cache: dict[str, tuple[float, dict]] = {}


def auth_status(cli: str | None = None, fresh: bool = False) -> dict:
    """Claude Code's own account of its sign-in (``claude auth status --json``).

    Kept for a minute, since a run builds a client per model tier and the web
    console asks on every page load; ``fresh`` asks again.
    """
    cli = cli or find_cli()
    now = time.monotonic()
    with _status_lock:
        cached = _status_cache.get(cli)
        if cached and not fresh and now - cached[0] < STATUS_TTL:
            return dict(cached[1])
    try:
        done = subprocess.run([cli, "auth", "status", "--json"], capture_output=True, text=True,
                              timeout=60, env=cli_environment(), cwd=_workdir(), stdin=subprocess.DEVNULL)
        status = json.loads(done.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        status = {}
    if not isinstance(status, dict):
        status = {}
    with _status_lock:
        _status_cache[cli] = (now, status)
    return dict(status)


def forget_status() -> None:
    """Drop the remembered sign-in checks, after signing in or out."""
    with _status_lock:
        _status_cache.clear()


def on_plan(status: dict) -> bool:
    """Whether a status is a sign-in to a Claude plan, not to API billing."""
    if not status.get("loggedIn"):
        return False
    return str(status.get("authMethod") or "").strip().lower() not in _API_BILLED


def describe(status: dict) -> str:
    """Who Claude Code is signed in as, for a person to read."""
    who = status.get("email") or status.get("orgName") or "your Claude account"
    plan = status.get("subscriptionType")
    return f"{who} ({str(plan).title()} plan)" if plan else str(who)


def require_plan(cli: str | None = None) -> str:
    """The claude executable, once it is new enough and signed in to a plan.

    Called when a client is built, so a run stops before it starts instead of at
    its first model call. A plan token in the environment is taken on trust: it
    is checked by the first call, which names the problem if it is refused.
    """
    cli = cli or find_cli()
    ensure_supported(cli)
    if os.environ.get(TOKEN_ENV):
        return cli
    status = auth_status(cli)
    if not on_plan(status):
        if status.get("loggedIn"):
            raise ClaudeSignInError("Claude Code is signed in to Anthropic API billing, not a Claude plan; "
                                    "run `tradingagents login claude` and choose your Claude subscription.")
        raise ClaudeSignInError(LOGIN_HINT)
    return cli


def sign_in(setup_token: bool = False) -> dict:
    """Sign Claude Code in to a Claude plan with its own login, attached to this terminal.

    Returns the resulting status. ``setup_token`` runs ``claude setup-token``
    instead, which prints a long-lived token for a machine without a browser; the
    token goes straight to the terminal and never through this process.
    """
    cli = find_cli()
    ensure_supported(cli)
    if not setup_token:
        status = auth_status(cli, fresh=True)
        if on_plan(status):
            return status
    command = [cli, "setup-token"] if setup_token else [cli, "auth", "login", "--claudeai"]
    try:
        code = subprocess.run(command, env=cli_environment(), cwd=_workdir()).returncode
    except OSError as exc:
        raise ClaudeCodeNotFound(f"Claude Code could not be started ({exc}). {INSTALL_HINT}") from None
    forget_status()
    if code != 0:
        raise ClaudeSignInError("Claude Code's sign-in did not complete; run `tradingagents login claude` again.")
    if setup_token:
        return {}
    status = auth_status(cli, fresh=True)
    if not on_plan(status):
        raise ClaudeSignInError(LOGIN_HINT)
    return status


def sign_out() -> bool:
    """Sign Claude Code itself out on this machine; True when it says it did."""
    cli = find_cli()
    try:
        code = subprocess.run([cli, "auth", "logout"], env=cli_environment(), cwd=_workdir()).returncode
    except OSError as exc:
        raise ClaudeCodeNotFound(f"Claude Code could not be started ({exc}). {INSTALL_HINT}") from None
    forget_status()
    return code == 0


# --- signing in from a browser elsewhere ---------------------------------------------
#
# A container has no browser, but the person has one. `claude auth login` prints
# the claude.com sign-in link and then asks for the code claude.com shows once the
# person approves, so the web console starts it here, hands the link to the
# browser and passes the code back. Claude Code keeps the resulting sign-in in its
# own config directory (CLAUDE_CONFIG_DIR; a data volume in Docker) and refreshes
# it itself: TradingAgents relays a one-time code and never sees a credential.

LOGIN_WINDOW = 600.0                             # seconds a started sign-in waits for its code
_LOGIN_URL = re.compile(r"https://\S+/oauth/authorize\?\S+")
_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


@dataclass
class _Login:
    process: subprocess.Popen
    terminal: int
    url: str
    started: float
    output: str = ""


_login_lock = threading.Lock()
_login: _Login | None = None


def _plain(raw: bytes) -> str:
    return _ESCAPES.sub("", raw.decode("utf-8", "replace")).replace("\r", "")


def _read_terminal(terminal: int, until: float, done: Any) -> str:
    """What the login prints until ``done(text)`` holds, the time is up or it exits."""
    import select

    text = ""
    while time.monotonic() < until and not done(text):
        ready, _, _ = select.select([terminal], [], [], 0.25)
        if not ready:
            continue
        try:
            chunk = os.read(terminal, 65536)
        except OSError:                          # the login exited and closed the terminal
            break
        if not chunk:
            break
        text += _plain(chunk)
    return text


def _end_login(login: _Login) -> None:
    if login.process.poll() is None:
        _kill(login.process)
    with contextlib.suppress(OSError):
        os.close(login.terminal)


def cancel_browser_sign_in() -> None:
    """Stop a sign-in that was started and not finished."""
    global _login
    with _login_lock:
        if _login is not None:
            _end_login(_login)
            _login = None


def start_browser_sign_in() -> str:
    """Start Claude Code's login and return the claude.com link to approve it at.

    Replaces any sign-in already waiting for its code. Raises ``ClaudeCodeError``
    where a terminal cannot be emulated (Windows): sign in from a terminal there.
    """
    global _login
    try:
        import fcntl
        import pty
        import struct
        import termios
    except ImportError:
        raise ClaudeCodeError("Sign in from a terminal on this machine: `tradingagents login claude`.") from None
    cli = find_cli()
    ensure_supported(cli)
    with _login_lock:
        if _login is not None:
            _end_login(_login)
            _login = None
        terminal, child = pty.openpty()
        # Wide enough that the link is printed on one line.
        fcntl.ioctl(child, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 4000, 0, 0))
        try:
            process = subprocess.Popen(
                [cli, "auth", "login", "--claudeai"], stdin=child, stdout=child, stderr=child,
                env=cli_environment({"BROWSER": "true", "TERM": "dumb"}), cwd=_workdir(), start_new_session=True,
            )
        except OSError as exc:
            os.close(terminal)
            raise ClaudeCodeNotFound(f"Claude Code could not be started ({exc}). {INSTALL_HINT}") from None
        finally:
            os.close(child)
        output = _read_terminal(terminal, time.monotonic() + 30, lambda text: "Paste code" in text)
        match = _LOGIN_URL.search(output)
        if not match:
            if process.poll() is None:
                _kill(process)
            os.close(terminal)
            last = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
            raise ClaudeSignInError(f"Claude Code did not offer a sign-in link{f': {last}' if last else '.'}")
        _login = _Login(process, terminal, match.group(0), time.monotonic(), output)
        return _login.url


def finish_browser_sign_in(code: str) -> dict:
    """Give Claude Code the code claude.com showed; returns the resulting sign-in status."""
    global _login
    code = code.strip()
    if not code or any(ch.isspace() for ch in code):
        raise ClaudeSignInError("Paste the whole code claude.com showed, with nothing else.")
    with _login_lock:
        login = _login
        if login is None or login.process.poll() is not None or time.monotonic() - login.started > LOGIN_WINDOW:
            if login is not None:
                _end_login(login)
                _login = None
            raise ClaudeSignInError("This sign-in has expired; start it again.")
        os.write(login.terminal, code.encode() + b"\r")
        output = _read_terminal(login.terminal, time.monotonic() + 60, lambda _text: login.process.poll() is not None)
        try:
            exit_code = login.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            exit_code = None
        _end_login(login)
        _login = None
    forget_status()
    status = auth_status(fresh=True)
    if exit_code == 0 and on_plan(status):
        return status
    if exit_code == 0 and status.get("loggedIn"):
        raise ClaudeSignInError("That account signed Claude Code in to API billing, not a Claude Pro or Max plan.")
    reason = next((line.strip() for line in reversed(output.splitlines())
                   if line.strip() and "Paste code" not in line), "")
    raise ClaudeSignInError(f"Claude Code did not accept the code{f' ({reason})' if reason else ''}; start the sign-in again.")


# --- one call --------------------------------------------------------------------------

@dataclass
class Completion:
    """One answer from Claude Code."""

    text: str
    structured: Any = None
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None          # what the call would cost at API list prices; the plan bills none
    duration_ms: int | None = None
    stop_reason: str | None = None
    # With a schema, the text the model wrote beside its structured answer, which
    # Claude Code's result leaves out (it holds only the structured answer).
    said: str = ""


_slots_lock = threading.Lock()
_slots: threading.BoundedSemaphore | None = None


def _parallel_slots() -> threading.BoundedSemaphore:
    """How many calls may run at once: the analysts run together, and each call is a process."""
    global _slots
    with _slots_lock:
        if _slots is None:
            try:
                count = max(1, int(os.environ.get(PARALLEL_ENV, "4")))
            except ValueError:
                count = 4
            _slots = threading.BoundedSemaphore(count)
        return _slots


def _kill(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass


def _run(args: list[str], prompt: str, env: dict[str, str], timeout: float | None) -> tuple[int, str, str]:
    """Run one Claude Code process to the end, killing its whole group when it overruns."""
    group = {"start_new_session": True} if os.name == "posix" else {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    try:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", env=env, cwd=_workdir(),
                                   **group)
    except OSError as exc:
        raise ClaudeCodeNotFound(f"Claude Code could not be started ({exc}). {INSTALL_HINT}") from None
    try:
        out, err = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(process)
        process.communicate()
        raise ClaudeCodeUnavailable(f"Claude did not answer within {timeout:.0f} seconds.") from None
    except BaseException:
        _kill(process)
        raise
    return process.returncode, out or "", err or ""


def _result_and_text(out: str) -> tuple[dict | None, str]:
    """The result object, and the text of the assistant messages a stream printed before it.

    A stream that was cut off has no result event; its last event is not one either.
    """
    said, result = [], None
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            result = event
        elif event.get("type") == "assistant":
            content = (event.get("message") or {}).get("content") or []
            said += [block.get("text", "") for block in content
                     if isinstance(block, dict) and block.get("type") == "text"]
    if result is None:
        result = _last_json(out)
        if result is not None and result.get("type") not in (None, "result"):
            result = None
    return result, "\n\n".join(part.strip() for part in said if part.strip())


def _last_json(text: str) -> dict | None:
    """The result object Claude Code prints last; anything before it is ignored."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


_LIMIT = re.compile(r"usage limit|limit reached|hit your limit|limit will reset|resets? (at|in)\b|"
                    r"out of (extra )?usage|weekly limit|session limit", re.IGNORECASE)
_SIGN_IN = re.compile(r"invalid api key|/login|not logged in|log ?in again|oauth token|authenticat|"
                      r"unauthori[sz]ed|credential|token (has )?expired|revoked", re.IGNORECASE)
_MODEL = re.compile(r"selected model|model .*(not exist|not found|not available|access)|"
                    r"not_found_error", re.IGNORECASE)
_BUSY = re.compile(r"overloaded|temporarily|timed? ?out|timeout|econnreset|econnrefused|enotfound|"
                   r"network|socket hang up|fetch failed|connection (error|reset|refused)|"
                   r"api error: 5\d\d|internal server error|bad gateway|service unavailable", re.IGNORECASE)


def _failure(message: str, status: Any = None) -> ClaudeCodeError:
    """The error to raise for a failed call, by what Claude Code said."""
    detail = " ".join(message.split())[:600] or "no detail"
    code = status if isinstance(status, int) else None
    if code == 429 or _LIMIT.search(detail):
        return ClaudeUsageLimit(f"Your Claude plan's usage limit is reached ({detail}). "
                                "Continue once it resets; a checkpointed run resumes where it stopped.")
    if code in (401, 403) or _SIGN_IN.search(detail):
        return ClaudeSignInError(f"Claude Code's sign-in was refused ({detail}). {LOGIN_HINT}")
    if code == 404 or _MODEL.search(detail):
        return ClaudeCodeError(f"Claude Code cannot use this model ({detail}). Choose another model.")
    if (code is not None and code >= 500) or _BUSY.search(detail):
        return ClaudeCodeUnavailable(f"Claude could not answer ({detail}).")
    return ClaudeCodeError(f"Claude Code failed: {detail}")


def complete(
    prompt: str,
    *,
    system: str,
    model: str,
    effort: str | None = None,
    json_schema: dict | None = None,
    timeout: float | None = DEFAULT_TIMEOUT,
    max_output_tokens: int | None = None,
    cli: str | None = None,
) -> Completion:
    """Ask Claude one prompt through Claude Code, on the signed-in plan.

    With ``json_schema`` the answer is checked against it by Claude Code, which
    has the model try again while it does not match; it is returned as
    ``structured``. Raises a ``ClaudeCodeError`` subclass naming what went wrong.
    """
    cli = cli or find_cli()
    ensure_supported(cli)
    extra = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_output_tokens)} if max_output_tokens else None
    with _parallel_slots(), tempfile.TemporaryDirectory(prefix="tradingagents-claude-call-") as scratch:
        # The system prompt goes by file: an analyst's can outgrow a command-line argument.
        system_file = Path(scratch) / "system.md"
        system_file.write_text(system, encoding="utf-8")
        # A structured answer's result holds only the structure; the stream also
        # carries any text the model wrote beside it, which can be the answer itself.
        output = ["stream-json", "--verbose"] if json_schema is not None else ["json"]
        args = [
            cli, "-p", "--output-format", *output, "--model", model,
            "--system-prompt-file", str(system_file),
            "--tools", "", "--strict-mcp-config", "--setting-sources", "",
            "--disable-slash-commands", "--no-session-persistence",
            # A structured answer is one checked tool call, re-asked while it does not match.
            "--max-turns", "4" if json_schema is not None else "1",
        ]
        if effort:
            args += ["--effort", effort]
        if json_schema is not None:
            args += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
        code, out, err = _run(args, prompt, cli_environment(extra), timeout)

    data, said = _result_and_text(out)
    if data is None:
        # A stream holds the model's own writing, which must not be read as Claude Code's error.
        printed = out if json_schema is None else ""
        raise _failure((err or printed).strip() or f"Claude Code exited with code {code} and no answer")
    subtype = data.get("subtype")
    if data.get("is_error") or subtype not in (None, "success"):
        said = data.get("result") or "; ".join(str(e) for e in data.get("errors") or [])
        if subtype in ("error_max_structured_output_retries", "error_max_turns") and json_schema is not None:
            raise ClaudeStructureError("Claude did not answer in the requested structure.")
        if subtype == "error_during_execution" and not said:
            raise ClaudeCodeUnavailable("Claude Code stopped while answering.")
        raise _failure(str(said or subtype or err or "error"), data.get("api_error_status"))
    structured = data.get("structured_output") if json_schema is not None else None
    if json_schema is not None and not isinstance(structured, dict):
        raise ClaudeStructureError("Claude did not return an answer in the requested structure.")
    usage = data.get("usage") or {}
    return Completion(
        text=str(data.get("result") or ""),
        structured=structured,
        model=next(iter(data.get("modelUsage") or {}), None),
        usage={key: int(usage.get(key) or 0) for key in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")},
        cost_usd=data.get("total_cost_usd"),
        duration_ms=data.get("duration_ms"),
        stop_reason=data.get("stop_reason"),
        said=said,
    )
