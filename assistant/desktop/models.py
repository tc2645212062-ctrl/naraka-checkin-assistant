from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    pid: int
    title: str
    class_name: str
    visible: bool
    minimized: bool
    rect: tuple[int, int, int, int]

    @property
    def width(self) -> int:
        return max(0, self.rect[2] - self.rect[0])

    @property
    def height(self) -> int:
        return max(0, self.rect[3] - self.rect[1])

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "pid": self.pid,
            "title": self.title,
            "class_name": self.class_name,
            "visible": self.visible,
            "minimized": self.minimized,
            "rect": list(self.rect),
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class OcrItem:
    text: str
    confidence: float
    polygon: tuple[tuple[float, float], ...]

    @property
    def center(self) -> tuple[int, int]:
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return round(sum(xs) / len(xs)), round(sum(ys) / len(ys))

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "polygon": [list(point) for point in self.polygon],
            "center": list(self.center),
            "bounds": list(self.bounds),
        }


@dataclass
class RunReport:
    started_at: str
    mode: str
    reused_wechat: bool = False
    initial_tray_state: bool | None = None
    initial_points: int | None = None
    final_points: int | None = None
    steps: dict[str, str] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_evidence(self, path: Path) -> None:
        self.evidence.append(str(path.resolve()))
