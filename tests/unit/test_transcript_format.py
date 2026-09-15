from raida.transcribe.base import Segment
from raida.transcribe.format import coalesce_segments, format_timestamp, transcript_markdown


def test_timestamp_format() -> None:
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(3725.9) == "01:02:05"


def test_coalesce_into_paragraphs() -> None:
    segments = [Segment(start=i * 10, end=i * 10 + 9, text=f"s{i}") for i in range(10)]
    paragraphs = coalesce_segments(segments, paragraph_seconds=30)
    assert len(paragraphs) == 4
    assert paragraphs[0].text == "s0 s1 s2"
    md = transcript_markdown(segments, 30)
    assert md.startswith("[00:00:00] s0 s1 s2")
    assert "[00:00:30] s3" in md


def test_empty_segments_skipped() -> None:
    assert transcript_markdown([Segment(start=0, end=1, text="   ")], 30) == ""
