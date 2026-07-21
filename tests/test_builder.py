"""Unit tests for subtitle cue building (split/merge/timing rules)."""
from app.speech.types import SegmentData, WordSpan
from app.subtitles.builder import BuilderOptions, build_cues


def make_words(n, start=0.0, step=0.5, prefix="word"):
    words = []
    t = start
    for i in range(n):
        words.append(WordSpan(start=t, end=t + step * 0.9,
                              word="%s%d" % (prefix, i)))
        t += step
    return words


def test_long_segment_is_split():
    words = make_words(40)  # 20 seconds, ~240 chars
    seg = SegmentData(start=0.0, end=20.0,
                      text=" ".join(w.word for w in words), words=words)
    cues = build_cues([seg])
    assert len(cues) > 1
    for cue in cues:
        assert cue.duration <= 8.0
        assert len(cue.text) <= 100


def test_short_fragments_are_merged():
    segs = [
        SegmentData(start=0.0, end=0.5, text="سلام"),
        SegmentData(start=0.6, end=1.2, text="چطوری؟"),
    ]
    cues = build_cues(segs)
    assert len(cues) == 1
    assert cues[0].text == "سلام چطوری؟"


def test_no_overlap_and_monotonic_indices():
    words = make_words(60)
    seg = SegmentData(start=0.0, end=30.0,
                      text=" ".join(w.word for w in words), words=words)
    short = SegmentData(start=30.2, end=30.6, text="پایان")
    cues = build_cues([seg, short])
    for i in range(1, len(cues)):
        assert cues[i].start >= cues[i - 1].end, "cues must not overlap"
    assert [c.index for c in cues] == list(range(1, len(cues) + 1))


def test_min_duration_extension():
    segs = [SegmentData(start=0.0, end=0.4, text="یک جمله نسبتا بلند برای خواندن")]
    cues = build_cues(segs)
    assert cues[0].duration >= 1.0


def test_empty_segments_skipped():
    segs = [SegmentData(start=0.0, end=1.0, text="   "),
            SegmentData(start=1.0, end=2.0, text="متن")]
    cues = build_cues(segs)
    assert len(cues) == 1


def test_sentence_boundary_preferred_on_split():
    words = make_words(10, step=0.4)
    words[4] = WordSpan(start=words[4].start, end=words[4].end,
                        word=words[4].word + ".")
    text = " ".join(w.word for w in words)
    seg = SegmentData(start=0.0, end=4.0, text=text, words=words)
    cues = build_cues([seg], BuilderOptions(max_chars=30, max_duration=10.0))
    assert any(c.text.endswith(".") for c in cues[:-1])
