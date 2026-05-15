# Rail Link API

Static JSON API for Hudson Rail Link bus schedule data, hosted on GitHub
Pages and refreshed weekly by a GitHub Action that parses the official MTA
PDF timetable.

## Endpoints

Base URL: `https://mgaspari.github.io/rail-link-api/api/`

| Path | Description |
| --- | --- |
| `meta.json` | Effective date, last updated, source PDFs (url + sha256), fair-use note |
| `index.json` | List of stops `{id,name}`, routes, services (`weekday` / `saturday` / `sunday`) |
| `stops/<slug>.json` | Per-stop departures keyed by service: `{time,route,direction}` |

Times are `"HH:MM"` 24-hour. Directions are `"to-ny"` or `"to-spuyten-duyvil"`.
Route codes are single letters from the source PDF (L / J / K / M).

Every payload is validated against a [pydantic model](./scripts/schemas.py)
before being committed. Index files are capped at 50 KB and per-stop files
at 10 KB.

## Example: fetch()

```js
const base = "https://mgaspari.github.io/rail-link-api/api";
const index = await fetch(`${base}/index.json`).then(r => r.json());
const firstStop = index.stops[0];
const stop = await fetch(`${base}/stops/${firstStop.id}.json`).then(r => r.json());
console.log(stop.departures.weekday.slice(0, 5));
```

```python
import requests
base = "https://mgaspari.github.io/rail-link-api/api"
index = requests.get(f"{base}/index.json").json()
stop = requests.get(f"{base}/stops/{index['stops'][0]['id']}.json").json()
print(stop["departures"]["weekday"][:5])
```

Example `stops/spuyten-duyvil.json`:

```json
{
  "stop": {"id": "spuyten-duyvil", "name": "Spuyten Duyvil"},
  "departures": {
    "weekday": [
      {"time": "06:44", "route": "L", "direction": "to-ny"},
      {"time": "07:14", "route": "J", "direction": "to-ny"}
    ]
  }
}
```

## Fair use

This project republishes MTA bus schedule data for the Hudson Rail Link
service. The MTA is the authoritative source; this API is a convenience
mirror. Schedules may change without notice — always check `meta.json` for
`effective_date` and `last_updated` before relying on the data.

Source PDFs are downloaded directly from `mta.info` and their SHA-256
digests are recorded in `meta.json` for traceability.

## Mirroring

To host your own mirror:

1. Fork the repo.
2. In **Settings → Pages**, set the source to `main` branch, `/docs` folder.
3. Enable **Actions** and let the weekly `refresh` workflow run, or trigger
   it manually via **workflow_dispatch** from the Actions tab.
4. Pages will serve `docs/api/**` as the JSON API at
   `https://<your-user>.github.io/<repo>/api/...`.

The workflow runs on a weekly `schedule` plus `workflow_dispatch` only. It
will not commit if validation fails — instead it opens an issue with a
diff summary. Required token scopes: `contents:write` (commits) and
`issues:write` (failure issues).

## Supply chain

Allowed dependency origins are limited to the US, UK, EU, Japan,
Switzerland, and Australia. Disallowed-origin tokens that fail the build
when present in `DEPENDENCIES.md` (CI grep gate):

- `Russia`
- `Russian Federation`
- `Yandex`
- `Kaspersky`
- `VK`
- `Mail.ru`
- `Sberbank`
- `Rostelecom`

Maintainers or organisations in disallowed jurisdictions, and registries
or mirrors hosted there, are also excluded. Before adding a dep we check
its PyPI and GitHub maintainer + org; after install we walk `pip list`
transitives and re-check each one.

See [DEPENDENCIES.md](./DEPENDENCIES.md) for the audited list (3 direct +
12 transitive packages today).

## Local development

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Run the full pipeline locally (needs outbound HTTPS to `mta.info`):

```sh
python -m scripts.fetch '[{"url":"https://www.mta.info/document/118701","service_day":"weekday"}]'
python -m scripts.parse data/sources/weekday.pdf weekday > build/parsed.weekday.json
python -m scripts.generate build/parsed.json build/sources.json
python -m scripts.validate
```

## First-run handoff

To stand up the live API after cloning / forking:

1. **Enable Pages** in repo Settings → Pages → Source: `main` branch, folder
   `/docs`. The `docs/.nojekyll` marker is already committed so Pages serves
   `docs/api/**` verbatim.
2. **Enable Actions** in repo Settings → Actions → General → Allow all
   actions. The workflow only needs the default `GITHUB_TOKEN`.
3. **Merge** the `claude/bus-schedule-json-api-*` branch into `main`.
4. **Trigger the first refresh** from Actions → "refresh" → Run workflow.
   It will fetch the PDFs, parse, generate, validate, and commit
   `docs/api/**` + `data/sources/**`.
5. After the workflow's commit, Pages will publish the JSON within ~1 min.
   Smoke-test:
   ```sh
   curl -sf https://mgaspari.github.io/rail-link-api/api/index.json | jq .stops
   ```
