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
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pdfplumber

from scripts.schemas import Direction, ServiceDay

REPO_ROOT = Path(__file__).resolve().parent.parent

EFFECTIVE_ANCHOR_RE = re.compile(r"effective(?:\s+date)?\b", re.IGNORECASE)
EFFECTIVE_MONTH_DATE_RE = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})"
)
EFFECTIVE_NUMERIC_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
TIME_PAIR_RE = re.compile(r"^\s*(\d{1,2})\s+(\d{2})\s*$")
SPLIT_PREV_TIME_RE = re.compile(r"^\s*(\d{1,2})\s+(\d)\s*$")
SPLIT_CURR_TIME_RE = re.compile(r"^\s*(\d)\s+(\d{1,2})\s+(\d{2})\s*$")
ROUTE_LETTERS = {"J", "K", "L", "M"}
AM_PM_RE = re.compile(r"^(AM|PM)$", re.IGNORECASE)
TRAIN_ROW_RE = re.compile(
    r"spuyten\s*duyvil.*lv\.?.*grand\s*central", re.IGNORECASE | re.DOTALL
)
BUS_SD_ROW_RE = re.compile(r"^spuyten\s*duyvil\s*station", re.IGNORECASE)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s


def _repair_split_time_cells(
    row: list[str | None],
) -> list[str | None]:
    """Recover times whose last minute digit pdfplumber pushed into the next cell.

    Some off-peak columns in the weekday PDF have vertical lines that
    fall a digit-width left of where they belong, so a cell that should
    read "10 02" arrives as "10 0" while the adjacent cell starts with
    the stray "2" ("2 10 24"). Multi-line cells (the train-connection
    row) exhibit the same defect on each line. Repair line-by-line so
    we don't mistakenly merge unrelated content.
    """
    out = list(row)
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        if not a or not b:
            continue
        a_lines = a.split("\n")
        b_lines = b.split("\n")
        new_a = list(a_lines)
        new_b = list(b_lines)
        changed = False
        for k in range(min(len(a_lines), len(b_lines))):
            m_a = SPLIT_PREV_TIME_RE.match(a_lines[k])
            m_b = SPLIT_CURR_TIME_RE.match(b_lines[k])
            if m_a and m_b:
                new_a[k] = f"{m_a.group(1)} {m_a.group(2)}{m_b.group(1)}"
                new_b[k] = f"{m_b.group(2)} {m_b.group(3)}"
                changed = True
        if changed:
            out[i] = "\n".join(new_a)
            out[i + 1] = "\n".join(new_b)
    return out


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


def _parse_train_cell(
    cell: str | None,
    sd_arrival_24h: str | None,
    fallback_period: str | None,
) -> tuple[str, str] | None:
    """Parse a merged train cell like "6 03\\n6 32\\nAM" into 24h (sd_dep, gc_arr).

    pdfplumber smashes the train-connection row's three logical sub-rows
    (SD Lv. / GC Ar. / AM-PM) into a single cell per column on document
    118701. The single AM/PM marker in the merged cell can refer to
    either line, so we resolve the sd_departure period by requiring
    ``sd_24 >= sd_arrival_24h`` (the train can't leave before the bus
    arrives) and pick the gc_arrival period that keeps the train trip
    same-day when possible, accepting a cross-midnight value otherwise.
    Returns None when the cell is missing, malformed, or no period
    assignment is consistent with the bus arrival.
    """
    if not cell:
        return None
    parts = [p.strip() for p in cell.split("\n") if p.strip()]
    if len(parts) < 2:
        return None
    sd_12 = _normalize_time_cell(parts[0])
    gc_12 = _normalize_time_cell(parts[1])
    if sd_12 is None or gc_12 is None:
        return None
    cell_period: str | None = None
    if len(parts) >= 3 and parts[2].upper() in {"AM", "PM"}:
        cell_period = parts[2].upper()

    periods: list[str] = []
    for p in (fallback_period, cell_period, "AM", "PM"):
        if p in {"AM", "PM"} and p not in periods:
            periods.append(p)
    if not periods:
        return None

    sd_24: str | None = None
    for p in periods:
        try:
            t = _to_24h(sd_12, p)
        except ValueError:
            continue
        if sd_arrival_24h is None or t >= sd_arrival_24h:
            sd_24 = t
            break
    if sd_24 is None:
        return None

    gc_candidates: list[str] = []
    for p in periods:
        try:
            gc_candidates.append(_to_24h(gc_12, p))
        except ValueError:
            continue
    if not gc_candidates:
        return None
    same_day = [c for c in gc_candidates if c >= sd_24]
    gc_24 = min(same_day) if same_day else min(gc_candidates)
    return sd_24, gc_24


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


def _parse_effective_date_from_text(text: str) -> date | None:
    """Locate the effective date in a page of text.

    Tolerates variations like "Effective October 6, 2025",
    "Effective: Oct 6, 2025", "EFFECTIVE DATE: 10/06/2025", and an
    optional weekday word ("Effective Monday, October 6, 2025") that
    sits between the anchor and the date.
    """
    anchor = EFFECTIVE_ANCHOR_RE.search(text)
    if anchor is None:
        return None
    window = text[anchor.end() : anchor.end() + 120]

    m = EFFECTIVE_MONTH_DATE_RE.search(window)
    if m is not None:
        month_str, day_str, year_str = m.group(1), m.group(2), m.group(3)
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(
                    f"{month_str} {day_str} {year_str}", fmt
                ).date()
            except ValueError:
                continue

    n = EFFECTIVE_NUMERIC_DATE_RE.search(window)
    if n is not None:
        mm, dd, yyyy = (int(g) for g in n.groups())
        try:
            return date(yyyy, mm, dd)
        except ValueError:
            return None
    return None


def _effective_date_diagnostic(pdf: pdfplumber.PDF) -> str:
    snippets: list[str] = []
    for page_num, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        for m in re.finditer(r"effective", text, re.IGNORECASE):
            start = max(0, m.start() - 20)
            end = min(len(text), m.end() + 80)
            snippets.append(f"page {page_num}: …{text[start:end]!r}…")
            if len(snippets) >= 5:
                return "\n".join(snippets)
    if snippets:
        return "\n".join(snippets)
    first_text = (pdf.pages[0].extract_text() or "")[:200] if pdf.pages else ""
    return f"no 'effective' substring found; page 1 head: {first_text!r}"


def _detect_effective_date(pdf: pdfplumber.PDF) -> date:
    for page in pdf.pages:
        text = page.extract_text() or ""
        result = _parse_effective_date_from_text(text)
        if result is not None:
            return result
    raise ValueError(
        "could not locate effective date in PDF; nearby text:\n"
        + _effective_date_diagnostic(pdf)
    )


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


def _table_head_snippet(table: list[list[str | None]], rows: int = 6) -> str:
    head = [
        [((c or "").strip() or "·") for c in (row or [])]
        for row in table[:rows]
    ]
    return "\n".join(f"  row {i}: {r!r}" for i, r in enumerate(head))


class _RouteRowNotFound(ValueError):
    """Raised when _parse_table cannot find the route-letter header row."""


def _parse_table(
    table: list[list[str | None]],
    direction: Direction,
) -> dict[str, object]:
    """Turn one pdfplumber table into a stops + trips dict for one direction."""
    period_row_idx: int | None = None
    route_row_idx: int | None = None
    for i, row in enumerate(table[:6]):
        cells = [(c or "").strip().upper() for c in row]
        if period_row_idx is None and any(AM_PM_RE.match(c) for c in cells if c):
            period_row_idx = i
        if route_row_idx is None and any(c in ROUTE_LETTERS for c in cells):
            route_row_idx = i
        if period_row_idx is not None and route_row_idx is not None:
            break
    if route_row_idx is None:
        raise _RouteRowNotFound(
            "could not locate route-letter header row; first rows:\n"
            + _table_head_snippet(table)
        )
    if period_row_idx is None:
        period_row_idx = max(0, route_row_idx - 1)

    period_row = _parse_period_row(table[period_row_idx])
    route_row = [(c or "").strip().upper() for c in table[route_row_idx]]
    header_end = max(route_row_idx, period_row_idx)
    data_rows = table[header_end + 1 :]

    n_cols = len(route_row)
    trip_cols = [
        i for i in range(1, n_cols) if route_row[i] in ROUTE_LETTERS
    ]

    train_row_idx: int | None = None
    sd_stop_slug: str | None = None
    stops: list[dict[str, str]] = []
    for r_idx, row in enumerate(data_rows):
        name = (row[0] or "").strip() if row else ""
        if not name:
            continue
        if name.upper() in {"AM", "PM"} or name.upper() in ROUTE_LETTERS:
            continue
        if TRAIN_ROW_RE.search(name):
            train_row_idx = r_idx
            continue
        sid = _slugify(name)
        if not sid:
            continue
        stops.append({"id": sid, "name": name})
        if sd_stop_slug is None and BUS_SD_ROW_RE.search(name.split("\n")[0]):
            sd_stop_slug = sid

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
        for r_idx, row in enumerate(data_rows):
            if r_idx == train_row_idx:
                continue
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
        if not col_stop_times:
            continue
        trip: dict[str, object] = {"route": route, "stops": col_stop_times}
        if sd_stop_slug and sd_stop_slug in col_stop_times:
            trip["sd_arrival"] = col_stop_times[sd_stop_slug]
        if train_row_idx is not None:
            train_row = data_rows[train_row_idx]
            train_cell = train_row[col] if col < len(train_row) else None
            sd_arr = col_stop_times.get(sd_stop_slug) if sd_stop_slug else None
            ct = _parse_train_cell(train_cell, sd_arr, period_cursor)
            if ct is not None:
                trip["connecting_train"] = {
                    "sd_departure": ct[0],
                    "gc_arrival": ct[1],
                }
        trips.append(trip)

    return {"stops": stops, "trips": trips}


def _extract_tables(page: pdfplumber.page.Page) -> list[list[list[str | None]]]:
    settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
    }
    raw = page.extract_tables(settings)
    return [
        [_repair_split_time_cells(row) for row in t]
        for t in raw
        if t and len(t) >= 3
    ]


def parse_pdf(pdf_path: Path, service_day: ServiceDay) -> dict[str, object]:
    pdf_path = Path(pdf_path)
    skip_reasons: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        effective_date = _detect_effective_date(pdf)
        directions: dict[Direction, dict[str, object]] = {}
        for page_num, page in enumerate(pdf.pages, start=1):
            for table_idx, table in enumerate(_extract_tables(page)):
                direction = _classify_table(table)
                if direction is None:
                    continue
                try:
                    parsed = _parse_table(table, direction)
                except _RouteRowNotFound as exc:
                    skip_reasons.append(
                        f"page {page_num} table {table_idx} "
                        f"(classified as {direction}): {exc}"
                    )
                    continue
                existing = directions.get(direction)
                if existing is None:
                    directions[direction] = parsed
                else:
                    existing["trips"].extend(parsed["trips"])  # type: ignore[union-attr]
    total_trips = sum(len(d["trips"]) for d in directions.values())  # type: ignore[arg-type]
    if not directions or total_trips == 0:
        detail = (
            "\nskipped tables:\n" + "\n".join(skip_reasons)
            if skip_reasons
            else ""
        )
        raise ValueError(
            f"no usable schedule data in PDF "
            f"(directions={list(directions)}, total_trips={total_trips})"
            + detail
        )
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
