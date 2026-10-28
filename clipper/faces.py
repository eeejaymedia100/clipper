"""Anime face detectors. Each takes an RGB array and returns boxes in its pixel coordinates."""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .times import ClipperError


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    score: float = 1.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    def scaled(self, k: float) -> "Box":
        return Box(self.x0 * k, self.y0 * k, self.x1 * k, self.y1 * k, self.score)


Detector = Callable[[np.ndarray], list[Box]]


def imgutils_detector(threshold: float = 0.3) -> Detector:
    """dghs-imgutils YOLO face model trained on anime (ONNX, runs on CPU)."""
    import os
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    import warnings
    warnings.filterwarnings("ignore", message=".*unauthenticated requests.*")
    from imgutils.detect import detect_faces
    from PIL import Image

    def detect(rgb: np.ndarray) -> list[Box]:
        found = detect_faces(Image.fromarray(rgb), level="s", conf_threshold=threshold)
        return [Box(*map(float, xyxy), score) for xyxy, _, score in found]

    return detect


CASCADE_URL = "https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml"


def opencv_detector() -> Detector:
    """nagadomi's lbpcascade_animeface. Weaker than imgutils, but only needs OpenCV."""
    import cv2

    path = Path.home() / ".cache" / "clipper" / "lbpcascade_animeface.xml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(CASCADE_URL, path)
        except OSError as e:
            raise ClipperError(f"Couldn't download the OpenCV anime face model ({e}).") from None
    cascade = cv2.CascadeClassifier(str(path))
    if cascade.empty():
        path.unlink(missing_ok=True)
        raise ClipperError("The OpenCV anime face model is damaged. Run again to download it fresh.")

    def detect(rgb: np.ndarray) -> list[Box]:
        gray = cv2.equalizeHist(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        side = max(24, rgb.shape[0] // 20)
        found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(side, side))
        return [Box(x, y, x + w, y + h) for x, y, w, h in found]

    return detect


def make_detector(name: str, say: Callable[[str], None]) -> Detector:
    if name in ("auto", "imgutils"):
        try:
            detector = imgutils_detector()
            detector(np.zeros((64, 64, 3), np.uint8))   # loads (and first time, downloads) the model
            say("Face detector: imgutils (anime YOLO, CPU)")
            return detector
        except Exception as e:  # not installed, or the model couldn't be fetched
            if name == "imgutils":
                raise ClipperError(f"The imgutils face detector won't start ({e}). "
                                   "Try:  pip install dghs-imgutils onnxruntime") from None
            say("imgutils isn't working here, using the OpenCV anime face detector instead.")
    try:
        detector = opencv_detector()
    except ImportError:
        raise ClipperError("No face detector available. Run:  pip install -r requirements.txt") from None
    say("Face detector: OpenCV lbpcascade_animeface")
    return detector
