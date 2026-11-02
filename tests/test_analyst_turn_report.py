"""An analyst's report survives a model that writes it beside its last tool call."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.analysts.turn import WRAP_UP, take_turn

REPORT = "# FCX news report\n\n" + "Copper rose after the Q2 beat. " * 60
POINTER = "I wrote the full report in the text above. The news feeds returned nothing."


class Replies(BaseChatModel):
    """Answers with the next scripted message."""

    replies: list

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


PROMPT = ChatPromptTemplate.from_messages(
    [("system", "Analyst. Tools: {tool_names}."), MessagesPlaceholder(variable_name="messages")]
).partial(tool_names="get_news")


def _history(draft: str) -> list:
    call = {"name": "get_news", "args": {}, "id": "call_1", "type": "tool_call"}
    return [
        HumanMessage("FCX"),
        AIMessage(content=draft, tool_calls=[call]),
        ToolMessage(content="no articles", tool_call_id="call_1", name="get_news"),
    ]


def test_a_report_written_beside_the_last_tool_call_is_kept():
    model = Replies(replies=[AIMessage(content=POINTER)])
    _, report = take_turn(PROMPT, model, [], _history(REPORT))
    assert report.startswith(REPORT.strip())
    assert report.endswith(POINTER)


def test_a_complete_final_report_is_used_as_is():
    final = "# Final\n\n" + "A full rewrite with the last result. " * 40
    model = Replies(replies=[AIMessage(content=final)])
    _, report = take_turn(PROMPT, model, [], _history(REPORT))
    assert report == final


def test_a_short_note_beside_a_tool_call_is_not_a_draft():
    model = Replies(replies=[AIMessage(content="Short final report.")])
    _, report = take_turn(PROMPT, model, [], _history("Fetching the news first."))
    assert report == "Short final report."


def test_the_wrap_up_turn_recovers_the_draft_too():
    history = [*_history(REPORT), HumanMessage(WRAP_UP)]
    model = Replies(replies=[AIMessage(content=POINTER)])
    _, report = take_turn(PROMPT, model, [], history)
    assert report.startswith(REPORT.strip())


def test_a_reply_that_calls_tools_files_no_report():
    call = {"name": "get_news", "args": {}, "id": "call_2", "type": "tool_call"}
    model = Replies(replies=[AIMessage(content=REPORT, tool_calls=[call])])
    _, report = take_turn(PROMPT, model, [], [HumanMessage("FCX")])
    assert report == ""


def test_a_draft_in_content_blocks_is_read():
    blocks = [{"type": "text", "text": REPORT}, {"type": "tool_use", "id": "call_1", "name": "get_news", "input": {}}]
    model = Replies(replies=[AIMessage(content=POINTER)])
    _, report = take_turn(PROMPT, model, [], _history(blocks))
    assert report.startswith(REPORT.strip())
