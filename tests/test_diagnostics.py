"""
Tests for core.diagnostics.

The report is meant to be pasted into a support email and forwarded around, and
it states in its own footer that no photos, file names or client details are
included. These tests exist to make that statement *true* rather than
aspirational — the log tail it embeds is full of the customer's client work by
default, so redaction is the whole point.
"""

from __future__ import annotations

import re

from core.diagnostics import collect, scrub


# --------------------------------------------------------------------------- #
# Photo paths — the sensitive case, because the log records every file processed
# --------------------------------------------------------------------------- #
def test_windows_photo_path_is_redacted():
    line = r"Blur analysis 'D:\startup\test\test-200\IMG_6173.JPG': score=164.71"
    out = scrub(line)
    assert "IMG_6173" not in out
    assert "test-200" not in out
    assert "<photo.jpg>" in out
    assert "score=164.71" in out, "diagnostic detail must survive redaction"


def test_unix_photo_path_is_redacted():
    out = scrub("Quality '/home/priya/Sangeet_Day2/DSC_0042.NEF': ok")
    assert "DSC_0042" not in out
    assert "Sangeet_Day2" not in out
    assert "<photo.nef>" in out


def test_client_folder_with_spaces_is_redacted():
    """Client folders are routinely named like "Priya & Arjun", and a naive
    pattern stops at the first space and leaks the rest."""
    out = scrub(r"Exporting to D:\Clients\Priya & Arjun\album.pdf")
    assert "Priya" not in out
    assert "Arjun" not in out
    assert "<path>" in out


def test_every_image_extension_is_covered():
    for ext in ("jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp",
                "psd", "heic", "cr2", "nef", "arw", "dng"):
        out = scrub(rf"processing 'D:\Wedding\ClientName\shot_001.{ext}'")
        assert "ClientName" not in out, ext
        assert "shot_001" not in out, ext


def test_extension_case_is_normalised_but_detail_kept():
    assert "<photo.jpg>" in scrub(r"'C:\A\B\X.JPG'")


# --------------------------------------------------------------------------- #
# Account name
# --------------------------------------------------------------------------- #
def test_windows_account_name_is_replaced():
    out = scrub(r"C:\Users\yashj\AppData\Local\Samiksha Technologies\PhotoFlow")
    assert "yashj" not in out
    assert "<user>" in out
    # Our own directory stays readable — it's useful and not private.
    assert "Samiksha Technologies" in out


def test_mac_and_linux_home_directories_are_replaced():
    assert "<user>" in scrub("/Users/someone/Library/Application Support")
    assert "<user>" in scrub("/home/someone/.local/share")
    assert "someone" not in scrub("/home/someone/.local/share")


def test_scrub_is_idempotent():
    once = scrub(r"'D:\A\B\photo.jpg' and C:\Users\bob\thing")
    assert scrub(once) == once, "re-scrubbing must not mangle placeholders"


def test_scrub_leaves_ordinary_text_alone():
    text = "Face detection FAILED for ALL 3 image(s). Check MediaPipe install."
    assert scrub(text) == text


# --------------------------------------------------------------------------- #
# The assembled report
# --------------------------------------------------------------------------- #
def test_report_contains_the_useful_sections():
    report = collect(include_log=False)
    for heading in ("Application", "System", "Optional components", "Licence", "Paths"):
        assert heading in report, heading
    assert "version" in report


def test_report_states_its_own_privacy_promise():
    assert "No photos, file names or client details" in collect(include_log=False)


def test_report_never_contains_a_full_licence_key(tmp_path, monkeypatch):
    """Only the last few characters, so a support email can't be used to
    activate someone else's copy."""
    from core import licensing
    from core.licensing import LicenseManager, LicenseState, save_state

    path = tmp_path / "license.json"
    save_state(
        LicenseState(
            first_run="2026-01-01", key="PHOTOFLOW-SECRET-KEY-999999",
            activated_on="2026-01-02", last_validated="2026-08-05",
        ),
        path,
    )
    monkeypatch.setattr(licensing, "state_path", lambda: path)

    report = collect(include_log=False)
    assert "PHOTOFLOW-SECRET-KEY-999999" not in report
    assert "SECRET" not in report


def test_report_survives_a_missing_log_file():
    # include_log=True with no readable log must not raise.
    assert "PhotoFlow diagnostics" in collect(include_log=True)


def test_report_with_log_has_no_photo_filenames():
    """End-to-end: whatever is in the real log, the report must not carry
    recognisable image filenames out of the machine."""
    report = collect(include_log=True)
    # Strip our own <photo.jpg>/<path> placeholders first, otherwise the search
    # matches the replacement text it is meant to be verifying.
    without_placeholders = re.sub(r"<[^>\s]*>", "", report)
    leaked = re.findall(
        r"[^\s\"'<>]+\.(?:jpe?g|png|nef|cr2|psd|tiff?)\b", without_placeholders, re.I
    )
    assert leaked == [], f"unredacted image paths in the report: {leaked[:5]}"


def test_report_reports_optional_component_availability():
    report = collect(include_log=False)
    for package in ("mediapipe", "insightface", "rembg"):
        assert package in report, package
