# Coding conventions

**TL;DR:** Python, formatted and linted with `ruff`. Short PEP 8 names, dataclasses for structures, exceptions for control flow.

**When to read this:** Before writing or reviewing code in this repo.

---

## Language and tooling

- **Primary language:** Python
- **Formatter:** `ruff format`
- **Linter:** `ruff check`

## File and folder organization

Monorepo layout under `packages/<name>/...`. Each package owns its own source tree.

## Naming

Short names, PEP 8 conventions throughout. `Grimoire()` is the public entrypoint to the library — callers go through it.

## Data structures

Use `@dataclass` for plain data structures. Avoid hand-rolled `__init__` boilerplate.

## Error handling

Errors propagate as exceptions and are expected to bubble all the way out to the caller. Each package defines its custom exceptions in an `errors.py` module, and every custom exception carries a meaningful description so the caller has actionable context. Inheritance is acceptable in `errors.py` to share structure across exception types — elsewhere, prefer composition.
