"""One turn of a tool-using analyst, and its last turn once its tool rounds are spent."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

WRAP_UP = (
    "You have used every tool round this report allows. Write your final report "
    "now from the tool results above, and say which data you could not retrieve."
)

# Text a reply wrote beside its tool calls counts as a report draft from this length.
DRAFT_MIN_CHARS = 800


def take_turn(prompt, llm, tools, messages):
    """The analyst's reply and its report, which stays empty while it calls tools.

    Told to wrap up, it answers without tools: a model offered tools may call one
    whatever it is told. Its earlier calls and their results reach it as text,
    since a provider may refuse a history holding tool calls when none are bound.
    """
    if messages and isinstance(messages[-1], HumanMessage) and messages[-1].content == WRAP_UP:
        # The prompt lists the tools it offers; a model told of tools it cannot
        # call may still try, and some providers then answer with nothing.
        prompt = prompt.partial(tool_names="none; your tool rounds are spent")
        result = (prompt | llm).invoke(_as_text(messages))
        return result, _report(result.content, messages)
    result = (prompt | llm.bind_tools(tools)).invoke(messages)
    return result, "" if result.tool_calls else _report(result.content, messages)


def _report(final, messages):
    """The report: the final reply, or the draft it points back to.

    A model can write its whole report beside one last tool call, then answer
    only "the full report is above" once the result is in. That pointer alone
    would be filed as the report, so the draft leads and the final reply,
    which may add what the last call returned, follows it.
    """
    drafts = [_said(m) for m in messages if isinstance(m, AIMessage) and m.tool_calls]
    draft = max(drafts, key=len, default="").strip()
    text = _said_text(final)
    if len(draft) < DRAFT_MIN_CHARS or len(text) * 2 >= len(draft):
        return final
    return f"{draft}\n\n{text}".strip()


def _said(message) -> str:
    return _said_text(message.content)


def _said_text(content) -> str:
    """The text of a reply, whether one string or a list of content blocks."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in content or []
        if not isinstance(block, dict) or block.get("type", "text") == "text"
    )


def _as_text(messages):
    """The history with its tool calls and tool results written out as plain messages."""
    written = []
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            said = message.content if isinstance(message.content, str) else ""
            calls = "; ".join(f"{c['name']}({json.dumps(c['args'], default=str)})" for c in message.tool_calls)
            written.append(AIMessage(content=f"{said}\n[Called {calls}]".strip()))
        elif isinstance(message, ToolMessage):
            written.append(HumanMessage(content=f"[{message.name or 'Tool'} returned]\n{message.content}"))
        else:
            written.append(message)
    return written
