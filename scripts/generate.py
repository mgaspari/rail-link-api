"""Serialize parsed schedule dicts to docs/api/{index,meta}.json and per-stop files.

Reads parsed dicts from :func:`scripts.parse.parse_many` (one per service
day), merges them into:

- ``docs/api/index.json`` — stops + routes + services
- ``docs/api/meta.json``  — effective_date, last_updated, sources, fair_use
- ``docs/api/stops/<slug>.json`` — per-stop departures

All output goes through pydantic models before being written, so any
malformed payload raises before a file is touched.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.schemas import (
    Departure,
    Direction,
    Index,
    Meta,
    ServiceDay,
    Source,
    Stop,
    StopRef,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_API = REPO_ROOT / "docs" / "api"
STOPS_DIR = DOCS_API / "stops"

FAIR_USE = (
    "Schedule data republished from the MTA's Hudson Rail Link timetables "
    "for convenience. The MTA is the authoritative source; check the "
    "linked PDFs and meta.json before relying on these times."
)


def _merge_stops(parsed: dict[str, dict[str, object]]) -> list[StopRef]:
    """Union the stop lists across services and directions, preserving order."""
    seen: dict[str, str] = {}
    order: list[str] = []
    for service_data in parsed.values():
        directions = service_data.get("directions", {})  # type: ignore[union-attr]
        for direction_data in directions.values():  # type: ignore[union-attr]
            for stop in direction_data.get("stops", []):  # type: ignore[union-attr]
                sid = stop["id"]
                if sid not in seen:
                    seen[sid] = stop["name"]
                    order.append(sid)
    return [StopRef(id=sid, name=seen[sid]) for sid in order]


def _collect_routes(parsed: dict[str, dict[str, object]]) -> list[str]:
    routes: set[str] = set()
    for service_data in parsed.values():
        for direction_data in service_data.get("directions", {}).values():  # type: ignore[union-attr]
            for trip in direction_data.get("trips", []):  # type: ignore[union-attr]
                routes.add(trip["route"])
    return sorted(routes)


def _build_departures(
    parsed: dict[str, dict[str, object]],
    stop_id: str,
) -> dict[ServiceDay, list[Departure]]:
    out: dict[ServiceDay, list[Departure]] = {}
    for service, service_data in parsed.items():
        deps: list[Departure] = []
        for direction, direction_data in service_data["directions"].items():  # type: ignore[index]
            for trip in direction_data["trips"]:  # type: ignore[index]
                time = trip["stops"].get(stop_id)
                if time is None:
                    continue
                deps.append(
                    Departure(
                        time=time,
                        route=trip["route"],
                        direction=direction,
                        sd_arrival=trip.get("sd_arrival"),
                        connecting_train=trip.get("connecting_train"),
                    )
                )
        if deps:
            deps.sort(key=lambda d: (d.time, d.route, d.direction))
            out[service] = deps  # type: ignore[assignment]
    return out


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def _referenced_stop_ids(parsed: dict[str, dict[str, object]]) -> set[str]:
    """Stop ids that at least one trip records a time for."""
    referenced: set[str] = set()
    for service_data in parsed.values():
        for direction_data in service_data.get("directions", {}).values():  # type: ignore[union-attr]
            for trip in direction_data.get("trips", []):  # type: ignore[union-attr]
                referenced.update(trip["stops"].keys())
    return referenced


def generate(
    parsed: dict[str, dict[str, object]],
    sources: list[dict[str, str]],
) -> None:
    """Write index.json, meta.json, and per-stop JSON files."""
    candidate_stops = _merge_stops(parsed)
    referenced = _referenced_stop_ids(parsed)
    stops = [s for s in candidate_stops if s.id in referenced]
    for dropped in candidate_stops:
        if dropped.id not in referenced:
            print(
                f"warn: dropping stop {dropped.id!r} "
                f"({dropped.name!r}) with zero departures",
                file=sys.stderr,
            )
    routes = _collect_routes(parsed)
    services: list[ServiceDay] = sorted(parsed.keys())  # type: ignore[assignment]

    index = Index(stops=stops, routes=routes, services=services)
    _write_json(DOCS_API / "index.json", index.model_dump(mode="json"))

    effective_dates = {sd["effective_date"] for sd in parsed.values()}  # type: ignore[index]
    if len(effective_dates) != 1:
        raise ValueError(
            f"effective_date mismatch across services: {sorted(effective_dates)}"
        )
    (effective_date,) = effective_dates

    meta = Meta(
        effective_date=effective_date,  # type: ignore[arg-type]
        last_updated=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        sources=[Source(**s) for s in sources],
        fair_use=FAIR_USE,
    )
    _write_json(DOCS_API / "meta.json", meta.model_dump(mode="json"))

    if STOPS_DIR.exists():
        for old in STOPS_DIR.glob("*.json"):
            old.unlink()

    for stop_ref in stops:
        departures = _build_departures(parsed, stop_ref.id)
        if not departures:
            # Filtered above; reaching here means a stop appears in trip['stops']
            # but every value is None — treat as a bug, not data we should write.
            raise ValueError(f"stop {stop_ref.id!r} has zero departures")
        stop = Stop(stop=stop_ref, departures=departures)
        _write_json(
            STOPS_DIR / f"{stop_ref.id}.json",
            stop.model_dump(mode="json", exclude_none=True),
        )


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 2:
        print("usage: generate.py <parsed.json> <sources.json>", file=sys.stderr)
        return 2
    parsed = json.loads(Path(argv[0]).read_text())
    sources = json.loads(Path(argv[1]).read_text())
    generate(parsed, sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
