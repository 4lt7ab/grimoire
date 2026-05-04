# Docs discipline

**When this applies:** Any time you are working in this repo.

- Do not modify files in `docs/`, `README.md`, or `CLAUDE.md` unless the user explicitly asks you to. Documentation drift caused by well-meaning edits is a bigger problem than slightly-stale docs.
- Cross-references between docs should be exceedingly rare. `CLAUDE.md` is the aggregator — it points into `docs/` and `.claude/rules/`. Individual docs do not link to each other in a web. If you find yourself wanting to add such a link, the content probably belongs in one place, not two.
