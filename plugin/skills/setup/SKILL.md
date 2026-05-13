---
name: setup
description: Run a short conversational grimoire quickstart — silently verify the CLI install, default mount, and MCP server registration; orient the user on six common usage patterns (knowledge base, idempotency record, semantic memory, browsable log, decision log with precedent retrieval, translation memory); help them pick one via `AskUserQuestion`; then run a real end-to-end demo of the chosen pattern via the `mcp__grimoire__*` tools so they leave with grimoire actually working. Finishes with a single copy-pastable bash line that deletes the demo data. **The conversation is the deliverable** — no project files are written. Use whenever the user asks to set up, install, configure, get started with, or invokes `/grimoire:setup`.
---

# Grimoire quickstart

A short conversational walkthrough that gets a new user oriented on grimoire and through a real first entry. **The conversation is the deliverable.** No project files are written, no rules baked in, no `CLAUDE.md` edits, no shell-profile changes.

By the end:

- The user has seen six ways people use grimoire, and the one they picked has been demonstrated end-to-end with actual MCP calls.
- They have a copy-pastable bash line that deletes every demo entry created.
- They know what tools to reach for next when they use grimoire for real.

Grimoire is versatile — entries are pure metadata, indexes are independent, payloads are arbitrary JSON. The same datastore can be a knowledge base, a dedup cache, a vector memory, a chronological journal, a precedent retriever, or a translation memory. The walkthrough's job is to make that surface concrete by demoing one shape.

## Steps

### 1. Silent infrastructure check

Run in parallel and stay quiet if everything is fine:

- `command -v grimoire` — CLI installed?
- `test -f ~/.grimoire/grimoire.db` — default mount initialized?
- Search `<cwd>/.mcp.json`, `~/.claude.json`, `~/.claude/settings.json`, `<cwd>/.claude/settings.json` for a `grimoire` MCP server entry — registered with Claude Code?

If all three pass, proceed silently to step 2.

**CLI missing.** Detect installers in parallel: `command -v uv` / `command -v pipx`. AskUserQuestion with whichever are available plus "I'll install it myself later". On a chosen installer, run it and re-check. On "later", end the walkthrough cleanly.

**Mount missing.** AskUserQuestion: **Create now** (recommended) or **Skip**. On create, run `grimoire mount create` and report. On skip, end cleanly — there's nothing to demo against.

**MCP server not registered.** Print the registration snippet below and ask the user to add it to `~/.claude.json` (or `<cwd>/.mcp.json` for project scope), then restart Claude Code. Do **not** auto-edit any config file. End the walkthrough — the demo cannot run without the server.

```json
{
  "mcpServers": {
    "grimoire": {
      "type": "stdio",
      "command": "grimoire",
      "args": ["mcp", "serve"]
    }
  }
}
```

### 2. Orient — show the patterns

Send a single message that introduces grimoire in one short paragraph, then describes six common usage patterns. Keep it scannable. For each pattern, give a name in bold, a one-line "what this shape looks like", a one-line "when this fits", and a tiny pseudo-call hint.

Use this template verbatim — adapt only the framing paragraph if needed for the user's stated context:

> Grimoire is a single-file SQLite + sqlite-vec datastore. Entries hold metadata; keyword (FTS5) and semantic (vec0) indexing are independent, opt-in operations against the same entry id. That separation lets one datastore bend to a lot of shapes — here are six ways people use it:
>
> 1. **Knowledge base.** Broad kinds, both indexes, structured payload. *Fits:* "find similar past notes by topic or keyword." `entry_add → index_keyword + index_semantic → search_*`.
> 2. **Idempotency record.** `(group_key, group_ref)` enforced unique, payload-only, no indexes. *Fits:* "have I seen / processed this URL, file, or event before?" `entry_add` for the first sighting, then `fetch(group_refs=[...])` to check next time.
> 3. **Semantic memory.** Semantic-indexed only, lightweight payload. *Fits:* "an agent needs to remember context across turns or sessions." `entry_add → index_semantic → search_semantic`.
> 4. **Browsable log.** Payload-only, no indexes, walked chronologically with ULID cursoring. *Fits:* "append-only journal; I'll browse it, not search it." `entry_add → fetch(limit=..., cursor=...)`.
> 5. **Decision log with precedent retrieval.** Each entry is a past decision; rationale prose is semantic-indexed. *Fits:* "when a new tradeoff comes up, find similar decisions we've made before." `entry_add → index_semantic → search_semantic(query=<new tradeoff>)`.
> 6. **Translation memory.** Each entry is a source/target pair; source text is semantic-indexed. *Fits:* "I want to match the style of my past good translations." `entry_add → index_semantic(source) → search_semantic(query=<new sentence>)`.
>
> Pick the one closest to what you want — I'll run through a real first entry so you can see it work.

### 3. Pick a pattern

AskUserQuestion (single-select). Bundle the four common patterns plus an escape lane:

- **Knowledge base**
- **Idempotency record**
- **Semantic memory**
- **Show me the less common ones**

If the user picks "Show me the less common ones", second AskUserQuestion:

- **Browsable log**
- **Decision log**
- **Translation memory**
- **Skip the demo — I'll design my own**

On "Skip the demo", end the walkthrough cleanly. The orientation alone was the value; they're ready to design from here.

### 4. Demo the chosen pattern end-to-end via MCP

Tell the user in one sentence what you're about to do: how many entries you'll add, which indexes you'll touch, which search you'll demonstrate.

Then run the demo using the **MCP tools** (`mcp__grimoire__entry_add`, `mcp__grimoire__index_keyword`, etc.) — **not** the CLI. After each tool call, give the user a one-line update ("created entry `<id>`", "indexed for semantic search", "search returned N hits with top distance X").

**Always use `group_key="quickstart-demo"`** on every entry the demo creates. That makes them findable by `fetch(group_keys=["quickstart-demo"])` later if the user loses the cleanup command.

**Pattern recipes.** Adapt the example content if the user has hinted at a specific domain; otherwise the neutral seeds below are fine.

#### Knowledge base — 1 entry, both indexes

- `entry_add(group_key="quickstart-demo", payload={"title": "Phoenix mythology", "tags": ["mythology", "fire"]}, context="quickstart demo entry")`
- `index_keyword(entry_id=<id>, text="phoenix mythology rebirth fire bird")`
- `index_semantic(entry_id=<id>, text="The phoenix is a mythological bird that cyclically regenerates from its own ashes.")`
- `search_semantic(query="bird that returns from death")` — show the hit with its `distance`.
- `search_keyword(query="phoenix")` — show the hit with its `rank`.

#### Idempotency record — 2 entries + a deliberate collision

- `entry_add(group_key="quickstart-demo", group_ref="https://example.com/article-42", payload={"title": "Article 42", "processed_at": "<now>"})`
- `entry_add(group_key="quickstart-demo", group_ref="https://example.com/article-99", payload={"title": "Article 99", "processed_at": "<now>"})`
- Re-attempt: `entry_add(group_key="quickstart-demo", group_ref="https://example.com/article-42", payload={})` — show that this raises. Tell the user this is the whole point: the unique constraint is the dedup primitive.
- Look up by ref: `fetch(group_keys=["quickstart-demo"], group_refs=["https://example.com/article-42"])` — show the existing record being found.

#### Semantic memory — 3 entries, semantic-only

- Three short prose entries that an agent might remember (e.g. user preferences, observed patterns).
- `entry_add(group_key="quickstart-demo", payload={"summary": "<short>"})` then `index_semantic(entry_id=<id>, text="<longer prose>")` for each.
- `search_semantic(query="<near-paraphrase of one of them>")` — show the matching memory surface with its distance.

#### Browsable log — 3 payload-only entries, then a walk

- Three `entry_add(group_key="quickstart-demo", payload={"event": "<...>", "level": "info", "ts": "<iso>"})` with **no** index calls.
- `fetch(group_keys=["quickstart-demo"], limit=10)` — show the chronological order and call out that the id IS the cursor (ULIDs sort by creation time).

#### Decision log with precedent retrieval — 3 entries, semantic-indexed on rationale

- Three past-decision entries: `entry_add(group_key="quickstart-demo", payload={"decision": "<short>", "date": "<iso>"})` with a short rationale.
- `index_semantic(entry_id=<id>, text=<rationale prose>)` for each.
- `search_semantic(query=<new tradeoff that echoes one of the past decisions>)` — show the precedent surfacing with its distance.

#### Translation memory — 3 source/target pairs, semantic-indexed on source

- Three short pairs (e.g. English source → Spanish target).
- `entry_add(group_key="quickstart-demo", payload={"source": "<en>", "target": "<es>"})` for each.
- `index_semantic(entry_id=<id>, text=<source>)` for each.
- `search_semantic(query=<a near-paraphrase of one source in English>)` — show the matching pair surface, payload includes the canonical target.

### 5. Cleanup command

Collect the entry ids returned by every `entry_add` during the demo. Print **one single bash line** that deletes them, chained with `&&`. **No comments anywhere in the bash** — comments break some shells when piped or pasted partially.

For one entry:

```sh
grimoire entry delete <ID> --yes
```

For multiple (most patterns):

```sh
grimoire entry delete <ID1> --yes && grimoire entry delete <ID2> --yes && grimoire entry delete <ID3> --yes
```

After the block, tell the user in plain English: "This deletes only the entries the demo created — every entry with `group_key='quickstart-demo'`. Your other entries in `~/.grimoire/grimoire.db` are untouched." If they lose the line, they can find demo entries any time via `grimoire fetch --group-key quickstart-demo`.

### 6. Cap-off

One short paragraph closing the walkthrough:

- Name the pattern they picked and the 2-3 tools they'd reach for next when using it for real.
- Mention `mcp__grimoire__info()` for checking current state at any time.
- Note that grimoire is local-first — nothing left the machine.

No file is written. The conversation ends.

## Guardrails

- **Never** write project files. No `.claude/rules/grimoire.md`, no edits to `CLAUDE.md`, no shell-profile changes, no `~/.claude/CLAUDE.md` cheatsheet. The skill is purely conversational; persistence is the user's own follow-up.
- **Never** auto-edit `~/.claude.json` or any MCP-server config. If the server isn't registered, print the snippet and end the walkthrough cleanly.
- **Never** put comments (`#`) in any bash command surfaced to the user. The cleanup line is a single chained `&&` invocation with no `#` anywhere.
- **Demo via MCP, not CLI.** Every read/write/index/search during the demo goes through `mcp__grimoire__*`. The CLI is referenced only in the cleanup command and the "if you need to set something up" infra-fix steps.
- **Label every demo entry** with `group_key="quickstart-demo"`. The user must be able to find and delete demo data even without the printed cleanup line.
- **Always print the cleanup command after the demo**, even if the user said they want to keep the entries. Their future self will want it.
- **Honor disengagement.** If the user wants to skip the demo after orientation, the orient-and-pick phase was the value. End cleanly without grinding through more questions.
- **Use `AskUserQuestion` at every real fork** — install method, mount creation, pattern pick. Free-text prompts are reserved for cases where the user has to supply a shape that doesn't fit a menu.
