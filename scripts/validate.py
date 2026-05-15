"""Validate generated JSON against pydantic schemas and invariant gates.

Filled in at build-order step 7. Gates:
- schema fails  → exit non-zero
- stop count drops vs previous index.json
- any stop has zero departures
- effective_date unparseable
- stop JSON > 10KB; index.json > 50KB
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError("scripts/validate.py — implemented at step 7")


if __name__ == "__main__":
    raise SystemExit(main())
