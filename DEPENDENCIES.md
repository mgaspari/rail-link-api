# Dependencies

Audited list of every direct and transitive dependency that lands in the
runtime environment used by `scripts/` and the `refresh` workflow.

See the supply-chain policy in [README.md](./README.md#supply-chain). The
CI dep-audit step greps this file for the disallowed-origin tokens listed
there and fails the build on any match.

**Audit procedure.** Before adding a dep, the maintainer and org are
checked on PyPI and GitHub. After install, `pip list` is walked for
transitives and each is re-checked. If a package traces to a disallowed
origin by maintainer, org, hosting, or funding, it is not installed; an
alternative is proposed.

**Result of this audit:** all 15 packages below resolve to maintainers in
allowed jurisdictions (US, UK, EU, Japan, Switzerland, Australia). No
disallowed-origin tokens appear in the maintainer metadata of the full
dependency tree, verified by walking `pip show` output for every package.

## Direct

| Package | Version | Maintainer | Country / Org | Notes |
| --- | --- | --- | --- | --- |
| `pdfplumber` | 0.11.4 | Jeremy Singer-Vine (`jsvine@gmail.com`) | USA, independent | github.com/jsvine/pdfplumber |
| `requests` | 2.32.3 | Kenneth Reitz + PSF requests team | USA, Python Software Foundation | github.com/psf/requests |
| `pydantic` | 2.9.2 | Samuel Colvin + Pydantic team | UK, Pydantic Services Inc. | github.com/pydantic/pydantic |

## Transitive

| Package | Version | Maintainer | Country / Org | Notes |
| --- | --- | --- | --- | --- |
| `pdfminer.six` | 20231228 | Yusuke Shinyama + Philippe Guglielmetti | Japan / Switzerland | github.com/pdfminer/pdfminer.six — required by `pdfplumber`; verified explicitly per policy |
| `pillow` | 12.2.0 | Jeffrey A. "Alex" Clark + Pillow contributors | USA, Pillow project | github.com/python-pillow/Pillow — required by `pdfplumber` |
| `pypdfium2` | 5.8.0 | pypdfium2-team (geisserml) | Germany, community | github.com/pypdfium2-team/pypdfium2 — required by `pdfplumber` |
| `cryptography` | 41.0.7 | Python Cryptographic Authority | USA, PyCA | github.com/pyca/cryptography — required by `pdfminer.six` |
| `charset-normalizer` | 3.4.6 | Ahmed R. TAHRI | France, independent | github.com/jawah/charset_normalizer — required by `requests`, `pdfminer.six` |
| `idna` | 3.11 | Kim Davies | Australia, independent | github.com/kjd/idna — required by `requests` |
| `urllib3` | 2.6.3 | Andrey Petrov + Seth Larson + urllib3 team | USA, Python Software Foundation | github.com/urllib3/urllib3 — required by `requests`. Maintainer is US-based via Shazow LLC; project is PSF-hosted |
| `certifi` | 2026.2.25 | Kenneth Reitz + PSF | USA, Python Software Foundation | github.com/certifi/python-certifi — required by `requests` |
| `pydantic-core` | 2.23.4 | Samuel Colvin (Pydantic) | UK, Pydantic Services Inc. | github.com/pydantic/pydantic-core — required by `pydantic` |
| `annotated-types` | 0.7.0 | Adrian Garcia Badaracco, Samuel Colvin, Zac Hatfield-Dodds | USA / UK / Australia | github.com/annotated-types/annotated-types — required by `pydantic` |
| `typing-extensions` | 4.15.0 | Guido van Rossum, Jukka Lehtosalo, Łukasz Langa, Michael Lee | USA / Poland / Finland, PSF typing-sig | github.com/python/typing_extensions — required by `pydantic`, `pydantic-core` |
| `cffi` | 2.0.0 | Armin Rigo, Maciej Fijalkowski | Switzerland / Poland, PyPy team | github.com/python-cffi/cffi — required transitively by `cryptography` |
| `pycparser` | 3.0 | Eli Bendersky | USA, independent | github.com/eliben/pycparser — required by `cffi` |

## Notes

- `pdfminer.six` is the only PDF parser in the tree; explicitly verified
  per policy. Maintainers based in Japan and Switzerland; hosted on the
  `pdfminer` GitHub org. Clean.
- `urllib3` historical co-maintainer is US-based (Shazow LLC); the project
  is part of the Python Software Foundation. Clean.
- Pydantic Services Inc. is UK-based; no funding/affiliation from a
  disallowed origin is published. Clean.
- Source registry: `pypi.org` only. No mirrors configured. No `extra-index-url`.

## Re-audit trigger

Re-run this audit (and update the tables) whenever:
- `requirements.txt` changes
- `pip list --format=freeze` diff shows new transitives after a fresh install
- A direct dep's maintainer/org changes upstream
