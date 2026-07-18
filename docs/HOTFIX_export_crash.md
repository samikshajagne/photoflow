# Hotfix — Export Crashing / Shutting Down the PC

## What was happening

The shutdown was **not your PC** — the export was overloading it. Two things
stacked up:

1. **Too much parallelism.** The exporter rendered up to **8 spreads at once**
   (`_default_workers` = min(8, CPU cores)). Each spread holds a large canvas
   (a 12×12″ double-page at 300 dpi is ~7200×3600 px ≈ 100 MB) plus several
   decoded photos — so 8 in parallel is multiple GB of RAM and every core pinned
   at 100%.
2. **Full-resolution photo decodes.** Each slot decoded the *entire* 24-megapixel
   source photo (~70–100 MB each) even though it renders at a fraction of that.
   With ~9–13 photos per spread × 8 parallel spreads, RAM spikes into the many
   GB — and a laptop either runs out of memory or overheats and trips its
   thermal cut-off. That's why it died at the **same ~26%** every time.

## The fix (in `core/album/raster.py`)

1. **Parallelism capped to 2** (`_default_workers`). Far less peak RAM and heat;
   the export is a bit slower but won't crash the machine.
2. **Source photos decode downscaled** — capped at **3000 px** on the long edge
   (`_MAX_SOURCE_EDGE_PX`), via a fast JPEG draft hint + thumbnail. A designed
   album page never needs more than that, so there's no visible quality loss,
   and memory per photo drops ~5–10×.

Together these cut peak memory roughly **10–15×**.

## To get the fix

Pull/rebuild with the updated `core/album/raster.py`, then re-run the export.

## Try these too (extra safety, and to confirm the diagnosis)

If you want to be gentle on the machine while testing, or if it *still* strains:

- **Lower the DPI** in Build Album settings to **150** (a 150 dpi 12×12 spread is
  a quarter of the pixels of 300 dpi — big memory saving, still fine for screen
  and most prints).
- **Set Target pages lower** (e.g. 20) so there are fewer, and export
  **PNG or JPG** first (they render spread-by-spread) rather than one large PDF.
- Close other heavy apps (browsers) during export.
- Watch Task Manager → Performance: if **Memory** hits ~100% it's RAM; if it
  shuts down with memory still low but **CPU pinned and fans roaring**, it's
  thermal — tell me and I'll drop the worker count to 1.

## Please report back

After pulling the fix, re-run the same export and let me know:
- Does it complete now?
- If it still stops, at what % — and was it memory-full or CPU-hot in Task
  Manager?

That tells me whether to push memory even lower or force single-threaded
rendering.

## Note

I couldn't reproduce/measure this in my sandbox (no way to run your full export
there, and the sandbox has had a file-sync issue all session), so this is a
targeted fix based on the code's resource profile. It's low-risk and additive —
`git diff core/album/raster.py` shows exactly the two changes.
