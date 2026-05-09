---
name: setup
description: Run the grimoire setup walkthrough — verify the `grimoire` CLI is installed, interview the user on which mount location to use (global, project-local, or custom) and initialize it, then offer to drop a generic grimoire CLI cheat sheet into the user's `~/.claude/CLAUDE.md`. Use whenever the user asks to set up, install, configure, or initialize grimoire for Claude Code, or invokes `/grimoire:setup`.
---

# Grimoire setup

A small, idempotent walkthrough that gets grimoire wired into Claude Code. The skill **interviews** the user before doing anything destructive or persistent — it never assumes the default is the right choice. Re-running on an already-configured machine is a no-op.

## Steps

Work through the steps in order. After each one, briefly tell the user what you found and what (if anything) you did. Skip a step entirely if its check passes.

### 1. Verify the CLI is installed

Run `command -v grimoire` via Bash.

- **Installed:** report the path and move on.
- **Missing:** detect available installers in parallel (`command -v uv`, `command -v pipx`). Then ask the user how they want to install it via `AskUserQuestion`, with options drawn from what's actually available:
  - `uv tool install '4lt7ab-grimoire-cli[fastembed]'` (recommended if `uv` is present)
  - `pipx install '4lt7ab-grimoire-cli[fastembed]'` (if `pipx` is present)
  - "I'll install it myself later" (skip, and end the walkthrough cleanly)

  If neither `uv` nor `pipx` is on PATH, surface that and stop — direct the user to install one of them first.

  On a chosen installer, run the command and confirm the binary lands on PATH (re-run `command -v grimoire`).

### 2. Choose and initialize the mount

The mount location is the most important decision in this walkthrough — it is **not** a yes/no on the default. Interview the user properly.

**Step 2a — gather signal before asking.** In parallel, check:
- Is `$GRIMOIRE_MOUNT` set? If so, capture its value.
- Does `~/.grimoire/grimoire.db` exist?
- Does `./.grimoire/grimoire.db` exist (relative to the user's current working directory)?
- Is the current working directory inside a git repo? (`git rev-parse --show-toplevel`) — useful for naming a project-local option.

**Step 2b — pick the mount path via `AskUserQuestion`.** Frame it as a real choice, with options tailored to the signal above. The available choices, in order:

1. **Existing mount detected** — if `$GRIMOIRE_MOUNT` is set and points to an initialized mount, OR `~/.grimoire/grimoire.db` exists, OR `./.grimoire/grimoire.db` exists, list each as a "use existing" option (most-relevant first). Picking one of these short-circuits step 2c — the mount is already initialized.
2. **`~/.grimoire`** — global, recommended for users who want one shared datastore across all projects.
3. **Project-local** — `<git-repo-root>/.grimoire` if inside a repo, otherwise `<cwd>/.grimoire`. Recommended for users who want grimoire data to live and version with a specific project. Mention that the project-local path will not auto-resolve unless they `export GRIMOIRE_MOUNT=<path>` or pass `--mount` every time.
4. **Custom path** — accept a free-text follow-up. Expand `~` and resolve to absolute.
5. **Skip mount setup** — exit step 2 without touching anything.

Always describe each option in one short line so the user can scan and pick. Do not pre-tick any choice — let the user choose.

**Step 2c — initialize the chosen mount.** If the user picked a fresh path (not an existing mount), check whether `<chosen>/grimoire.db` exists:
- **Exists:** report it, move on.
- **Missing:** run `grimoire mount --mount <chosen>`. The command is idempotent — safe even if directory pieces exist.

**Step 2d — `GRIMOIRE_MOUNT` follow-up.** If the chosen mount is **not** `~/.grimoire` AND `$GRIMOIRE_MOUNT` is not already set to it, print (do not run) the export line they should add to their shell profile:

```sh
export GRIMOIRE_MOUNT=<chosen-path>
```

Tell the user this is a copy-paste step for them — the skill will not edit `~/.zshrc`, `~/.bashrc`, or any shell profile.

### 3. Drop the CLI cheat sheet into ~/.claude/CLAUDE.md

This is the headline deliverable: a generic CLI reference that lets future Claude Code sessions drive grimoire without rediscovering the surface.

The snippet lives next to this `SKILL.md` at `claude-md-snippet.md` (relative to the skill directory). It is wrapped in `<!-- BEGIN grimoire-cheatsheet -->` / `<!-- END grimoire-cheatsheet -->` markers so re-runs can detect and update it cleanly.

Procedure:

1. Read `claude-md-snippet.md` (adjacent to this file).
2. Read `~/.claude/CLAUDE.md`. If it does not exist, treat it as empty.
3. Search for the `<!-- BEGIN grimoire-cheatsheet -->` marker:
   - **Found:** ask the user whether to refresh it. On confirmation, replace everything between the BEGIN and END markers (inclusive) with the current snippet contents. On decline, leave it alone.
   - **Not found:** ask the user whether to add it. On confirmation, append the snippet to the end of `~/.claude/CLAUDE.md`, separated from existing content by a single blank line. If the file does not exist, create it with the snippet as its sole contents.
4. Tell the user the file path you wrote and the byte count or line count of the snippet.

### 4. Wrap up

Print a short summary: which steps ran, which were skipped, and any one-line follow-ups (e.g. "set `GRIMOIRE_MOUNT` in your shell profile if you want a non-default mount").

## Guardrails

- **Never** modify the user's shell profile (`~/.zshrc`, `~/.bashrc`, etc.) — only suggest the export line for them to add.
- **Never** delete an existing mount or run `grimoire mount destroy` from this skill, even if the user asks mid-walkthrough. Tell them to run it themselves.
- **Interview, don't assume.** When a step has a real fork (where the mount lives, which installer to use), ask the user with `AskUserQuestion` and let them pick — don't pre-select the default and rubber-stamp it. A short follow-up (e.g. capturing a custom path) is fine; back-to-back yes/no confirmations are not.
- **Confirm before every destructive or persistent action.** Installing a CLI, creating a mount, and editing `~/.claude/CLAUDE.md` all need explicit user assent.
- The cheat sheet is **generic by design**. Do not embellish it with project-specific kinds, opinionated workflows, or examples drawn from the user's own data. If the user wants tailored notes, they can add them outside the marker block.
