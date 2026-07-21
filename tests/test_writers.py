"""Unit tests for subtitle writers: format correctness + identical timing."""
from app.subtitles.builder import Cue
from app.subtitles.writers import (RenderOptions, original_text, write_ass,
                                   write_outputs, write_srt, write_txt,
                                   write_vtt)

CUES = [
    Cue(index=1, start=0.0, end=2.5, text="Hello world",
        translation="سلام دنیا"),
    Cue(index=2, start=3.0, end=5.25, text="Second line",
        translation="خط دوم"),
]
OPTS = RenderOptions(rtl_marks=False)


def test_srt_format(tmp_path):
    path = write_srt(CUES, tmp_path / "a.srt", original_text, OPTS)
    content = path.read_text(encoding="utf-8-sig")
    assert "1\n00:00:00,000 --> 00:00:02,500\nHello world" in content
    assert "2\n00:00:03,000 --> 00:00:05,250\nSecond line" in content


def test_vtt_format(tmp_path):
    path = write_vtt(CUES, tmp_path / "a.vtt", original_text, OPTS)
    content = path.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.500" in content


def test_ass_format(tmp_path):
    path = write_ass(CUES, tmp_path / "a.ass", original_text, OPTS)
    content = path.read_text(encoding="utf-8-sig")
    assert "[Script Info]" in content
    assert content.count("Dialogue:") == 2
    assert "0:00:02.50" in content


def test_txt_format(tmp_path):
    path = write_txt(CUES, tmp_path / "a.txt", original_text, OPTS)
    assert path.read_text(encoding="utf-8").splitlines() == [
        "Hello world", "Second line"]


def test_outputs_translated_includes_original_srt(tmp_path):
    files = write_outputs(CUES, tmp_path, "movie", ["srt", "vtt"], "en",
                          translated=True, opts=OPTS)
    names = sorted(f.name for f in files)
    assert names == ["movie.en.srt", "movie.fa.srt", "movie.fa.vtt"]
    fa = (tmp_path / "movie.fa.srt").read_text(encoding="utf-8-sig")
    assert "سلام دنیا" in fa
    assert "00:00:00,000 --> 00:00:02,500" in fa  # timing identical to source
