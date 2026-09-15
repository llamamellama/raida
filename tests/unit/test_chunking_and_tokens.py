from raida.llm.chunking import split_markdown
from raida.llm.tokens import estimate_tokens


def test_estimate_tokens_has_margin() -> None:
    assert estimate_tokens("", 3.7) == 0
    assert estimate_tokens("a" * 370, 3.7) == 115


def test_split_respects_budget_and_keeps_paragraphs() -> None:
    paragraphs = [f"[00:{i:02d}:00] Paragraph {i} " + "word " * 60 for i in range(40)]
    text = "\n\n".join(paragraphs)
    chunks = split_markdown(text, max_tokens=400, chars_per_token=3.7)
    assert len(chunks) > 3
    for chunk in chunks:
        assert estimate_tokens(chunk, 3.7) <= 400 + 120  # overlap paragraph allowed
    joined = "\n\n".join(chunks)
    for i in range(40):
        assert f"Paragraph {i} " in joined


def test_split_handles_giant_paragraph() -> None:
    text = "sentence. " * 5000
    chunks = split_markdown(text, max_tokens=300, chars_per_token=3.7)
    assert len(chunks) > 10
    assert all(estimate_tokens(c, 3.7) <= 420 for c in chunks)


def test_heading_starts_new_chunk_when_half_full() -> None:
    text = "# A\n\n" + "alpha " * 200 + "\n\n# B\n\n" + "beta " * 200
    chunks = split_markdown(text, max_tokens=600, chars_per_token=3.7)
    assert chunks[0].startswith("# A")
    assert any(c.startswith("# B") or "\n# B" in c for c in chunks)
