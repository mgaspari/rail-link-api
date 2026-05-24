"""Validate generated JSON against pydantic schemas and invariant gates.

Run after ``scripts.generate`` and before committing. Exits non-zero
(and prints a one-line reason per failure) when any of the following hold:

- A JSON payload fails its pydantic schema.
- ``docs/api/index.json`` is larger than 50 KB.
- Any ``docs/api/stops/<slug>.json`` is larger than 10 KB.
- The new ``index.json`` has fewer stops than the previous version
  (detected via ``git show HEAD:docs/api/index.json``; absent on first
  generation, in which case the check is skipped).
- Any stop has zero departures across all services.
- ``meta.effective_date`` is not a valid ISO date.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from scripts.schemas import Index, Meta, Stop

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_API = REPO_ROOT / "docs" / "api"
STOPS_DIR = DOCS_API / "stops"

INDEX_LIMIT_BYTES = 50 * 1024
STOP_LIMIT_BYTES = 10 * 1024


def _previous_index_stop_count() -> int | None:
    try:
        rel = (DOCS_API / "index.json").relative_to(REPO_ROOT)
    except ValueError:
        return None
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{rel.as_posix()}"],
            check=True,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    try:
        data = json.loads(result.stdout)
        return len(data.get("stops", []))
    except json.JSONDecodeError:
        return None


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def _check(path: Path, limit: int, errors: list[str]) -> None:
    size = path.stat().st_size
    if size > limit:
        errors.append(f"{_display(path)} is {size}B > {limit}B")


def validate() -> list[str]:
    errors: list[str] = []

    index_path = DOCS_API / "index.json"
    meta_path = DOCS_API / "meta.json"

    if not index_path.exists():
        return [f"missing {_display(index_path)}"]
    if not meta_path.exists():
        return [f"missing {_display(meta_path)}"]

    try:
        index = Index.model_validate_json(index_path.read_text())
    except ValidationError as e:
        errors.append(f"index.json schema: {e.errors(include_url=False)}")
        return errors

    try:
        meta = Meta.model_validate_json(meta_path.read_text())
    except ValidationError as e:
        errors.append(f"meta.json schema: {e.errors(include_url=False)}")

    _check(index_path, INDEX_LIMIT_BYTES, errors)
    _check(meta_path, INDEX_LIMIT_BYTES, errors)

    if not index.stops:
        errors.append("index.json has zero stops")
    if not index.routes:
        errors.append("index.json has zero routes")

    prev_count = _previous_index_stop_count()
    if prev_count is not None and len(index.stops) < prev_count:
        errors.append(
            f"stop count regressed: {prev_count} → {len(index.stops)}"
        )

    seen_slugs = {s.id for s in index.stops}
    on_disk = {p.stem for p in STOPS_DIR.glob("*.json")}
    missing = seen_slugs - on_disk
    extra = on_disk - seen_slugs
    if missing:
        errors.append(f"stops in index but not on disk: {sorted(missing)}")
    if extra:
        errors.append(f"stop files on disk but not in index: {sorted(extra)}")

    for stop_path in sorted(STOPS_DIR.glob("*.json")):
        try:
            stop = Stop.model_validate_json(stop_path.read_text())
        except ValidationError as e:
            errors.append(f"{stop_path.name} schema: {e.errors(include_url=False)}")
            continue
        total = sum(len(deps) for deps in stop.departures.values())
        if total == 0:
            errors.append(f"{stop_path.name} has zero departures")
        _check(stop_path, STOP_LIMIT_BYTES, errors)

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("validation ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
