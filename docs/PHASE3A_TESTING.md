# Phase 3a — Testing Guide (Template Schema + Programmatic Renderer)

The start of the design engine. This checkpoint adds a **declarative template
system** — shaped photo slots, borders, drop shadows, and sampled backgrounds —
plus a programmatic renderer that composites real photos into a template. No art
assets required; it runs today.

This is the foundation that later checkpoints build on (wiring it into album
generation, cutouts, event colour themes, text overlays).

## What changed (files)

- `core/album/template.py` — **new**. The whole engine:
  - `TemplateSlot` (relative rect + shape + border + shadow + rotation + fit),
    `Background` (solid or sampled-from-photos), `SpreadTemplate` (named, themed
    set of slots). All round-trip to/from JSON.
  - `render_spread(template, image_paths, spec)` — Pillow renderer supporting
    **rect / rounded / circle / oval / diamond** slots, borders, drop shadows,
    and solid/sampled backgrounds.
  - `default_templates()` (built-in `classic` theme, 1–4 photos),
    `load_templates(dir)`, `select_template(...)`, `auto_grid_template(...)`.
- `data/templates/classic/*.json` — **new**. The `classic` theme shipped as JSON
  (the "author templates as data" model).
- `tests/test_album_template.py` — **new**. 13 tests.

## What it does NOT do yet

- It is **not wired into album generation** yet — building an album in the app
  still uses the old rectangular layout. Wiring the renderer into the album/
  export pipeline is the next checkpoint (3b), along with per-event colour
  themes (3c). This keeps the change isolated and safe to review.

---

## Verification — run the tests (these run anywhere)

```powershell
pytest -q tests/test_album_template.py
```

**Expected:** 13 passed. They cover schema validation, JSON round-tripping, the
built-in library + selection (including fallback to an auto grid for counts with
no exact template), and that the renderer composites shaped slots onto a spread
of the correct size.

I ran this suite here and it passed (13/13) — this module is pure Pillow/NumPy,
so unlike the GUI code it could be executed directly.

## See it render (optional, ~10 lines)

You can render a spread yourself from any folder of images:

```python
from core.album.layout import AlbumSpec
from core.album.template import default_templates, select_template, render_spread

spec = AlbumSpec(12, 12, 150)                 # 12x12in double-spread @150dpi
tpl = select_template(default_templates(), 4, "classic")
imgs = ["p1.jpg", "p2.jpg", "p3.jpg", "p4.jpg"]   # any four images
render_spread(tpl, imgs, spec).save("spread_demo.png")
```

Open `spread_demo.png`: you should see a hero rectangle, a circular slot, a
rounded-rectangle slot, and a diamond slot — each with a white border and a soft
drop shadow — over a background tinted from the photos' colours.

I've attached three sample renders (2-, 3-, and 4-photo `classic` templates) in
the chat so you can see the output. The busy interiors are only because I used
your already-composed album pages as stand-in fill photos; with raw photos each
slot holds one image.

---

## What to look for / give feedback on

Now is a good time to react to the **look** so 3b builds the right thing:

- Slot shapes and arrangements (do the 1–4 photo templates feel right?).
- Border thickness/colour and shadow strength.
- Background tint (how light the sampled colour should be).

Tell me what to adjust and I'll tune the `classic` theme (and add more themes)
before wiring it into the album build.

## Rollback

All changes are uncommitted and additive (a new module, new JSON, new test
file). Delete `core/album/template.py`, `data/templates/`, and
`tests/test_album_template.py` to fully revert; nothing else was modified.
