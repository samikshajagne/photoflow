"""
Shared pytest fixtures for the PhotoFlow test suite.

The main job here is removing an accidental dependency on whether MediaPipe
happens to be installed on the machine running the tests. See
:func:`zero_face_detector` for the details -- several pipeline tests were
silently testing a *failure-fallback* code path instead of the behaviour they
described, purely because MediaPipe was missing.
"""

from __future__ import annotations

import pytest

from core.face_detector import FaceResult


class StubFaceDetector:
    """
    A face detector that always *succeeds* and reports zero faces.

    This is what a real, working MediaPipe detector does on the synthetic
    fixtures the pipeline tests use (checkerboards, flat colour fields): it
    runs fine and simply finds nothing. That is very different from the
    detector being absent, which the pipeline treats as a *failure* -- and
    when detection fails for **every** image, ``PhotoFlowPipeline`` bypasses
    face scoring entirely ("Face sub-score is EXCLUDED ... so BestShots
    selection is not arbitrarily emptied"). That bypass changes which
    category a photo lands in, so tests asserting on categories/BestShots
    would pass or fail depending only on whether MediaPipe was installed.

    Injecting this stub makes those tests deterministic everywhere and means
    they exercise the intended path (a genuinely faceless photo) rather than
    the all-detection-failed fallback.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def detect(self, image_path) -> FaceResult:
        path = str(image_path)
        self.calls.append(path)
        return FaceResult(
            image_path=path,
            face_count=0,
            faces_detected=False,
            regions=(),
        )


@pytest.fixture
def zero_face_detector() -> StubFaceDetector:
    """A working detector that finds no faces (see :class:`StubFaceDetector`)."""
    return StubFaceDetector()
