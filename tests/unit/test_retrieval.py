"""BM25 passage retrieval with CJK bigrams."""

from __future__ import annotations

from raida.llm import retrieval

TRANSCRIPT = "\n\n".join(
    [
        "[00:00:00] 歡迎大家準時上線，今天天氣很涼爽。",
        "[00:00:43] 我每天早上起來做曼達拉呼吸，已經三個月了。",
        "[00:01:21] 真正的力量來自內在，安全感也來自內在。",
        "[00:02:00] 浴室的水龍頭蓋子卡住了，我用橡皮筋增加摩擦力就打開了。",
    ]
)


def test_terms_use_bigrams_for_cjk_and_words_for_latin() -> None:
    # Chinese is folded to Simplified so either script matches the other.
    assert retrieval.terms("內在力量") == ["内在", "在力", "力量"]
    assert retrieval.terms("内在力量") == retrieval.terms("內在力量")
    assert retrieval.terms("The inner Power, 2026") == ["the", "inner", "power", "2026"]
    # Anchors are not content.
    assert retrieval.terms("[00:12:22] 呼吸") == ["呼吸"]


def test_split_passages_carries_anchors() -> None:
    text = "[p. 1] First page.\n\nSecond paragraph.\n\n[p. 2] Next page."
    passages = retrieval.split_passages(text, 1, "doc", 3.7)
    assert [p.anchor for p in passages] == ["[p. 1]", "[p. 1]", "[p. 2]"]
    assert all(p.tokens > 0 for p in passages)


def test_bm25_finds_the_passage_about_the_question() -> None:
    passages = retrieval.split_passages(TRANSCRIPT, 1, "248", 3.7)
    chosen = retrieval.select_passages("水龍頭是怎麼打開的？", passages, 500)
    assert [p.anchor for p in chosen] == ["[00:02:00]"]


def test_a_simplified_question_finds_traditional_passages() -> None:
    passages = retrieval.split_passages(TRANSCRIPT, 1, "248", 3.7)
    chosen = retrieval.select_passages("水龙头是怎么打开的？", passages, 500)
    assert [p.anchor for p in chosen] == ["[00:02:00]"]


def test_select_respects_budget_and_source_order() -> None:
    a = retrieval.split_passages(TRANSCRIPT, 1, "a", 3.7)
    b = retrieval.split_passages(TRANSCRIPT, 2, "b", 3.7)
    chosen = retrieval.select_passages("內在的力量與安全感", a + b, 10_000)
    assert [(p.source_index, p.anchor) for p in chosen] == [(1, "[00:01:21]"), (2, "[00:01:21]")]
    one = retrieval.select_passages("內在的力量與安全感", a + b, a[2].tokens)
    assert len(one) == 1
    assert retrieval.select_passages("unrelated words", a, 10_000) == []
    assert retrieval.select_passages("內在", a, 0) == []


def test_excerpts_block_merges_neighbours() -> None:
    passages = retrieval.split_passages(TRANSCRIPT, 3, "t", 3.7)
    block = retrieval.excerpts_block([passages[1], passages[2], passages[3]])
    assert block.count("<excerpt") == 1 and '<excerpt source="3">' in block
    block = retrieval.excerpts_block([passages[0], passages[2]])
    assert block.count("<excerpt") == 2
