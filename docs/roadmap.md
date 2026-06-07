# Roadmap

**TL;DR:** No active feature direction — current focus is hardening the test
suite and removing maintenance friction so the project stays cheap to own.

---

## Milestone: Automate the dev loop

Nothing runs without a human today. Make correctness checks automatic.

- [x] Add `pytest-cov` and surface a coverage number
- [ ] Wire a type-checker (mypy or pyright) into the loop to back the strict-typing rule
- [ ] Keep CI fully offline — no embedder weight fetch, no network (honors the no-network-tests rule)

## Milestone: Close test-coverage gaps

Documented contracts that currently have no test.

- [x] `SchemaVersionError` — the version-mismatch / recreate-the-file path is untested
- [x] `limit` is a positive int across all reads — `None`/unbounded dropped, `<= 0` rejected
- [x] `check_same_thread=False` cross-thread use
- [x] Opt-in, marked integration test against the real `FastembedEmbedder` (skipped by default, network-gated)
- [ ] Set a coverage floor once `pytest-cov` lands

## Milestone: Keep examples from rotting

The four runnable examples have no safety net.

- [ ] Smoke-run each `examples/<name>/app.py` in CI so a broken example fails the build
