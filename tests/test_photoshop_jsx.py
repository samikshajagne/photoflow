"""
Unit tests for core.album.photoshop_jsx.

Builds a small album project with real Spread/Placement geometry and verifies
the generated .jsx: the file is written, the embedded JSON payload matches the
spreads (absolute pixel frames + relative crops), and Windows backslash paths
are escaped for the JavaScript engine.
"""

import json
import re

from core.album.photoshop_jsx import (
    JSX_FILENAME,
    build_jsx,
    build_payload,
    export_photoshop_jsx,
)
from core.album.project import AlbumProject, SpreadRecord


def _project(spreads):
    project = AlbumProject.new("/shoot", album_spec={"dpi": 300, "page_width_in": 12})
    project.spreads = spreads
    project.export.manifest_path = "/out/album_manifest.json"
    return project


def _spreads():
    # Mirrors what the orchestrator stores: SpreadRecord with dict placements
    # carrying absolute pixel frames and relative crop bounds.
    placements = [
        {"path": r"D:\shoot\a.jpg", "frame_px": [0, 0, 100, 200], "crop": [0.1, 0.0, 0.8, 1.0]},
        {"path": "/unix/b.jpg", "frame_px": [100, 0, 100, 200], "crop": [0.0, 0.0, 1.0, 1.0]},
    ]
    return [
        SpreadRecord(index=0, section="Cover", width_px=7200, height_px=3600,
                     placements=placements)
    ]


def _extract_album_json(jsx: str) -> dict:
    """Pull the `var ALBUM = {...};` object back out of the script."""
    m = re.search(r"var ALBUM = (\{.*?\});\s*\n\s*var FEATHER_PX", jsx, re.DOTALL)
    assert m, "ALBUM payload not found in JSX"
    return json.loads(m.group(1))


# --------------------------------------------------------------------------- #
# Payload correctness
# --------------------------------------------------------------------------- #
def test_payload_matches_spreads():
    payload = build_payload(_project(_spreads()))
    assert payload["dpi"] == 300
    assert len(payload["spreads"]) == 1

    spread = payload["spreads"][0]
    assert (spread["width"], spread["height"]) == (7200, 3600)
    assert spread["index"] == 0

    a = spread["placements"][0]
    # Absolute pixel frame preserved.
    assert (a["frameX"], a["frameY"], a["frameW"], a["frameH"]) == (0, 0, 100, 200)
    # Relative crop preserved.
    assert (a["cropX"], a["cropW"]) == (0.1, 0.8)
    assert a["path"] == r"D:\shoot\a.jpg"


def test_jsx_embeds_matching_payload():
    project = _project(_spreads())
    jsx = build_jsx(project)
    embedded = _extract_album_json(jsx)
    assert embedded == build_payload(project)


def test_windows_backslashes_are_escaped_for_js():
    jsx = build_jsx(_project(_spreads()))
    # The raw .jsx text must contain an escaped path (\\), not a lone backslash,
    # so the ExtendScript string parser reads it correctly.
    assert r"D:\\shoot\\a.jpg" in jsx
    assert r"D:\shoot\a.jpg" not in jsx.replace(r"\\", "")  # no unescaped form
    # And it round-trips back to the real Windows path via JSON.
    assert _extract_album_json(jsx)["spreads"][0]["placements"][0]["path"] == r"D:\shoot\a.jpg"


def test_jsx_has_builder_essentials():
    jsx = build_jsx(_project(_spreads()))
    assert "Folder.selectDialog" in jsx          # prompts for output folder
    assert "app.documents.add" in jsx            # creates a doc per spread
    assert "PhotoshopSaveOptions" in jsx         # saves layered PSD
    assert "feather" in jsx                      # feathered mask
    assert "#target photoshop" in jsx


# --------------------------------------------------------------------------- #
# File export
# --------------------------------------------------------------------------- #
def test_export_writes_file(tmp_path):
    out = export_photoshop_jsx(tmp_path, _project(_spreads()))
    assert out.name == JSX_FILENAME
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert _extract_album_json(text)["spreads"][0]["width"] == 7200


def test_export_handles_empty_album(tmp_path):
    project = _project([])
    out = export_photoshop_jsx(tmp_path, project)
    payload = _extract_album_json(out.read_text(encoding="utf-8"))
    assert payload["spreads"] == []
