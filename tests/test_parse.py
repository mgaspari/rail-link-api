"""Tests for scripts/parse.py and the schema → generate → validate path.

The PDF snapshot test runs only when ``tests/fixtures/118701.pdf`` is
present. It is downloaded in CI by the refresh workflow; for local
development you can drop a copy at that path. When the fixture is
missing the test is skipped rather than failed so unrelated changes
don't go red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate, parse, schemas, validate

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PDF_FIXTURE = FIXTURE_DIR / "118701.pdf"
SNAPSHOT = FIXTURE_DIR / "118701.parsed.json"


def test_normalize_time_cell_pads_hour() -> None:
    assert parse._normalize_time_cell("6 44") == "06:44"
    assert parse._normalize_time_cell("12 05") == "12:05"
    assert parse._normalize_time_cell("  9 30 ") == "09:30"


def test_normalize_time_cell_handles_colon_form() -> None:
    assert parse._normalize_time_cell("6:44") == "06:44"
    assert parse._normalize_time_cell("18:07") == "18:07"


def test_normalize_time_cell_empty_returns_none() -> None:
    assert parse._normalize_time_cell("") is None
    assert parse._normalize_time_cell("   ") is None
    assert parse._normalize_time_cell(None) is None
    assert parse._normalize_time_cell("--") is None


def test_to_24h_am_pm() -> None:
    assert parse._to_24h("06:44", "AM") == "06:44"
    assert parse._to_24h("12:05", "AM") == "00:05"
    assert parse._to_24h("12:30", "PM") == "12:30"
    assert parse._to_24h("01:15", "PM") == "13:15"
    assert parse._to_24h("11:59", "PM") == "23:59"


def test_parse_period_row_forward_fills() -> None:
    row = [None, "AM", None, None, "PM", None]
    assert parse._parse_period_row(row) == [None, "AM", "AM", "AM", "PM", "PM"]


def test_slugify_lowercases_and_dashes() -> None:
    assert parse._slugify("Spuyten Duyvil") == "spuyten-duyvil"
    assert parse._slugify("W 231 St / Broadway") == "w-231-st-broadway"


def test_schema_rejects_bad_time() -> None:
    with pytest.raises(Exception):
        schemas.Departure(time="6:44", route="L", direction="to-ny")
    with pytest.raises(Exception):
        schemas.Departure(time="25:00", route="L", direction="to-ny")


def test_schema_rejects_bad_route() -> None:
    with pytest.raises(Exception):
        schemas.Departure(time="06:44", route="LL", direction="to-ny")
    with pytest.raises(Exception):
        schemas.Departure(time="06:44", route="l", direction="to-ny")


def test_schema_rejects_unknown_direction() -> None:
    with pytest.raises(Exception):
        schemas.Departure(time="06:44", route="L", direction="to-mars")


def _fake_parsed() -> dict[str, dict[str, object]]:
    return {
        "weekday": {
            "effective_date": "2025-10-06",
            "service_day": "weekday",
            "directions": {
                "to-ny": {
                    "stops": [
                        {"id": "spuyten-duyvil", "name": "Spuyten Duyvil"},
                        {"id": "marble-hill", "name": "Marble Hill"},
                    ],
                    "trips": [
                        {"route": "L", "stops": {
                            "spuyten-duyvil": "06:44",
                            "marble-hill": "06:50",
                        }},
                        {"route": "J", "stops": {
                            "spuyten-duyvil": "07:14",
                        }},
                    ],
                },
                "to-spuyten-duyvil": {
                    "stops": [
                        {"id": "marble-hill", "name": "Marble Hill"},
                        {"id": "spuyten-duyvil", "name": "Spuyten Duyvil"},
                    ],
                    "trips": [
                        {"route": "K", "stops": {
                            "marble-hill": "17:30",
                            "spuyten-duyvil": "17:38",
                        }},
                    ],
                },
            },
        },
    }


def test_generate_then_validate_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(generate, "DOCS_API", tmp_path / "api")
    monkeypatch.setattr(generate, "STOPS_DIR", tmp_path / "api" / "stops")
    monkeypatch.setattr(validate, "DOCS_API", tmp_path / "api")
    monkeypatch.setattr(validate, "STOPS_DIR", tmp_path / "api" / "stops")

    sources = [{
        "url": "https://www.mta.info/document/118701",
        "hash": "0" * 64,
        "service": "weekday",
    }]
    generate.generate(_fake_parsed(), sources)

    errors = validate.validate()
    assert errors == [], errors

    index = json.loads((tmp_path / "api" / "index.json").read_text())
    assert {s["id"] for s in index["stops"]} == {"spuyten-duyvil", "marble-hill"}
    assert index["routes"] == ["J", "K", "L"]
    assert index["services"] == ["weekday"]

    stop = json.loads((tmp_path / "api" / "stops" / "spuyten-duyvil.json").read_text())
    deps = stop["departures"]["weekday"]
    assert len(deps) == 3
    assert deps[0]["time"] == "06:44"
    assert deps[0]["direction"] == "to-ny"


def test_validate_flags_oversized_index(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(validate, "DOCS_API", tmp_path / "api")
    monkeypatch.setattr(validate, "STOPS_DIR", tmp_path / "api" / "stops")
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "stops").mkdir()

    big_stops = [{"id": f"stop-{i:04d}", "name": "x" * 80} for i in range(800)]
    (tmp_path / "api" / "index.json").write_text(json.dumps({
        "stops": big_stops,
        "routes": ["L"],
        "services": ["weekday"],
    }))
    (tmp_path / "api" / "meta.json").write_text(json.dumps({
        "effective_date": "2025-10-06",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "sources": [{
            "url": "https://www.mta.info/document/118701",
            "hash": "0" * 64,
            "service": "weekday",
        }],
        "fair_use": "x",
    }))
    errors = validate.validate()
    assert any("index.json" in e and ">" in e for e in errors), errors


@pytest.mark.skipif(not PDF_FIXTURE.exists(), reason="118701.pdf fixture not present")
def test_parse_118701_snapshot(tmp_path) -> None:
    parsed = parse.parse_pdf(PDF_FIXTURE, "weekday")
    if not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(parsed, indent=2, sort_keys=True))
        pytest.skip("snapshot written; rerun to compare")
    expected = json.loads(SNAPSHOT.read_text())
    assert parsed == expected
