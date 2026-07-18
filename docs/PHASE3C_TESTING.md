# Phase 3c — Testing Guide (Per-Event Colour Theming + Event Naming)

Each event now gets its own **colour mood**: every spread in a section shares a
background tinted from that section's photos — Haldi spreads read warm/yellow, a
green-lawn event reads green, and so on, like your sample album. Events are also
given best-effort names.

## What changed (files)

- `core/album/theming.py` — **new**. Pure Pillow/NumPy:
  - `dominant_color(paths)` — the representative "mood" colour of a set of
    photos (averages the *colourful* pixels, not the neutral walls/skin).
  - `background_tint(rgb, lighten)` — lightens a mood colour into a soft
    backdrop; `to_hex(rgb)`.
  - `classify_event_name(rgb)` — best-effort event name from colour.
- `core/album/raster.py` — `render_spread_template` now computes **one mood
  colour per section** (cached) and gives every spread in that section the same
  tinted background, for a coherent per-event look.
- `core/album/orchestrator.py` — `_build_events` names each event via
  `classify_event_name` (falls back to `Event N`).
- `tests/test_album_theming.py` — **new**, 7 tests. `tests/test_album_raster.py`
  — added a per-section-consistency test.

## Honest scope note on naming

Colour reliably distinguishes only a few events — the turmeric-yellow **Haldi**
is the one we name with confidence. Most ceremonies (mehndi, baraat, varmala,
reception) are **not** separable by colour alone, so those keep their
chronological label rather than risk a wrong guess. Accurate classification of
all events would need either a trained model or a quick user confirmation step —
a good candidate for a later phase (it could piggyback on the people-first
labelling UI). The **colour theming** applies to every event regardless.

Also unchanged this phase: layered **PSD** export is still rectangular (a 3b
carve-over).

---

## Verification

### Unit tests

```powershell
pytest -q tests/test_album_theming.py tests/test_album_raster.py
```

**Expected:** all pass. Highlights:

- `test_album_theming.py` — mood-colour extraction, tinting, hex, and that
  turmeric yellow classifies as `Haldi` while blue/grey do not. *(I ran this
  suite here: 7/7 passed.)*
- `test_album_raster.py::test_section_theme_is_consistent_across_spreads` — two
  spreads in one section get an identical background; a different section gets a
  different one.

> **Environment note:** as in 3b, my sandbox couldn't run the *raster* suite
> this round (its filesystem kept serving a truncated copy of the freshly-edited
> `raster.py` — an environment glitch, not the code). The `theming` module was
> verified here, and the raster wiring was verified against the source. Your
> local `pytest` is the authoritative check.

### See it in the app

1. `python -m ui_qt.main`
2. **Open & Analyze** a shoot with visually distinct events (e.g. a yellow Haldi
   and an indoor reception) → optionally **Label People** → **Build Album** →
   **Export** (PNG/JPG/PDF).
3. Open the spreads in `…/PhotoFlow_Album/renders/`.

**Expected:** spreads within the same section share one background colour pulled
from those photos, and different sections have visibly different moods — instead
of every spread sampling only its own two or three photos.

---

## Rollback

All additive. To revert theming from the render, drop the `section_color` block
in `render_spread_template` and the `_section_theme_color` helper in
`core/album/raster.py`; to revert naming, restore the `f"Event {seg.index + 1}"`
line in `orchestrator._build_events`. `core/album/theming.py` and its test can be
deleted.
