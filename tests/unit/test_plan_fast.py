"""The fast-path planner: the sources take their share, and the whole conversation comes on top."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from raida.db import Database
from raida.llm.fake import FakeLlmBackend
from raida.llm.gate import LlmGate
from raida.llm.synthesis import ConversationTooLongError, Synthesizer
from raida.models import Message, NoteSection, ProcessedSource, SkillUse, SourceNotes
from tests.conftest import make_config


def _synth(tmp_path: Path, **env: str) -> Synthesizer:
    config = make_config(tmp_path, **env)
    return Synthesizer(config, Database.in_memory(), FakeLlmBackend(config.llm), LlmGate())


def _source(i: int, tokens: int = 20_000) -> tuple[ProcessedSource, SourceNotes]:
    doc = ProcessedSource(
        source_id=f"s{i}",
        title=f"talk {i}",
        kind="text",
        text_markdown="word " * 100,
        token_estimate=tokens,
    )
    notes = SourceNotes(
        key=f"k{i}",
        sha256=f"h{i}",
        model="m",
        overview=f"Overview {i}.",
        sections=[NoteSection(start="[00:00:00]", text="note " * 1500)],
        token_estimate=0,
        created_at="now",
    )
    notes.token_estimate = 2_000
    return doc, notes


def _library(count: int = 4) -> tuple[list[ProcessedSource], dict[str, SourceNotes]]:
    pairs = [_source(i) for i in range(count)]
    return [d for d, _ in pairs], {d.source_id: n for d, n in pairs}


def _message(i: int, role: str, status: str = "done", words: int = 150) -> Message:
    return Message(
        id=f"{role}{i}",
        session_id="s",
        role=role,
        status=status,
        content=f"{role} turn {i} " + "text " * words,
        created_at=f"2026-09-26T00:00:{i:02d}",  # a question and its answer share it
        updated_at="x",
    )


def _turns(count: int, words: int = 150) -> list[Message]:
    return [_message(i, role, words=words) for i in range(count) for role in ("user", "assistant")]


def _sent(plan) -> list[str]:
    return [m["content"] for m in plan.prefix[3:]]


def test_every_question_and_answer_is_sent(tmp_path: Path) -> None:
    synth = _synth(
        tmp_path,
        RAIDA_LLM__INTERACTIVE_BUDGET_TOKENS="12000",
        RAIDA_LLM__SYNTHESIS_BUDGET_TOKENS="20000",
        RAIDA_LLM__PASSAGE_BUDGET_TOKENS="1000",
    )
    docs, notes = _library()
    history = _turns(12)  # far more than one turn's worth
    plan = synth.plan_fast(docs, notes, history, "question")
    assert plan is not None and set(plan.forms.values()) == {"notes"}
    assert _sent(plan) == [m.content for m in history]
    # The sources are chosen without the conversation: the same all session, so the cached
    # prefix only grows as answers are added.
    alone = synth.plan_fast(docs, notes, [], "question")
    assert alone is not None and alone.prefix == plan.prefix[:3]


def test_sources_give_way_before_the_conversation(tmp_path: Path) -> None:
    synth = _synth(
        tmp_path,
        RAIDA_LLM__INTERACTIVE_BUDGET_TOKENS="12000",
        RAIDA_LLM__SYNTHESIS_BUDGET_TOKENS="14000",
        RAIDA_LLM__PASSAGE_BUDGET_TOKENS="1000",
    )
    docs, notes = _library()
    history = _turns(12)
    plan = synth.plan_fast(docs, notes, history, "question")
    assert plan is not None
    assert list(plan.forms.values()).count("overview") >= 1  # a source shortened to its overview
    assert _sent(plan) == [m.content for m in history]  # the conversation whole


def test_overviews_when_notes_alone_do_not_fit_and_the_conversation_stays(tmp_path: Path) -> None:
    synth = _synth(tmp_path, RAIDA_LLM__INTERACTIVE_BUDGET_TOKENS="7000")
    docs, notes = _library()
    history = _turns(3, words=100)
    plan = synth.plan_fast(docs, notes, history, "question")
    assert plan is not None and "overview" in plan.forms.values()
    assert _sent(plan) == [m.content for m in history]


def test_a_conversation_too_long_for_the_largest_prompt_is_refused(tmp_path: Path) -> None:
    synth = _synth(
        tmp_path,
        RAIDA_LLM__INTERACTIVE_BUDGET_TOKENS="5000",
        RAIDA_LLM__SYNTHESIS_BUDGET_TOKENS="6000",
    )
    docs, notes = _library()
    with pytest.raises(ConversationTooLongError, match="Start a new session"):
        synth.plan_fast(docs, notes, _turns(12), "question")
    with pytest.raises(ConversationTooLongError):
        synth.plan_fast([], {}, _turns(40), "question")  # no sources at all


def test_questions_without_a_finished_answer_are_left_out(tmp_path: Path) -> None:
    synth = _synth(tmp_path)
    history = [
        _message(0, "user"),
        _message(0, "assistant"),
        _message(1, "user"),
        _message(1, "assistant", status="failed"),
        _message(2, "user"),
        _message(2, "assistant", status="cancelled"),
        _message(3, "user"),
        _message(3, "assistant", status="streaming"),
        _message(4, "user"),
        _message(4, "assistant"),
    ]
    expected = [history[0].content, history[1].content, history[8].content, history[9].content]
    shuffled = history[:]
    random.Random(7).shuffle(shuffled)  # the database does not order a question and its answer
    for messages in (history, shuffled):
        assert [m["content"] for m in synth._conversation(messages)] == expected


def test_an_earlier_skill_run_is_sent_as_its_command(tmp_path: Path) -> None:
    synth = _synth(tmp_path)
    use = SkillUse(
        name="summary",
        title="Summary",
        description="A structured summary.",
        arguments="for the team",
        body="Summarize the sources ...",
        revision="r1",
    )
    question = _message(0, "user").model_copy(
        update={"content": "@summary for the team", "skill": use}
    )
    conversation = synth._conversation([question, _message(0, "assistant")])
    assert conversation[0]["content"].startswith("@summary for the team")
    assert 'Ran the skill "Summary"' in conversation[0]["content"]
    assert "Summarize the sources" not in conversation[0]["content"]
    assert conversation[1]["content"].startswith("assistant turn 0")


def test_the_full_text_path_counts_the_whole_conversation(tmp_path: Path) -> None:
    synth = _synth(tmp_path, RAIDA_LLM__SYNTHESIS_BUDGET_TOKENS="8000")
    doc, _ = _source(0, tokens=4_000)
    assert synth.plan([doc], "question", []) == "single_shot"
    assert synth.plan([doc], "question", _turns(20)) == "map_reduce"
