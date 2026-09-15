"""Generate small test fixtures: two PDFs, a WAV and an MP4. Run once; outputs are committed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent


def make_text_pdf(path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    for number, title in enumerate(["Introduction", "Findings"], start=1):
        page = doc.new_page()
        body = f"{title}\n\n" + " ".join(
            f"This is sentence {i} on page {number} of the fixture document about raida."
            for i in range(1, 25)
        )
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 760), body, fontsize=11)
    doc.save(str(path))
    doc.close()


def make_image_pdf(path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    # Draw text into a pixmap and place it as an image so the page has no text layer.
    tmp = pymupdf.open()
    src = tmp.new_page()
    src.insert_text((72, 100), "Scanned page: the treaty was signed in 1648.", fontsize=16)
    pix = src.get_pixmap(dpi=40, colorspace=pymupdf.csGRAY)
    page.insert_image(page.rect, stream=pix.tobytes("jpeg", jpg_quality=60))
    doc.save(str(path))
    doc.close()
    tmp.close()


def make_media(wav: Path, mp4: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run(
        [
            *base,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        check=True,
    )
    subprocess.run(
        [
            *base,
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:d=5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=5",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "32k",
            str(mp4),
        ],
        check=True,
    )


def main() -> None:
    make_text_pdf(HERE / "text.pdf")
    make_image_pdf(HERE / "scanned.pdf")
    make_media(HERE / "tone.wav", HERE / "clip.mp4")
    (HERE / "notes.md").write_text(
        "# Meeting notes\n\n- Decision: ship raida v1\n- Owner: the team\n\nSecond paragraph.\n",
        encoding="utf-8",
    )
    (HERE / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,500\nHello and welcome.\n\n"
        "2\n00:00:02,500 --> 00:00:05,000\nToday we talk about harnesses.\n",
        encoding="utf-8",
    )
    for p in sorted(HERE.iterdir()):
        if p.suffix != ".py":
            print(p.name, p.stat().st_size)


if __name__ == "__main__":
    main()
