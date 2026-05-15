"""Parse Hudson Rail Link timetable PDFs into intermediate Python dicts.

The PDF layout (per the source 118701 and its weekend siblings):

- One PDF per service day (weekday / saturday / sunday).
- Each page carries two tables: outbound (Spuyten Duyvil → NY) on the
  top half, inbound (NY → Spuyten Duyvil) on the bottom half.
- Within a table, the leftmost column is the list of stop names. Every
  subsequent column is one trip.
- Above the trip columns sits a route letter (L / J / K / M) — the
  trip metadata.
- Above the route letters, a single "AM"/"PM" label row groups columns.
- Time cells are printed space-separated ("6 44") and need to be
  normalized to ``HH:MM``.
- An empty cell means the trip skips that stop — do not emit a
  departure for it.
- Within a row the times are monotonic; we use that to convert 12h
  labels to 24h and to flag malformed columns.

The output of :func:`parse_pdf` is a Python dict with shape::

    {
        "effective_date": "2025-10-06",
        "service_day": "weekday",
        "directions": {
            "to-ny": {
                "stops": [{"id": "...", "name": "..."}, ...],
                "trips": [
                    {"route": "L", "stops": {"<stop_id>": "06:44", ...}},
                    ...
                ],
            },
            "to-spuyten-duyvil": { ... },
        },
    }
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

import pdfplumber

from scripts.schemas import Direction, ServiceDay

REPO_ROOT = Path(__file__).resolve().parent.parent

EFFECTIVE_DATE_RE = re.compile(
    r"[Ee]ffective\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})"
)
TIME_PAIR_RE = re.compile(r"^\s*(\d{1,2})\s+(\d{2})\s*$")
ROUTE_LETTERS = {"J", "K", "L", "M"}
AM_PM_RE = re.compile(r"^(AM|PM)$", re.IGNORECASE)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s


def _normalize_time_cell(cell: str | None) -> str | None:
    """Convert "6 44" → "06:44". Return None for an empty cell."""
    if cell is None:
        return None
    text = cell.strip()
    if not text:
        return None
    m = TIME_PAIR_RE.match(text)
    if not m:
        if ":" in text and re.fullmatch(r"\d{1,2}:\d{2}", text):
            h, mn = text.split(":")
            return f"{int(h):02d}:{mn}"
        return None
    hh, mm = m.group(1), m.group(2)
    return f"{int(hh):02d}:{mm}"


def _to_24h(time_12h: str, period: str) -> str:
    """Combine ``HH:MM`` (12h) with AM/PM into a 24h ``HH:MM`` string."""
    hh, mm = time_12h.split(":")
    h = int(hh)
    p = period.upper()
    if p == "AM":
        h = 0 if h == 12 else h
    elif p == "PM":
        h = h if h == 12 else h + 12
    else:
        raise ValueError(f"period must be AM or PM, got {period!r}")
    return f"{h:02d}:{mm}"


def _parse_period_row(row: list[str | None]) -> list[str | None]:
    """Forward-fill the AM/PM label row across columns."""
    out: list[str | None] = []
    current: str | None = None
    for cell in row:
        if cell:
            t = cell.strip().upper()
            if AM_PM_RE.match(t):
                current = t
        out.append(current)
    return out


def _detect_effective_date(pdf: pdfplumber.PDF) -> date:
    for page in pdf.pages:
        text = page.extract_text() or ""
        m = EFFECTIVE_DATE_RE.search(text)
        if m:
            from datetime import datetime as _dt

            for fmt in ("%B %d, %Y", "%B %d %Y"):
                try:
                    return _dt.strptime(m.group(1).replace(",", ""), fmt.replace(",", "")).date()
                except ValueError:
                    continue
    raise ValueError("could not locate effective date in PDF")


def _classify_table(table: list[list[str | None]]) -> Direction | None:
    """A table is "outbound" (to-ny) if the first stop column lists
    Spuyten Duyvil before Marble Hill / Inwood / etc; otherwise inbound.
    """
    first_col = [(r[0] or "").lower() for r in table if r and r[0]]
    if not first_col:
        return None
    joined = " | ".join(first_col)
    spuyten_idx = next(
        (i for i, c in enumerate(first_col) if "spuyten" in c),
        None,
    )
    grand_central_idx = next(
        (i for i, c in enumerate(first_col) if "grand central" in c or "harlem" in c),
        None,
    )
    if spuyten_idx is None and grand_central_idx is None:
        return None
    if spuyten_idx is not None and grand_central_idx is not None:
        return "to-ny" if spuyten_idx < grand_central_idx else "to-spuyten-duyvil"
    if "spuyten" in joined and "ny" in joined:
        return "to-ny" if spuyten_idx == 0 else "to-spuyten-duyvil"
    return "to-ny" if spuyten_idx == 0 else "to-spuyten-duyvil"


def _parse_table(
    table: list[list[str | None]],
    direction: Direction,
) -> dict[str, object]:
    """Turn one pdfplumber table into a stops + trips dict for one direction."""
    period_row_idx: int | None = None
    route_row_idx: int | None = None
    for i, row in enumerate(table[:6]):
        cells = [(c or "").strip().upper() for c in row]
        if any(AM_PM_RE.match(c) for c in cells if c):
            period_row_idx = i
        if any(c in ROUTE_LETTERS for c in cells):
            route_row_idx = i
            break
    if route_row_idx is None:
        raise ValueError("could not locate route-letter header row")
    if period_row_idx is None:
        period_row_idx = max(0, route_row_idx - 1)

    period_row = _parse_period_row(table[period_row_idx])
    route_row = [(c or "").strip().upper() for c in table[route_row_idx]]
    data_rows = table[route_row_idx + 1 :]

    n_cols = len(route_row)
    trip_cols = [
        i for i in range(1, n_cols) if route_row[i] in ROUTE_LETTERS
    ]

    stops: list[dict[str, str]] = []
    for row in data_rows:
        name = (row[0] or "").strip() if row else ""
        if not name:
            continue
        if name.upper() in {"AM", "PM"} or name.upper() in ROUTE_LETTERS:
            continue
        sid = _slugify(name)
        if not sid:
            continue
        stops.append({"id": sid, "name": name})

    trips: list[dict[str, object]] = []
    for col in trip_cols:
        route = route_row[col]
        period = period_row[col] if col < len(period_row) else None
        if period is None:
            continue
        col_stop_times: dict[str, str] = {}
        last_24h: str | None = None
        period_cursor = period
        stop_iter = iter(stops)
        for row in data_rows:
            name = (row[0] or "").strip() if row else ""
            if not name:
                continue
            if name.upper() in {"AM", "PM"} or name.upper() in ROUTE_LETTERS:
                continue
            stop_def = next(stop_iter, None)
            if stop_def is None:
                break
            cell = row[col] if col < len(row) else None
            t12 = _normalize_time_cell(cell)
            if t12 is None:
                continue
            t24 = _to_24h(t12, period_cursor)
            if last_24h is not None and t24 < last_24h:
                period_cursor = "PM" if period_cursor == "AM" else "AM"
                t24 = _to_24h(t12, period_cursor)
                if last_24h is not None and t24 < last_24h:
                    raise ValueError(
                        f"non-monotonic times in trip col={col}: "
                        f"{last_24h} → {t24} ({t12} {period_cursor})"
                    )
            col_stop_times[stop_def["id"]] = t24
            last_24h = t24
        if col_stop_times:
            trips.append({"route": route, "stops": col_stop_times})

    return {"stops": stops, "trips": trips}


def _extract_tables(page: pdfplumber.page.Page) -> list[list[list[str | None]]]:
    settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
    }
    raw = page.extract_tables(settings)
    return [t for t in raw if t and len(t) >= 3]


def parse_pdf(pdf_path: Path, service_day: ServiceDay) -> dict[str, object]:
    pdf_path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        effective_date = _detect_effective_date(pdf)
        directions: dict[Direction, dict[str, object]] = {}
        for page in pdf.pages:
            for table in _extract_tables(page):
                direction = _classify_table(table)
                if direction is None:
                    continue
                parsed = _parse_table(table, direction)
                existing = directions.get(direction)
                if existing is None:
                    directions[direction] = parsed
                else:
                    existing["trips"].extend(parsed["trips"])  # type: ignore[union-attr]
    if not directions:
        raise ValueError("no schedule tables found in PDF")
    return {
        "effective_date": effective_date.isoformat(),
        "service_day": service_day,
        "directions": {
            d: directions.get(d, {"stops": [], "trips": []})
            for d in ("to-ny", "to-spuyten-duyvil")
        },
    }


def parse_many(jobs: Iterable[dict[str, str]]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for job in jobs:
        path = REPO_ROOT / job["path"] if not Path(job["path"]).is_absolute() else Path(job["path"])
        service: ServiceDay = job["service_day"]  # type: ignore[assignment]
        out[service] = parse_pdf(path, service)
    return out


def main(argv: list[str] | None = None) -> int:
    import json

    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print("usage: parse.py <pdf_path> <service_day>", file=sys.stderr)
        return 2
    pdf_path = Path(argv[0])
    service_day: ServiceDay = argv[1] if len(argv) > 1 else "weekday"  # type: ignore[assignment]
    parsed = parse_pdf(pdf_path, service_day)
    print(json.dumps(parsed, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
