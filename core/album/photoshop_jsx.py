"""
Photoshop ExtendScript (.jsx) exporter for PhotoFlow albums.

Compiles a generated album's layout geometry — spread sizes, per-photo frame
coordinates (absolute pixels) and crop bounds (relative to the source) — into a
self-contained Photoshop builder script. The photographer runs it inside
Photoshop (File ▸ Scripts ▸ Browse…); it prompts for an output folder and, one
spread at a time (to stay within RAM), builds a layered ``.psd`` for each
spread with each photo placed, scaled, positioned, and **non-destructively
cropped** by a feathered reveal mask in the shape of its frame.

The layout data is embedded as a JSON payload inside the script. JSON is valid
JavaScript and ``json.dumps`` escapes backslashes and quotes, so Windows paths
like ``D:\\shoot\\a.jpg`` survive correctly into the ExtendScript string.

Public API:
- :func:`build_payload` — the JSON-able layout payload for a project.
- :func:`build_jsx` — the full ``.jsx`` script text.
- :func:`export_photoshop_jsx` — write ``photoshop_album.jsx`` to a folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

from utils.logger import get_logger

logger = get_logger(__name__)

PathLike = Union[str, Path]

JSX_FILENAME = "photoshop_album.jsx"
DEFAULT_DPI = 300
# Soft mask edge, in pixels, so placed photos blend at frame borders.
FEATHER_PX = 2.0

# The builder script. ``__ALBUM_JSON__`` is replaced with the JSON payload.
# Uses string replacement (not str.format) because ExtendScript is brace-heavy.
_JSX_TEMPLATE = r"""#target photoshop
// PhotoFlow -> Photoshop album builder (auto-generated). Do not edit by hand.
// Run via: File > Scripts > Browse...  You'll be asked for an output folder.
(function () {
    var ALBUM = __ALBUM_JSON__;
    var FEATHER_PX = __FEATHER_PX__;

    if (!ALBUM.spreads || ALBUM.spreads.length === 0) {
        alert("PhotoFlow: this album has no spreads to build.");
        return;
    }

    var outFolder = Folder.selectDialog("Select a folder to save the album PSD spreads");
    if (outFolder === null) {
        return;  // user cancelled
    }

    var prevUnits = app.preferences.rulerUnits;
    var prevDialogs = app.displayDialogs;
    app.preferences.rulerUnits = Units.PIXELS;
    app.displayDialogs = DialogModes.NO;

    var built = 0, failed = 0, errors = [];
    for (var s = 0; s < ALBUM.spreads.length; s++) {
        var spread = ALBUM.spreads[s];
        try {
            buildSpread(spread, outFolder);
            built++;
        } catch (e) {
            failed++;
            errors.push("Spread " + (spread.index + 1) + ": " + e);
            // Close any document left open by the failed spread so we don't leak RAM.
            try {
                while (app.documents.length > 0) {
                    app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
                }
            } catch (ignore) {}
        }
    }

    app.preferences.rulerUnits = prevUnits;
    app.displayDialogs = prevDialogs;

    var msg = "PhotoFlow album build complete.\nSpreads saved: " + built;
    if (failed > 0) {
        msg += "\nFailed: " + failed + "\n" + errors.join("\n");
    }
    alert(msg);

    // ------------------------------------------------------------------ //
    function buildSpread(spread, outFolder) {
        var name = "spread_" + pad(spread.index + 1);
        var doc = app.documents.add(
            spread.width, spread.height, ALBUM.dpi, name,
            NewDocumentMode.RGB, DocumentFill.WHITE
        );

        // Sort placements by z_index so background layers are placed first
        // and overlay frames are composited on top — matching the original design.
        var sorted = spread.placements.slice().sort(function(a, b) {
            return (a.zIndex || 0) - (b.zIndex || 0);
        });

        for (var i = 0; i < sorted.length; i++) {
            placePhoto(doc, sorted[i]);
        }
        var outFile = new File(outFolder.fsName + "/" + name + ".psd");
        var opts = new PhotoshopSaveOptions();
        opts.layers = true;
        opts.embedColorProfile = true;
        doc.saveAs(outFile, opts, true, Extension.LOWERCASE);
        doc.close(SaveOptions.DONOTSAVECHANGES);
    }

    function placePhoto(doc, p) {
        var f = new File(p.path);
        if (!f.exists) { return; }

        // Open the source, copy its full pixels, then close it (RAM-friendly).
        var src = app.open(f);
        try {
            if (src.mode != DocumentMode.RGB) { src.changeMode(ChangeMode.RGB); }
        } catch (e) {}
        src.flatten();
        var W = src.width.as("px");
        var H = src.height.as("px");
        src.selection.selectAll();
        src.activeLayer.copy();
        src.close(SaveOptions.DONOTSAVECHANGES);

        app.activeDocument = doc;
        var layer = doc.paste();   // pasted centered; full source pixels (non-destructive)

        // Scale so the relative crop region maps onto the frame, then position
        // so the crop's top-left lands at the frame's top-left.
        var denomW = p.cropW * W;
        if (denomW <= 0) { denomW = W; }
        var scale = (p.frameW / denomW) * 100.0;
        layer.resize(scale, scale, AnchorPosition.TOPLEFT);

        var b = layer.bounds;
        var curX = b[0].as("px");
        var curY = b[1].as("px");
        var sf = scale / 100.0;
        var targetX = p.frameX - (p.cropX * W * sf);
        var targetY = p.frameY - (p.cropY * H * sf);
        layer.translate(targetX - curX, targetY - curY);

        // Non-destructive crop: reveal only the frame rectangle via a mask.
        doc.selection.select([
            [p.frameX, p.frameY],
            [p.frameX + p.frameW, p.frameY],
            [p.frameX + p.frameW, p.frameY + p.frameH],
            [p.frameX, p.frameY + p.frameH]
        ]);
        if (FEATHER_PX > 0) {
            try { doc.selection.feather(FEATHER_PX); } catch (e) {}
        }
        addRevealSelectionMask();
        doc.selection.deselect();

        // Overlay layers (z_index > 0) get a Photoshop drop shadow layer effect
        // so they visually "float" off the background layer beneath them.
        if (p.zIndex && p.zIndex > 0) {
            applyDropShadow(layer);
        }
    }

    function addRevealSelectionMask() {
        var d = new ActionDescriptor();
        var r = new ActionReference();
        r.putClass(charIDToTypeID("Chnl"));
        d.putReference(charIDToTypeID("null"), r);
        d.putEnumerated(charIDToTypeID("At  "), charIDToTypeID("Chnl"), charIDToTypeID("Msk "));
        d.putEnumerated(charIDToTypeID("Usng"), charIDToTypeID("UsrM"), charIDToTypeID("RvlS"));
        executeAction(charIDToTypeID("Mk  "), d, DialogModes.NO);
    }

    // Apply a professional drop shadow layer style to an overlay frame.
    // Shadow: soft, offset 10px down-right, 60% opacity — classic album look.
    function applyDropShadow(layer) {
        try {
            var d = new ActionDescriptor();
            var fx = new ActionDescriptor();
            var shadow = new ActionDescriptor();
            shadow.putBoolean(stringIDToTypeID("enabled"), true);
            shadow.putDouble(stringIDToTypeID("opacity"), 60.0);
            shadow.putDouble(stringIDToTypeID("localLightingAngle"), 120.0);
            shadow.putDouble(stringIDToTypeID("distance"), 10.0);
            shadow.putDouble(stringIDToTypeID("chokeMatte"), 0.0);
            shadow.putDouble(stringIDToTypeID("blur"), 18.0);
            fx.putObject(stringIDToTypeID("dropShadow"), stringIDToTypeID("dropShadow"), shadow);
            d.putObject(stringIDToTypeID("layerEffects"), stringIDToTypeID("layerEffects"), fx);
            var ref = new ActionReference();
            ref.putEnumerated(charIDToTypeID("Lyr "), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
            d.putReference(charIDToTypeID("null"), ref);
            executeAction(stringIDToTypeID("set"), d, DialogModes.NO);
        } catch (e) { /* drop shadow is cosmetic, never abort on failure */ }
    }

    function pad(n) { return (n < 10) ? ("0" + n) : ("" + n); }
})();
"""


def build_payload(project: Any) -> dict[str, Any]:
    """
    Build the JSON-able layout payload from an :class:`AlbumProject`.

    Returns ``{"dpi", "spreads": [{"index","width","height","placements":[
    {"path","frameX","frameY","frameW","frameH","cropX","cropY","cropW","cropH","zIndex"}
    ]}]}`` with absolute pixel frames, relative crop bounds, and z-index
    stacking order for overlay layers.
    """
    spec = getattr(project.meta, "album_spec", None) or {}
    dpi = int(spec.get("dpi", DEFAULT_DPI))

    spreads_out: list[dict[str, Any]] = []
    for spread in project.spreads:
        # Sort placements by z_index so the JSX script can place them in order.
        placements_sorted = sorted(
            spread.placements,
            key=lambda p: int(p.get("z_index", 0)),
        )
        placements_out: list[dict[str, Any]] = []
        for placement in placements_sorted:
            fx, fy, fw, fh = placement["frame_px"]
            cx, cy, cw, ch = placement["crop"]
            placements_out.append(
                {
                    "path": placement["path"],
                    "frameX": int(fx),
                    "frameY": int(fy),
                    "frameW": int(fw),
                    "frameH": int(fh),
                    "cropX": float(cx),
                    "cropY": float(cy),
                    "cropW": float(cw),
                    "cropH": float(ch),
                    # zIndex drives layer stacking in the Photoshop JSX builder.
                    # 0 = background layer, >0 = overlay frame (gets drop shadow).
                    "zIndex": int(placement.get("z_index", 0)),
                }
            )
        spreads_out.append(
            {
                "index": int(spread.index),
                "width": int(spread.width_px),
                "height": int(spread.height_px),
                "placements": placements_out,
            }
        )
    return {"dpi": dpi, "spreads": spreads_out}


def build_jsx(project: Any) -> str:
    """Return the full ``.jsx`` builder script text for ``project``."""
    payload_json = json.dumps(build_payload(project), indent=2)
    return _JSX_TEMPLATE.replace("__ALBUM_JSON__", payload_json).replace(
        "__FEATHER_PX__", repr(float(FEATHER_PX))
    )


def export_photoshop_jsx(out_dir: PathLike, project: Any) -> Path:
    """
    Write ``photoshop_album.jsx`` into ``out_dir`` and return its path.

    If ``out_dir`` is a directory (or has no suffix) the standard filename is
    used; otherwise ``out_dir`` is treated as the target file path.
    """
    out = Path(out_dir)
    if out.is_dir() or out.suffix == "":
        out = out / JSX_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_jsx(project), encoding="utf-8")
    logger.info(
        "Wrote Photoshop builder script (%d spread(s)) to '%s'.",
        len(project.spreads),
        out,
    )
    return out
