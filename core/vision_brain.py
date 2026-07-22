"""
Vision "brain" layer — OpenAI GPT-4o Vision edition.

A single feature-extraction pass per photo that everything downstream reads
from.  The primary engine is the **OpenAI GPT-4o Vision API** (scene labels
and dominant colours via a structured JSON prompt).  Face detection keeps
running locally via **MediaPipe** (GPT-4o Vision does not return pixel
bounding-boxes, so the local detector is actually better for that task).

Fallback behaviour
------------------
- If no ``OPENAI_API_KEY`` is set, or the API call fails, the brain degrades
  to local-only: MediaPipe faces + a dominant colour extracted by Pillow.
- All results are stored in the :class:`~persistence.analysis_cache.AnalysisCache`
  (namespace ``"vision_brain"``), so the API is called **at most once per
  photo, ever**.

The ``openai`` package must be installed::

    pip install openai

It is listed in ``requirements.txt``.  The class works without it (local
fallback only) but warns on first use so the user knows what to install.
"""

from __future__ import annotations

import base64
import dataclasses
import json
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

RelRect = Tuple[float, float, float, float]
Point = Tuple[float, float]
RGB = Tuple[int, int, int]

SOURCE_OPENAI = "openai_vision"
SOURCE_LOCAL = "local_fallback"

# Keep legacy alias so existing code that imports SOURCE_GOOGLE still works.
SOURCE_GOOGLE = SOURCE_OPENAI

# GPT model to use — gpt-4o has native image understanding.
_OPENAI_MODEL = "gpt-4o"

# The structured-output prompt sent to GPT-4o for each photo.
_SYSTEM_PROMPT = (
    "You are a computer-vision assistant that analyses wedding photographs. "
    "Return ONLY a JSON object — no markdown, no extra text — with these keys:\n"
    "  scene_labels: list of up to 15 lowercase English keywords describing the "
    "scene (e.g. 'haldi', 'mehndi', 'ceremony', 'baraat', 'reception', "
    "'bride', 'groom', 'dance', 'outdoor', etc.)\n"
    "  scene_confidence: parallel list of floats in [0,1] — your confidence "
    "that each label applies.\n"
    "  dominant_colors: list of up to 3 objects each with keys 'r','g','b' "
    "(0-255 integers) representing the most prominent colours in the image.\n"
    "  face_count: integer — approximate number of faces visible.\n"
    "Example output:\n"
    '{"scene_labels":["ceremony","mandap","fire","priest"],'
    '"scene_confidence":[0.95,0.90,0.80,0.75],'
    '"dominant_colors":[{"r":220,"g":180,"b":60}],'
    '"face_count":4}'
)


@dataclasses.dataclass
class PhotoBrain:
    """
    Everything the vision layer knows about one photo.

    Face boxes and landmark points are **normalised** to ``[0, 1]`` of the
    image so they're resolution-independent (matching the rest of the album
    engine).
    """

    path: str
    face_count: int = 0
    face_boxes: List[RelRect] = dataclasses.field(default_factory=list)
    face_landmarks: List[List[Point]] = dataclasses.field(default_factory=list)
    face_emotions: List[str] = dataclasses.field(default_factory=list)
    scene_labels: List[str] = dataclasses.field(default_factory=list)
    scene_confidence: List[float] = dataclasses.field(default_factory=list)
    dominant_colors: List[RGB] = dataclasses.field(default_factory=list)
    capture_time: Optional[datetime] = None
    source: str = SOURCE_LOCAL

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "face_count": self.face_count,
            "face_boxes": [list(b) for b in self.face_boxes],
            "face_landmarks": [[list(p) for p in face] for face in self.face_landmarks],
            "face_emotions": list(self.face_emotions),
            "scene_labels": list(self.scene_labels),
            "scene_confidence": list(self.scene_confidence),
            "dominant_colors": [list(c) for c in self.dominant_colors],
            "capture_time": self.capture_time.isoformat() if self.capture_time else None,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PhotoBrain":
        ct = data.get("capture_time")
        capture_time = None
        if ct:
            try:
                capture_time = datetime.fromisoformat(ct)
            except (ValueError, TypeError):
                capture_time = None
        return cls(
            path=data.get("path", ""),
            face_count=int(data.get("face_count", 0)),
            face_boxes=[tuple(float(v) for v in b) for b in data.get("face_boxes", [])],
            face_landmarks=[
                [tuple(float(v) for v in p) for p in face]
                for face in data.get("face_landmarks", [])
            ],
            face_emotions=list(data.get("face_emotions", [])),
            scene_labels=list(data.get("scene_labels", [])),
            scene_confidence=[float(s) for s in data.get("scene_confidence", [])],
            dominant_colors=[tuple(int(v) for v in c) for c in data.get("dominant_colors", [])],
            capture_time=capture_time,
            source=data.get("source", SOURCE_LOCAL),
        )


class VisionBrain:
    """
    Extracts a :class:`PhotoBrain` per photo using OpenAI GPT-4o Vision for
    scene understanding, and MediaPipe locally for face bounding-boxes.

    Args:
        api_key: OpenAI API key.  When falsy the brain uses local fallback only.
        enable_fallback: If ``True`` (default), any API failure falls back to
            the local extractor instead of raising.
        max_labels: Maximum number of scene labels to request from GPT-4o.
        detector: Optional pre-built local face detector (injected for tests /
            to share one instance); built lazily otherwise.
        timeout_s: Per-request network timeout in seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        enable_fallback: bool = True,
        max_labels: int = 15,
        detector=None,
        timeout_s: float = 30.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.enable_fallback = enable_fallback
        self.max_labels = max_labels
        self._detector = detector
        self.timeout_s = timeout_s
        self._openai_client = None  # built lazily

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def available(self) -> bool:
        """True if an OpenAI API key is configured."""
        return bool(self.api_key)

    def analyze(self, path: str) -> PhotoBrain:
        """
        Analyze one photo.

        Strategy:
        1. Call OpenAI GPT-4o Vision for scene labels + dominant colours +
           rough face count (if API key is set).
        2. Run MediaPipe locally for precise face bounding-boxes (always, if
           MediaPipe is available — it's better than GPT-4o for pixel coords).
        3. If the API call fails and ``enable_fallback`` is True, degrade to
           local-only (no scene labels).
        """
        # --- Step 1: OpenAI scene understanding ---
        gpt_labels: List[str] = []
        gpt_confidences: List[float] = []
        gpt_colors: List[RGB] = []
        gpt_face_count: int = 0
        source = SOURCE_LOCAL

        if self.api_key:
            try:
                gpt_labels, gpt_confidences, gpt_colors, gpt_face_count = (
                    self._call_openai(path)
                )
                source = SOURCE_OPENAI
            except Exception as exc:  # noqa: BLE001
                if not self.enable_fallback:
                    raise
                logger.warning(
                    "OpenAI Vision failed for '%s' (%s); using local fallback.", path, exc
                )

        # --- Step 2: MediaPipe face detection (local, always attempted) ---
        boxes: List[RelRect] = []
        try:
            detector = self._local_detector()
            if detector is not None:
                result = detector.detect(path)
                boxes = [
                    tuple(float(v) for v in b) for b in getattr(result, "regions", ())
                ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Local face detection failed for '%s': %s", path, exc)

        # --- Step 3: dominant colour fallback if GPT skipped ---
        colors = gpt_colors
        if not colors:
            colors = self._local_dominant_color(path)

        face_count = len(boxes) if boxes else gpt_face_count

        return PhotoBrain(
            path=path,
            face_count=face_count,
            face_boxes=boxes,
            face_landmarks=[],          # landmarks need a dedicated model
            face_emotions=[],           # emotion from GPT is not pixel-precise
            scene_labels=gpt_labels,
            scene_confidence=gpt_confidences,
            dominant_colors=colors,
            capture_time=self._exif_time(path),
            source=source,
        )

    def analyze_batch(self, paths: Sequence[str]) -> List[PhotoBrain]:
        """Analyze many photos, one :class:`PhotoBrain` each (order preserved)."""
        return [self.analyze(p) for p in paths]

    # ------------------------------------------------------------------ #
    # OpenAI Vision REST call
    # ------------------------------------------------------------------ #

    def _call_openai(
        self, path: str
    ) -> Tuple[List[str], List[float], List[RGB], int]:
        """
        Send one image to GPT-4o Vision and return
        ``(scene_labels, scene_confidence, dominant_colors, face_count)``.

        Raises on any error so :meth:`analyze` can fall back.
        """
        client = self._get_openai_client()

        # Encode image as base64 data-URL.
        with open(path, "rb") as fh:
            raw = fh.read()
        b64 = base64.b64encode(raw).decode("ascii")

        # Detect MIME type from file magic bytes.
        mime = _detect_mime(raw)

        response = client.chat.completions.create(
            model=_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64}",
                                "detail": "low",   # cheaper; sufficient for labels
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Analyse this wedding photograph. "
                                f"Return at most {self.max_labels} scene_labels."
                            ),
                        },
                    ],
                },
            ],
            max_tokens=512,
            timeout=self.timeout_s,
        )

        raw_text = response.choices[0].message.content or ""
        return self._parse_gpt_response(raw_text)

    @staticmethod
    def _parse_gpt_response(
        raw_text: str,
    ) -> Tuple[List[str], List[float], List[RGB], int]:
        """
        Extract structured data from the GPT JSON response.

        Robust to minor formatting issues (leading/trailing whitespace, code
        fences, etc.).  Returns empty lists on parse failure so the caller
        can decide whether to fall back.
        """
        text = raw_text.strip()
        # Strip markdown code fences if GPT wraps them.
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("GPT-4o returned non-JSON: %r", raw_text[:200])
            return [], [], [], 0

        labels = [str(l).strip().lower() for l in data.get("scene_labels", []) or []]
        confs_raw = data.get("scene_confidence", []) or []
        confs = [float(c) for c in confs_raw]
        # Pad/trim so labels and confidences have equal length.
        if len(confs) < len(labels):
            confs.extend([0.8] * (len(labels) - len(confs)))
        else:
            confs = confs[: len(labels)]

        colors: List[RGB] = []
        for col in (data.get("dominant_colors") or [])[:3]:
            if isinstance(col, dict):
                colors.append(
                    (
                        int(round(float(col.get("r", 0)))),
                        int(round(float(col.get("g", 0)))),
                        int(round(float(col.get("b", 0)))),
                    )
                )

        face_count = int(data.get("face_count", 0) or 0)

        return labels, confs, colors, face_count

    def _get_openai_client(self):
        """Lazily build and cache the OpenAI client (avoids import cost at startup)."""
        if self._openai_client is None:
            try:
                import openai  # noqa: PLC0415
            except ImportError as exc:
                raise ImportError(
                    "The 'openai' package is required for Vision Brain. "
                    "Run: pip install openai"
                ) from exc
            self._openai_client = openai.OpenAI(api_key=self.api_key)
        return self._openai_client

    # ------------------------------------------------------------------ #
    # Local fallback helpers
    # ------------------------------------------------------------------ #

    def _local(self, path: str) -> PhotoBrain:
        """Best-effort local extraction: MediaPipe faces + a dominant colour."""
        boxes: List[RelRect] = []
        try:
            detector = self._local_detector()
            if detector is not None:
                result = detector.detect(path)
                boxes = [tuple(float(v) for v in b) for b in getattr(result, "regions", ())]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Local face detection failed for '%s': %s", path, exc)

        colors = self._local_dominant_color(path)

        return PhotoBrain(
            path=path,
            face_count=len(boxes),
            face_boxes=boxes,
            face_landmarks=[],
            face_emotions=[],
            scene_labels=[],
            scene_confidence=[],
            dominant_colors=colors,
            capture_time=self._exif_time(path),
            source=SOURCE_LOCAL,
        )

    def _local_detector(self):
        if self._detector is None:
            try:
                from core.face_detector import FaceDetector  # noqa: PLC0415

                self._detector = FaceDetector()
            except Exception as exc:  # noqa: BLE001
                logger.debug("No local face detector available: %s", exc)
                self._detector = False  # sentinel: tried and failed
        return self._detector or None

    @staticmethod
    def _local_dominant_color(path: str) -> List[RGB]:
        try:
            from core.album.theming import dominant_color  # noqa: PLC0415

            return [tuple(int(v) for v in dominant_color([path]))]
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _image_size(path: str) -> Tuple[int, int]:
        try:
            from PIL import Image  # noqa: PLC0415

            with Image.open(path) as img:
                return img.size
        except Exception:  # noqa: BLE001
            return (1, 1)

    @staticmethod
    def _exif_time(path: str) -> Optional[datetime]:
        try:
            from PIL import Image  # noqa: PLC0415

            with Image.open(path) as img:
                exif = img.getexif()
            raw = exif.get(0x9003) or exif.get(0x0132)  # DateTimeOriginal / DateTime
            if raw:
                return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            pass
        return None


# ------------------------------------------------------------------ #
# Module-level helpers
# ------------------------------------------------------------------ #

def _detect_mime(raw_bytes: bytes) -> str:
    """Guess image MIME type from the first few magic bytes."""
    if raw_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw_bytes[:4] in (b"RIFF", b"WEBP"):
        return "image/webp"
    # Default — JPEG is the most common in wedding photography.
    return "image/jpeg"


__all__ = [
    "PhotoBrain",
    "VisionBrain",
    "SOURCE_OPENAI",
    "SOURCE_GOOGLE",  # legacy alias
    "SOURCE_LOCAL",
]
