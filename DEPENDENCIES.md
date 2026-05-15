# Dependencies

Audited list of every direct and transitive dependency. Populated at
build-order **step 2** before any `pip install` runs.

Policy: no Russian-origin code/deps/services (transitive). Disallowed names
(grepped by CI): `Russia`, `Russian Federation`, `Yandex`, `Kaspersky`, `VK`,
`Mail.ru`, `Sberbank`, `Rostelecom`. Maintainers/orgs in Russia and Russian
registries/mirrors are also disallowed.

## Direct

| Package | Version | Maintainer | Country / Org | Notes |
| --- | --- | --- | --- | --- |
| _pending step 2_ | | | | |

## Transitive

| Package | Version | Maintainer | Country / Org | Notes |
| --- | --- | --- | --- | --- |
| _pending step 2_ | | | | |

## Notes

- `pdfminer.six` is a transitive of `pdfplumber` and must be verified
  explicitly during the step-2 audit.
