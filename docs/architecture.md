# Architecture

**TL;DR:** A core Python library wraps SQLite, sqlite-vec, and FTS5 to provide a polymorphic datastore with both vector and keyword search. A CLI sits on top as the first consumer.

**When to read this:** Before making any change that crosses a component boundary.

---

## Components

- **`grimoire`** — The core library. Owns query access, schema migrations, and the domain objects callers work with. All reads and writes pass through it.
- **`grimoire-cli`** — A management tool for operating on a grimoire datastore (bulk operations, maintenance). Treated as the library's first user — its needs shape the public API.

## How they connect

The CLI imports the library directly. There is no IPC, RPC, or daemon — both run in the same process when invoked, and they share the underlying SQLite file on disk.

## External dependencies

- **SQLite** — the underlying datastore.
- **sqlite-vec** — vector index extension for semantic search.
- **SQLite FTS5** — full-text index for keyword search. Bundled with SQLite, not a separate package.
- **Embedding model/provider** — produces the vectors that sqlite-vec indexes.

## Data flow

Data enters and exits exclusively through the library. Records are indexed against grimoire's schema as they are written, and queries return domain objects on the way back out. The CLI uses this same path for bulk import/export operations.
