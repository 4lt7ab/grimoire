# Glossary

**Mount / mount directory.** The on-disk directory `grimoire-cli` operates over.
Holds everything the CLI produces for a single grimoire: `<mount>/grimoire.db`
(the SQLite file) and `<mount>/models/` (the embedder model cache). Specified
as `--mount <dir>` or `GRIMOIRE_MOUNT`. The library does not see a mount —
callers pass the SQLite path directly to `Grimoire.init` / `Grimoire.open`.
