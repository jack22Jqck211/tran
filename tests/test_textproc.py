"""Unit tests for Persian text normalization and line wrapping."""
from app.utils.textproc import (has_persian, normalize_fa,
                                strip_wrapping_quotes, wrap_lines)


def test_arabic_chars_unified():
    assert normalize_fa("علي كتاب") == "علی کتاب"


def test_question_mark_persianized():
    assert normalize_fa("چطوری ?") == "چطوری؟"


def test_space_before_punct_removed():
    assert normalize_fa("سلام ، دنیا") == "سلام، دنیا"


def test_space_added_after_comma():
    assert normalize_fa("سلام،دنیا") == "سلام، دنیا"


def test_latin_comma_between_persian_words():
    assert normalize_fa("سلام, دنیا") == "سلام، دنیا"


def test_has_persian():
    assert has_persian("سلام")
    assert not has_persian("hello")


def test_wrap_short_line_untouched():
    assert wrap_lines("سلام دنیا", max_len=42) == ["سلام دنیا"]


def test_wrap_long_line_two_lines():
    text = "این یک جمله بسیار طولانی برای آزمایش شکستن خطوط زیرنویس در دو خط است"
    lines = wrap_lines(text, max_len=42, max_lines=2)
    assert 2 <= len(lines) <= 3
    assert " ".join(lines) == text


def test_strip_wrapping_quotes():
    assert strip_wrapping_quotes('"سلام"') == "سلام"
    assert strip_wrapping_quotes("«سلام»") == "سلام"
    assert strip_wrapping_quotes('او گفت "سلام" و رفت') == 'او گفت "سلام" و رفت'
