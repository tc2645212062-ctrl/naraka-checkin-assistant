from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .vision import find_text

ASSETS = Path(__file__).parent / "assets"


def read_image(path):
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def match(image, name, scales=(1.0,), threshold=.88):
    """Template recognition; matching is invariant to absolute brightness."""
    template = read_image(ASSETS / f"{name}.png")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    best = (-1.0, (0, 0))
    for scale in scales:
        resized = cv2.resize(template, None, fx=scale, fy=scale)
        h, w = resized.shape[:2]
        if gray.shape[0] < h or gray.shape[1] < w:
            continue
        values = cv2.matchTemplate(gray, cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY), cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(values)
        if score > best[0]:
            best = score, (loc[0] + w // 2, loc[1] + h // 2)
    return best if best[0] >= threshold else None


@dataclass(frozen=True)
class Calendar:
    status: str
    point: tuple[int, int]
    days: int
    detail: str


def calendar(image, items, day):
    target = find_text(items, f"{day.month}.{day.day}", exact=True)
    counts = []
    for item in items:
        hit = re.fullmatch(r"已签到\s*(\d+)\s*天?", item.text)
        if hit:
            counts.append((item, int(hit.group(1))))
    if len(target) != 1 or len(counts) != 1:
        raise ValueError("未唯一识别当天日期和累计签到天数")
    x, y = target[0].center
    dates = [i for i in items if re.fullmatch(r"\d{1,2}\.\d{1,2}", i.text) and abs(i.center[1]-y) < 15]
    xs = sorted(i.center[0] for i in dates)
    if len(xs) < 5 or len(xs) > 7 or y <= counts[0][0].center[1]:
        raise ValueError("日历布局不符合已验证的日期行结构")
    gaps = np.diff(xs)
    spacing = float(np.median(gaps))
    if not 35 < spacing < 150 or max(abs(gaps-spacing)) > spacing*.25:
        raise ValueError("日期格间距不一致，停止操作")
    scale = spacing / 77.0
    left, right = round(x-27*scale), round(x+27*scale)
    top, bottom = round(y-88*scale), round(y-24*scale)
    if left < 0 or top < 0 or right > image.shape[1] or bottom > image.shape[0]:
        raise ValueError("日历区域超出截图")
    patch = image[top:bottom, left:right]
    scales = tuple(scale * s for s in (.92, .96, 1, 1.04, 1.08))
    signed = match(patch, "signed", scales, .86)
    unsigned = match(patch, "unsigned", scales, .86)
    if signed and (not unsigned or signed[0] > unsigned[0] + .08):
        state, hit = "already_signed", signed
    elif unsigned and (not signed or unsigned[0] > signed[0] + .08):
        state, hit = "not_signed", unsigned
    else:
        raise ValueError("当天日历图标不确定；不能用暗色或积分推断已签到")
    return Calendar(state, (left+hit[1][0], top+hit[1][1]), counts[0][1], f"日历图标匹配 {hit[0]:.3f}")
