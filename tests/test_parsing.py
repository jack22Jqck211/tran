"""Unit tests for LLM response parsing robustness."""
from app.translation.parsing import coverage, parse_translations

IDS = [1, 2, 3]


def test_clean_json_array():
    raw = '[{"i": 1, "t": "یک"}, {"i": 2, "t": "دو"}, {"i": 3, "t": "سه"}]'
    assert parse_translations(raw, IDS) == {1: "یک", 2: "دو", 3: "سه"}


def test_fenced_json():
    raw = '```json\n[{"i": 1, "t": "الف"}]\n```'
    assert parse_translations(raw, IDS) == {1: "الف"}


def test_object_map():
    raw = '{"1": "یک", "2": "دو"}'
    assert parse_translations(raw, IDS) == {1: "یک", 2: "دو"}


def test_alternative_keys():
    raw = '[{"index": 2, "text": "دو"}]'
    assert parse_translations(raw, IDS) == {2: "دو"}


def test_numbered_lines_fallback():
    raw = "1. اولین خط\n2) دومین خط\n3 - سومین خط"
    result = parse_translations(raw, IDS)
    assert result[1] == "اولین خط"
    assert result[2] == "دومین خط"
    assert result[3] == "سومین خط"


def test_trailing_comma_repair():
    raw = '[{"i": 1, "t": "x"},]'
    assert parse_translations(raw, IDS) == {1: "x"}


def test_unexpected_ids_filtered():
    raw = '[{"i": 99, "t": "نامربوط"}, {"i": 1, "t": "یک"}]'
    assert parse_translations(raw, IDS) == {1: "یک"}


def test_prose_wrapped_json():
    raw = 'Sure! Here are the translations:\n[{"i": 1, "t": "سلام"}]\nHope it helps.'
    assert parse_translations(raw, IDS) == {1: "سلام"}


def test_coverage():
    assert coverage({1: "a", 2: "b"}, [1, 2]) == 1.0
    assert coverage({1: "a"}, [1, 2]) == 0.5
    assert coverage({}, []) == 1.0
