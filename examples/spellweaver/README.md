# Spellweaver

An experimental combat REPL. Showcases **`entry_vec`** used as a runtime *dispatcher* rather than as a search box.

You face a goblin warlock. Each turn you type a free-form incantation — *"I call lightning to my fingertips"*, *"let the world hold still"* — and grimoire semantically matches your prose to the closest spell in its repertoire. The top hit is the spell you cast; the runner-up is shown as *"what you almost cast,"* so the language becomes the wand. If your phrasing drifts too far from any known spell, the incantation fizzles.

## The interesting interaction

Most demos point semantic search at a corpus you want to *retrieve*. Here it dispatches an *action*: the same query surface that powers a search box becomes a fuzzy lookup of named operations. Useful far beyond games — natural-language command palettes, intent routing in chat UIs, "did you mean…?" tool-call resolution.

Two things fall out of this for free:

- **Distance is a confidence score.** The game uses a `FIZZLE_THRESHOLD` (default `0.95`) — anything above is too vague to cast. Tune this knob to taste; higher = generous, lower = strict.
- **The runner-up is a tutor.** Showing the player which spell they *almost* cast turns each turn into a hint about how to phrase next time.

## Run

```sh
# Interactive — recommended.
uv run examples/spellweaver/app.py

# Scripted demo (no stdin needed; runs a fixed scenario).
uv run examples/spellweaver/app.py --demo
```

First run downloads the default embedder (~30 MB) into `.grimoire/__models__/`. Same model cache as `ai-journal` — feel free to symlink to share weights if you've already run that one.

## Game

- **You:** 50 HP, 100 mana.
- **Goblin warlock:** 60 HP, hits for 6–12 per turn.
- Win by reducing the goblin to 0 HP. Lose at 0 HP yourself.
- Type `quit` (or send EOF) to leave the duel.

The grimoire knows 12 spells across damage, healing, stuns, life-drain, time-stops, and reveals. The full list is the `SPELLS` constant in [`app.py`](app.py) — read it after your first duel, not before, to keep the matching honest.

## What this example does not do

It's a one-file demo of a single Grimoire facet — combat is intentionally thin. There's no inventory, no levels, no save-state, no second enemy type. The point is the dispatcher pattern; everything else is just stakes around it.
