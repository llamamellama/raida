"""The fast-path planner keeps every source's notes before it keeps old conversation turns."""

from __future__ import annotations

from pathlib import Path

from raida.db import Database
from raida.llm.fake import FakeLlmBackend
from raida.llm.gate import LlmGate
from raida.llm.synthesis import Synthesizer
from raida.models import Message, NoteSection, ProcessedSource, SourceNotes
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


def _turns(count: int, words: int) -> list[Message]:
    turns = []
    for i in range(count):
        for role in ("user", "assistant"):
            turns.append(
                Message(
                    id=f"{role}{i}",
                    session_id="s",
                    role=role,
                    status="done",
                    content=f"{role} turn {i} " + "text " * words,
                    created_at=f"2026-09-26T00:00:{i:02d}",
                    updated_at="x",
                )
            )
    return turns


def test_old_turns_give_way_before_notes(tmp_path: Path) -> None:
    synth = _synth(
        tmp_path,
        RAIDA_LLM__INTERACTIVE_BUDGET_TOKENS="12000",
        RAIDA_LLM__HISTORY_BUDGET_TOKENS="6000",
        RAIDA_LLM__PASSAGE_BUDGET_TOKENS="1000",
    )
    pairs = [_source(i) for i in range(4)]
    docs = [d for d, _ in pairs]
    notes = {d.source_id: n for d, n in pairs}
    history = _turns(8, 150)
    plan = synth.plan_fast(docs, notes, history, "question")
    assert plan is not None and set(plan.forms.values()) == {"notes"}
    kept = plan.prefix[3:]
    assert kept and kept[0]["role"] == "user"
    assert kept[-1]["content"].startswith("assistant turn 7")  # the newest turn stays
    assert len(kept) < len(synth._history_messages(history))  # some older turns went
    # Without history the same sources fit with room to spare.
    alone = synth.plan_fast(docs, notes, [], "question")
    assert alone is not None and alone.prefix[:3] == plan.prefix[:3]


def test_overviews_only_when_notes_alone_do_not_fit(tmp_path: Path) -> None:
    synth = _synth(tmp_path, RAIDA_LLM__INTERACTIVE_BUDGET_TOKENS="7000")
    pairs = [_source(i) for i in range(4)]
    docs = [d for d, _ in pairs]
    notes = {d.source_id: n for d, n in pairs}
    plan = synth.plan_fast(docs, notes, _turns(3, 100), "question")
    assert plan is not None and "overview" in plan.forms.values()
    assert len(plan.prefix) == 3  # the history went first
