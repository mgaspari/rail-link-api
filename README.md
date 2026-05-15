# Rail Link API

Static JSON API for Hudson Rail Link bus schedule data, hosted on GitHub Pages
and refreshed weekly by a GitHub Action that parses the official MTA PDF
timetable.

> **Status:** Skeleton — endpoints and data are populated by the `refresh`
> workflow. See [Build Order](#build-order) below.

## Endpoints

Base URL: `https://<owner>.github.io/rail-link-api/api/`

| Path | Description |
| --- | --- |
| `meta.json` | Effective date, last updated, source PDFs (url + sha256), fair-use note |
| `index.json` | List of stops `{id,name}`, routes, services (`weekday` / `saturday` / `sunday`) |
| `stops/<slug>.json` | Per-stop departures keyed by service: `{time,route,direction}` |

Times are `"HH:MM"` 24-hour. Directions are `"to-ny"` or `"to-spuyten-duyvil"`.
Route codes are single letters from the source PDF (L/J/K/M).

## Example: fetch()

```js
const base = "https://<owner>.github.io/rail-link-api/api";
const index = await fetch(`${base}/index.json`).then(r => r.json());
const firstStop = index.stops[0];
const stop = await fetch(`${base}/stops/${firstStop.id}.json`).then(r => r.json());
console.log(stop.departures.weekday.slice(0, 5));
```

```python
import requests
base = "https://<owner>.github.io/rail-link-api/api"
index = requests.get(f"{base}/index.json").json()
stop = requests.get(f"{base}/stops/{index['stops'][0]['id']}.json").json()
print(stop["departures"]["weekday"][:5])
```

## Fair use

This project republishes MTA bus schedule data for the Hudson Rail Link
service. The MTA is the authoritative source; this API is a convenience
mirror. Schedules may change without notice — always check `meta.json` for
the `effective_date` and `last_updated` before relying on the data.

Source PDFs are downloaded directly from `mta.info` and their SHA-256
digests are recorded in `meta.json` for traceability.

## Mirroring

To host your own mirror:

1. Fork the repo.
2. In **Settings → Pages**, set the source to `main` branch, `/docs` folder.
3. Enable **Actions** and let the weekly `refresh` workflow run, or trigger
   it manually via **workflow_dispatch**.
4. Pages will serve `docs/api/**` as the JSON API.

The workflow has `permissions: contents:write` only and runs on a weekly
`schedule` plus `workflow_dispatch`. It will not commit if validation fails
— instead it opens an issue with a diff summary.

## Supply chain

See [DEPENDENCIES.md](./DEPENDENCIES.md) for the audited list of direct and
transitive dependencies. Russian-origin code, deps, services, maintainers,
registries and mirrors are disallowed; a CI gate fails the build if any
appear in `DEPENDENCIES.md`.

## Local development

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch.py
python scripts/parse.py
python scripts/generate.py
python scripts/validate.py
pytest
```

## Build order

1. Skeleton + `requirements.txt` + README outline
2. `DEPENDENCIES.md` audit for planned direct + transitive deps
3. `scripts/fetch.py`: download sources, sha256 diff, set `GITHUB_OUTPUT`
4. `scripts/schemas.py`: pydantic models
5. `scripts/parse.py` against the live PDF
6. `scripts/generate.py`: writes `index.json`, `meta.json`, per-stop JSON
7. `scripts/validate.py`: schema + invariant gates
8. `tests/test_parse.py`: snapshot vs `tests/fixtures/118701.pdf`
9. `.github/workflows/refresh.yml`: cron + dispatch + concurrency + timeout + dep-audit + issue-on-failure
10. Enable Pages, smoke-test URLs, finish README
