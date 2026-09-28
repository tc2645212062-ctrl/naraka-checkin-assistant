from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from rapidocr_onnxruntime import RapidOCR

from .models import OcrItem


_ENGINE: RapidOCR | None = None


def engine() -> RapidOCR:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCR()
    return _ENGINE


def read_text(image: Path, minimum_confidence: float = 0.45) -> list[OcrItem]:
    result, _ = engine()(str(image))
    items: list[OcrItem] = []
    for raw in result or []:
        polygon_raw, text, confidence = raw
        if float(confidence) < minimum_confidence:
            continue
        polygon = tuple((float(point[0]), float(point[1])) for point in polygon_raw)
        items.append(OcrItem(text=str(text).strip(), confidence=float(confidence), polygon=polygon))
    return sorted(items, key=lambda item: (item.bounds[1], item.bounds[0]))


def normalize(text: str) -> str:
    return re.sub(r"[\s‘’'\"“”·•|丨]+", "", text).strip("!！。:：").casefold()


def find_text(
    items: Iterable[OcrItem],
    needle: str,
    *,
    exact: bool = False,
) -> list[OcrItem]:
    expected = normalize(needle)
    matches: list[OcrItem] = []
    for item in items:
        actual = normalize(item.text)
        if actual == expected if exact else expected in actual:
            matches.append(item)
    return matches


def combined_text(items: Iterable[OcrItem]) -> str:
    return "\n".join(item.text for item in items)


def nearest_to(
    source: OcrItem,
    candidates: Iterable[OcrItem],
    *,
    max_vertical_distance: int = 90,
) -> OcrItem | None:
    sx, sy = source.center
    eligible = [item for item in candidates if abs(item.center[1] - sy) <= max_vertical_distance]
    if not eligible:
        return None
    return min(eligible, key=lambda item: abs(item.center[0] - sx) + 2 * abs(item.center[1] - sy))
