# Coding conventions

**TL;DR:** Python 3.12+, formatted and linted with `ruff`. Short PEP 8 names, dataclasses for structures, exceptions for control flow. Pre-v1 schema is not migrated in place.

**When to read this:** Before writing or reviewing code in this repo.

---

## Language and tooling

- **Primary language:** Python (`>=3.12`)
- **Formatter:** `ruff format`
- **Linter:** `ruff check` with `E, F, I, B, UP, SIM` rule sets enabled (see top-level `pyproject.toml`)
- **Workspace:** uv workspace; `packages/*` are members.

## File and folder organization

Monorepo layout under `packages/<name>/...`. Each package owns its own source tree (`src/<module>/`), tests (`tests/`), and `pyproject.toml`. Two packages today: `grimoire` (library) and `grimoire-cli` (CLI + MCP server).

## Naming

Short names, PEP 8 conventions throughout. The library's primary surface is the `Grimoire` class; lifecycle entry points are the `Grimoire.open(...)` and `Grimoire.peek(...)` staticmethods.

## Data structures

Use `@dataclass(frozen=True, slots=True)` for plain data structures (`Entry`, `EntryIndex`, `Filters`, `KeywordHit`, `SemanticHit`, `Peek`, `Mount`). Avoid hand-rolled `__init__` boilerplate.

## Error handling

Errors propagate as exceptions and are expected to bubble all the way out to the caller. Each package defines its custom exceptions in an `errors.py` module, and every custom exception carries a meaningful description so the caller has actionable context. Inheritance is acceptable in `errors.py` to share a common base — elsewhere, prefer composition.

## Testing

Tests live under `packages/<name>/tests/` and are collected from both packages by the root `pyproject.toml`. Run with `uv run pytest`. Tests use `tmp_path` and a lightweight `fake_embedder` fixture rather than the real fastembed model.

## Schema policy

Pre-v1, schema changes are **not** migrated in place. `SCHEMA_VERSION` stays put across schema changes — no bumps as tripwires, no migration runners, no in-place upgrades. A `SCHEMA_VERSION` mismatch raises `SchemaVersionError`; the response is to recreate the file. Migration ergonomics get designed once v1 is on the table.
