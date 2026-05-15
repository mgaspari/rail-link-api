"""Download Hudson Rail Link source PDFs into data/sources/ and record sha256.

Inputs are a list of ``{url, service_day}`` dicts. For each source we write
two files into ``data/sources/``:

- ``<service_day>.pdf`` — the raw PDF bytes
- ``<service_day>.sha256`` — hex digest of the PDF

We compare the new digest to the previous one (if any) and report whether
the source changed. Results are emitted as JSON to stdout and, when
``GITHUB_OUTPUT`` is set, written as a ``sources<<EOF`` heredoc so the
workflow can branch on ``changed``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO_ROOT / "data" / "sources"

DEFAULT_SOURCES: list[dict[str, str]] = [
    {
        "url": "https://www.mta.info/document/118701",
        "service_day": "weekday",
    },
]

USER_AGENT = "rail-link-api/0.1 (+https://github.com/mgaspari/rail-link-api)"
TIMEOUT_SECONDS = 30


@dataclass
class FetchResult:
    service_day: str
    url: str
    path: str
    sha256: str
    changed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "service_day": self.service_day,
            "url": self.url,
            "path": self.path,
            "sha256": self.sha256,
            "changed": self.changed,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def fetch_one(source: dict[str, str], session: requests.Session) -> FetchResult:
    url = source["url"]
    service_day = source["service_day"]

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = SOURCES_DIR / f"{service_day}.pdf"
    sha_path = SOURCES_DIR / f"{service_day}.sha256"

    response = session.get(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "pdf" not in content_type.lower() and not response.content.startswith(b"%PDF"):
        raise RuntimeError(
            f"{url} did not return a PDF (content-type={content_type!r})"
        )

    new_hash = _sha256_bytes(response.content)
    old_hash = _read_text(sha_path)
    changed = new_hash != old_hash

    pdf_path.write_bytes(response.content)
    sha_path.write_text(new_hash + "\n")

    return FetchResult(
        service_day=service_day,
        url=url,
        path=str(pdf_path.relative_to(REPO_ROOT)),
        sha256=new_hash,
        changed=changed,
    )


def fetch_all(sources: list[dict[str, str]] | None = None) -> list[FetchResult]:
    sources = sources if sources is not None else DEFAULT_SOURCES
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return [fetch_one(s, session) for s in sources]


def _emit_github_output(results: list[FetchResult]) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return
    payload = json.dumps([r.to_dict() for r in results])
    any_changed = "true" if any(r.changed for r in results) else "false"
    with open(out_path, "a", encoding="utf-8") as fh:
        fh.write(f"any_changed={any_changed}\n")
        fh.write("sources<<EOF\n")
        fh.write(payload + "\n")
        fh.write("EOF\n")


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    sources = DEFAULT_SOURCES
    if argv:
        sources = json.loads(argv[0])

    results = fetch_all(sources)
    print(json.dumps([r.to_dict() for r in results], indent=2))
    _emit_github_output(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
