# Contributing

Contributions are welcome, particularly where they make LogLathe more modular,
auditable, or interoperable.

## Development setup

LogLathe supports Python 3.11 through 3.14 and uses
[uv](https://docs.astral.sh/uv/) for dependency management.

Run the same checks used in CI:

```console
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```

Enable commit-time checks after cloning:

```console
uv run pre-commit install
uv run pre-commit run --all-files
```

GitHub Actions runs quality checks and tests on Python 3.11, 3.12, 3.13, and 3.14.

## Pull requests

Keep each pull request focused on one purpose and use a title that states the
outcome. In the description, use one to three summary bullets, list the exact
checks and results, and add reviewer notes only for material risks, tradeoffs,
migrations, or areas that need close attention.

Before requesting review, read the diff and verify every claim in the summary and
validation section. This applies equally to human- and agent-drafted descriptions;
do not include an agent transcript or an implementation diary.

## Useful contribution areas

- adapters for other normalization targets, including OCSF;
- semantic-mapping techniques not represented by the current baselines;
- integrations with review tools, data platforms, and downstream validation;
- provider-neutral cases, metrics, and leakage-resistant dataset splits; and
- improvements that separate general workflow contracts from ASIM-specific types
  without weakening provenance or human approval boundaries.

Open an issue before starting a substantial architectural change so the relevant
contract boundary can be agreed first. See the [current architecture](docs/architecture.md)
and [roadmap](ROADMAP.md) before proposing changes.

## Data and security

Evaluation fixtures and examples must be synthetic or suitably sanitized. Do not
commit operational logs, credentials, customer data, annotation state, generated
artifacts, or other sensitive telemetry.

Raw logs are untrusted input. New providers or integrations must keep operational
authority outside the semantic-suggestion boundary, use strict output contracts,
preserve provenance, and retain deterministic validation and human approval.

## Documentation

Update the canonical task guide when behavior changes. Avoid copying commands or
current counts into several pages; link to the canonical guide instead. Active
documents should state their audience, status, canonical purpose, and verification
date.
