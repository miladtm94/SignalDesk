"""The ``claude`` provider: Claude on the person's Claude plan, each call made by their Claude Code.

Claude Code answers one prompt per call (see ``claude_code``), so this chat model
renders the conversation into that prompt and reads the answer back:

- A plain call is answered as text.
- A call with tools bound is answered in a checked structure holding the tool
  calls to make and any text. The tools run in the graph, as they do for every
  provider; their results come back in the next call's conversation.
- ``with_structured_output`` asks for the schema itself, and the result is
  validated into it.

Plan usage takes no sampling settings, so ``temperature`` and ``top_p`` are
dropped with a warning; ``max_tokens`` caps the output through Claude Code.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from . import claude_code
from .anthropic_client import _supports_effort
from .base_client import BaseLLMClient
from .validators import validate_model

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM = (
    "You are a careful analyst working inside an automated research pipeline. "
    "Follow the instructions in each message exactly."
)

# Claude Code attaches the machine's environment, today's date included, to every
# call, even under a system prompt of our own. A run dated in the past would then
# read its own data as weeks stale, and a model may even take the machine's date
# as "now"; told what that context is, it keeps to the run's date.
SESSION_NOTE = (
    "\n\n# About this session\n"
    "This session is one step of an automated research pipeline. The environment details attached "
    "to it (working directory, platform, today's date, account) describe the machine running the "
    "pipeline, not the task, so ignore them. An analysis may be dated in the past: the date these "
    "instructions give is the present for the task. Never call its data stale or out of date, and "
    "never mention another current date, because of what the machine reports."
)

# Claude Code's model aliases, and whether the model each names takes ``effort``.
_ALIAS_EFFORT = {"fable": True, "opus": True, "sonnet": True, "haiku": False}

# Sampling settings a Claude plan run through Claude Code does not take.
_PLAN_REFUSED = ("temperature", "top_p")


def supports_effort(model: str) -> bool:
    """Whether Claude takes ``effort`` for this model ID or Claude Code alias."""
    name = model.strip().lower()
    return _ALIAS_EFFORT.get(name, False) or _supports_effort(name)


def _text(content: Any) -> str:
    """A message's text, its text blocks joined when it has several."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _turn(message: BaseMessage) -> str:
    """One message of a conversation, written out for the prompt."""
    if isinstance(message, ToolMessage):
        name = message.name or "tool"
        return f'<tool_result name="{name}" call_id="{message.tool_call_id}">\n{_text(message.content)}\n</tool_result>'
    if isinstance(message, AIMessage):
        parts = [_text(message.content)] if _text(message.content) else []
        for call in message.tool_calls:
            arguments = json.dumps(call.get("args") or {}, default=str, ensure_ascii=False)
            parts.append(f'<tool_call name="{call["name"]}" call_id="{call.get("id")}">{arguments}</tool_call>')
        return "<assistant>\n" + "\n".join(parts) + "\n</assistant>"
    role = message.role if isinstance(message, ChatMessage) else "user"
    return f"<{role}>\n{_text(message.content)}\n</{role}>"


def render(messages: Sequence[BaseMessage]) -> tuple[str, str]:
    """The system prompt and the one prompt Claude Code is asked.

    A single user message is sent as it is. A longer conversation, an analyst's
    tool rounds for example, is written out oldest turn first, and Claude is
    asked for the assistant's next turn.
    """
    system = "\n\n".join(_text(m.content) for m in messages if isinstance(m, SystemMessage) and _text(m.content))
    turns = [m for m in messages if not isinstance(m, SystemMessage)]
    if len(turns) == 1 and isinstance(turns[0], HumanMessage):
        return system or DEFAULT_SYSTEM, _text(turns[0].content)
    transcript = "\n\n".join(_turn(m) for m in turns)
    ask = ("Write the assistant's next turn, continuing from the tool results above."
           if turns and isinstance(turns[-1], ToolMessage) else "Write the assistant's next turn.")
    prompt = f"The conversation so far, oldest turn first:\n\n{transcript}\n\n{ask}"
    return system or DEFAULT_SYSTEM, prompt


def _tool_spec(tool: Any) -> dict:
    function = convert_to_openai_tool(tool)["function"]
    parameters = function.get("parameters") or {"type": "object", "properties": {}}
    return {"name": function["name"], "description": function.get("description", ""), "parameters": parameters}


def _forced_tool(tool_choice: Any, names: list[str]) -> str | bool:
    """The tool a ``tool_choice`` insists on, True for "some tool", False for none."""
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name") or tool_choice.get("name")
        return name if name in names else True
    if tool_choice in ("any", "required") or tool_choice is True:
        return True
    if isinstance(tool_choice, str) and tool_choice in names:
        return tool_choice
    return False


def tool_envelope(tools: list[dict], tool_choice: Any = None) -> dict:
    """The structure a tool-bound answer takes: the calls to make, and any text."""
    names = [tool["name"] for tool in tools]
    forced = _forced_tool(tool_choice, names)
    offered = [tool for tool in tools if not isinstance(forced, str) or tool["name"] == forced]
    calls = [{
        "type": "object",
        "description": tool["description"],
        "properties": {"name": {"type": "string", "enum": [tool["name"]]}, "arguments": tool["parameters"]},
        "required": ["name", "arguments"],
        "additionalProperties": False,
    } for tool in offered]
    array: dict[str, Any] = {"type": "array", "items": calls[0] if len(calls) == 1 else {"anyOf": calls}}
    if forced:
        array["minItems"] = 1
    return {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": (
                "With tool calls, at most a sentence on why. Without any, the complete answer, "
                "written exactly as it should be read.")},
            "tool_calls": array,
        },
        "required": ["content", "tool_calls"],
        "additionalProperties": False,
    }


def tool_instructions(tools: list[dict], tool_choice: Any = None) -> str:
    """What the system prompt says about the tools and how to call them."""
    described = "\n\n".join(
        f"### {tool['name']}\n{tool['description']}\nArguments (JSON schema): "
        f"{json.dumps(tool['parameters'], ensure_ascii=False)}"
        for tool in tools
    )
    forced = _forced_tool(tool_choice, [tool["name"] for tool in tools])
    must = (f"\nThis turn you must request {forced}." if isinstance(forced, str)
            else "\nThis turn you must request at least one tool." if forced else "")
    return (
        "\n\n# Tools\n"
        "The tools named in these instructions are not callable directly in this session. You use "
        "one by requesting it in the tool_calls field of your structured output; the pipeline runs "
        "every request and returns each result to you as a tool_result in the next turn.\n\n"
        f"{described}\n\n"
        "# How to answer\n"
        "Answer only with the structured output, in one reply. To use tools, list the requests in "
        "tool_calls, each with the tool's name and its arguments; independent requests can go "
        "together, and content stays to a sentence at most. Never write a tool's result yourself: "
        "request it and stop. When you have what you need, leave tool_calls empty and write your "
        f"complete answer in content.{must}"
    )


def structured_instructions() -> str:
    return ("\n\n# How to answer\nAnswer with the structured output only, filling every field "
            "as its description asks.")


def _json_schema(schema: Any) -> dict:
    """The JSON schema to ask for: a Pydantic model's, or one given as a dict."""
    if isinstance(schema, dict) and schema.get("type") == "object" and "properties" in schema:
        return schema
    return convert_to_openai_tool(schema)["function"]["parameters"]


def _parsed(schema: Any, message: AIMessage) -> Any:
    data = message.additional_kwargs.get("parsed")
    if data is None:
        data = json.loads(_text(message.content))
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_validate(data)
    return data


class ChatClaudeCode(BaseChatModel):
    """Claude on the person's Claude plan, answered by their Claude Code one call at a time."""

    model: str
    effort: str | None = None
    timeout: float | None = claude_code.DEFAULT_TIMEOUT
    max_retries: int = 2
    max_output_tokens: int | None = None
    cli_path: str | None = None

    @property
    def _llm_type(self) -> str:
        return "claude-code"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model, "effort": self.effort}

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any):
        return self.bind(tools=[_tool_spec(tool) for tool in tools], tool_choice=tool_choice, **kwargs)

    def with_structured_output(self, schema: Any, *, include_raw: bool = False, method: Any = None,
                               **kwargs: Any):
        bound = self.bind(response_schema=_json_schema(schema))
        parse = RunnableLambda(lambda message: _parsed(schema, message))
        if not include_raw:
            return bound | parse

        def parse_raw(raw: AIMessage) -> dict:
            try:
                return {"raw": raw, "parsed": _parsed(schema, raw), "parsing_error": None}
            except Exception as exc:
                return {"raw": raw, "parsed": None, "parsing_error": exc}

        return bound | RunnableLambda(parse_raw)

    def _ask(self, prompt: str, system: str, schema: dict | None) -> claude_code.Completion:
        """One call, tried again while Claude is only busy or unreachable.

        An answer that never matched its structure is asked for once more: for an
        analyst's tool round there is no free-text fallback to catch it.
        """
        attempt, reshaped, cli = 0, False, self.cli_path
        while True:
            try:
                return claude_code.complete(
                    prompt, system=system, model=self.model, json_schema=schema,
                    effort=self.effort if self.effort and supports_effort(self.model) else None,
                    timeout=self.timeout, max_output_tokens=self.max_output_tokens, cli=cli,
                )
            except claude_code.ClaudeCodeNotFound:
                # An editor's bundled copy can be replaced by its update during a run.
                found = claude_code.find_cli()
                if found == cli:
                    raise
                cli = found
            except claude_code.ClaudeStructureError:
                if reshaped:
                    raise
                reshaped = True
                logger.warning("Claude's answer did not match the requested structure; asking once more")
            except claude_code.ClaudeCodeUnavailable as exc:
                if attempt >= self.max_retries:
                    raise
                attempt += 1
                wait = min(30.0, 2.0 * 2 ** (attempt - 1))
                logger.warning("Claude was unavailable (%s); trying again in %.0fs", exc, wait)
                time.sleep(wait)

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                  *, tools: list[dict] | None = None, tool_choice: Any = None,
                  response_schema: dict | None = None, **kwargs: Any) -> ChatResult:
        system, prompt = render(messages)
        system += SESSION_NOTE
        use_tools = bool(tools) and tool_choice != "none"
        if use_tools:
            completion = self._ask(prompt, system + tool_instructions(tools, tool_choice),
                                   tool_envelope(tools, tool_choice))
            answer = completion.structured
            calls = [{"name": call["name"], "args": call.get("arguments") or {},
                      "id": f"call_{uuid.uuid4().hex[:24]}", "type": "tool_call"}
                     for call in answer.get("tool_calls") or []]
            message = AIMessage(content=str(answer.get("content") or ""), tool_calls=calls)
        elif response_schema is not None:
            completion = self._ask(prompt, system + structured_instructions(), response_schema)
            message = AIMessage(content=json.dumps(completion.structured, ensure_ascii=False),
                                additional_kwargs={"parsed": completion.structured})
        else:
            completion = self._ask(prompt, system, None)
            message = AIMessage(content=completion.text)

        usage = completion.usage
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0)
        input_tokens = usage.get("input_tokens", 0) + cache_read + cache_creation
        output_tokens = usage.get("output_tokens", 0)
        message.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cache_read, "cache_creation": cache_creation},
        }
        message.response_metadata = {
            "model_name": completion.model or self.model,
            "model_provider": claude_code.PROVIDER,
            "billed_to": "claude_plan",
            # What the same tokens would cost at API list prices; the plan bills none of it.
            "api_equivalent_cost_usd": completion.cost_usd,
            "duration_ms": completion.duration_ms,
            "stop_reason": completion.stop_reason,
        }
        return ChatResult(generations=[ChatGeneration(message=message)])


class ClaudeCodeClient(BaseLLMClient):
    """Client for the ``claude`` provider: the person's Claude plan through Claude Code."""

    provider = claude_code.PROVIDER

    def get_llm(self) -> Any:
        self.warn_if_unknown_model()
        if self.base_url:
            logger.warning("The claude provider runs through Claude Code; backend_url %s is not used.",
                           self.base_url)
        for name in _PLAN_REFUSED:
            if self.kwargs.get(name) is not None:
                logger.warning("A Claude plan run through Claude Code does not accept %s; it is not sent.", name)
        if "api_key" in self.kwargs:
            raise ValueError("The claude provider uses Claude Code's sign-in, not an api_key.")
        # Fails here, before the run, when Claude Code is missing or signed out.
        cli = claude_code.require_plan()
        llm_kwargs: dict[str, Any] = {"model": self.model, "cli_path": cli}
        for key, target in (("effort", "effort"), ("timeout", "timeout"), ("max_retries", "max_retries"),
                            ("max_tokens", "max_output_tokens"), ("callbacks", "callbacks")):
            if self.kwargs.get(key) is not None:
                llm_kwargs[target] = self.kwargs[key]
        return ChatClaudeCode(**llm_kwargs)

    def validate_model(self) -> bool:
        return validate_model(claude_code.PROVIDER, self.model)
