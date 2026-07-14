# Contributing to PhotoFlow

## Dev setup

```bash
pip install -r requirements-dev.txt
```

## Quality gates

```bash
ruff check .              # lint
ruff format .             # auto-format
mypy core utils ui_qt     # type-check
pytest -q                 # tests (Qt tests self-skip when headless)
```

CI (`.github/workflows/ci.yml`) runs these on every push / PR across
Python 3.10–3.12.

## Pre-commit hooks (recommended)

Create a file named `.pre-commit-config.yaml` in the project root with the
contents below, then install the hooks:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-merge-conflict
      - id: check-added-large-files
        args: [--maxkb=2048]
```

## Packaging

See `docs/BUILD.md` for running from source, installing as a package, and
building the frozen Windows executable.
