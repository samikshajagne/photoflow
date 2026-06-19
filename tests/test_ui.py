"""
Unit tests for the UI layer.

Streamlit's rendering can't easily run under pytest, so these tests cover the
pure, side-effect-free helpers in ui.components.folder_utils plus an import
smoke test of the app module (which must import without launching Streamlit).
"""

from pathlib import Path

import pytest

from ui.components.folder_utils import (
    FolderError,
    file_manager_command,
    reveal_folder,
    validate_input_folder,
    validate_output_folder,
)


# --------------------------------------------------------------------------- #
# file_manager_command
# --------------------------------------------------------------------------- #
def test_command_for_macos():
    assert file_manager_command("Darwin", "/x/y") == ["open", "/x/y"]


def test_command_for_linux():
    assert file_manager_command("Linux", "/x/y") == ["xdg-open", "/x/y"]


def test_command_for_windows_is_none():
    # Windows uses os.startfile, signalled by a None command.
    assert file_manager_command("Windows", r"C:\x\y") is None


# --------------------------------------------------------------------------- #
# Path validation
# --------------------------------------------------------------------------- #
def test_validate_input_folder_accepts_existing_dir(tmp_path: Path):
    ok, msg = validate_input_folder(str(tmp_path))
    assert ok is True
    assert msg == ""


def test_validate_input_folder_rejects_empty():
    ok, msg = validate_input_folder("   ")
    assert ok is False
    assert msg


def test_validate_input_folder_rejects_missing(tmp_path: Path):
    ok, msg = validate_input_folder(str(tmp_path / "nope"))
    assert ok is False


def test_validate_input_folder_rejects_file(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    ok, msg = validate_input_folder(str(f))
    assert ok is False


def test_validate_output_folder_allows_nonexistent(tmp_path: Path):
    # Output folder may not exist yet; PhotoFlow creates it.
    ok, msg = validate_output_folder(str(tmp_path / "new_out"))
    assert ok is True


def test_validate_output_folder_rejects_empty():
    ok, _ = validate_output_folder("")
    assert ok is False


def test_validate_output_folder_rejects_existing_file(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    ok, _ = validate_output_folder(str(f))
    assert ok is False


# --------------------------------------------------------------------------- #
# reveal_folder
# --------------------------------------------------------------------------- #
def test_reveal_folder_missing_raises(tmp_path: Path):
    with pytest.raises(FolderError):
        reveal_folder(tmp_path / "does_not_exist")


def test_reveal_folder_on_file_raises(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(FolderError):
        reveal_folder(f)


def test_reveal_folder_invokes_launcher(tmp_path: Path, monkeypatch):
    # Don't actually open a window: stub the OS launchers.
    calls = {}
    monkeypatch.setattr("ui.components.folder_utils.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "ui.components.folder_utils.subprocess.Popen",
        lambda cmd, *a, **k: calls.setdefault("cmd", cmd),
    )
    reveal_folder(tmp_path)
    assert calls["cmd"] == ["xdg-open", str(tmp_path)]


# --------------------------------------------------------------------------- #
# App import smoke test
# --------------------------------------------------------------------------- #
def test_app_module_imports_without_launching():
    # Importing must not execute main() (guarded by __main__).
    import ui.app as app

    assert hasattr(app, "main")
    assert app.STAGE_SETUP == "setup"


# --------------------------------------------------------------------------- #
# End-to-end wizard flow via Streamlit's AppTest harness
# --------------------------------------------------------------------------- #
def _make_sample_photos(folder: Path) -> None:
    import cv2
    import numpy as np

    folder.mkdir(parents=True, exist_ok=True)
    n, sq = 128, 8
    rows = [
        np.repeat([(0 if (r + c) % 2 else 255) for c in range(n // sq)], sq)
        for r in range(n // sq)
    ]
    checker = np.repeat(np.array(rows, np.uint8), sq, 0)[:n, :n]
    cv2.imwrite(str(folder / "a.png"), checker)
    (folder / "b_copy.png").write_bytes((folder / "a.png").read_bytes())
    cv2.imwrite(str(folder / "z_blurry.png"), np.tile(np.linspace(0, 255, n, dtype=np.uint8), (n, 1)))


def test_app_wizard_setup_to_results(tmp_path: Path):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

    photos = tmp_path / "photos"
    _make_sample_photos(photos)

    at = AppTest.from_file("ui/app.py", default_timeout=60).run()
    assert not at.exception
    assert "PhotoFlow" in [t.value for t in at.title]
    assert len(at.text_input) == 2  # input + output folder

    at.text_input[0].set_value(str(photos))
    at.text_input[1].set_value(str(tmp_path / "out"))
    at.button[0].click().run()  # setup -> running
    at = at.run()               # running -> results

    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Best shots"] == "1"
    assert metrics["Duplicates"] == "1"
    assert metrics["Blurry"] == "1"
    assert metrics["Review"] == "0"
    labels = [b.label for b in at.button]
    assert any("Open Output Folder" in b for b in labels)
    assert any("Open BestShots Folder" in b for b in labels)
