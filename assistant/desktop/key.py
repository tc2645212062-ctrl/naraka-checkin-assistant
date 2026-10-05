from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from .models import OcrItem
from .vision import engine, find_text, normalize, read_text


def article_is_today(items: list[OcrItem], today: date) -> bool:
    body = normalize("".join(item.text for item in items))
    variants = {
        f"{today.year}年{today.month}月{today.day}日",
        f"{today.year}-{today.month:02d}-{today.day:02d}",
        f"{today.year}/{today.month:02d}/{today.day:02d}",
    }
    return any(normalize(value) in body for value in variants)


def publication_date(items):
    authors = [i for i in items if "永劫无间" in i.text]
    candidates = set()
    pattern = r"(20\d{2})年(\d{1,2})月(\d{1,2})日"
    for author in authors:
        for item in items:
            hit = re.search(pattern, item.text)
            if not hit:
                continue
            if item is not author:
                # OCR may split publisher and timestamp into adjacent boxes.
                # Associate only the same baseline, never a body/comment date.
                height = max(author.bounds[3]-author.bounds[1], item.bounds[3]-item.bounds[1], 1)
                gap = item.bounds[0]-author.bounds[2]
                if (not re.match(pattern, item.text.strip())
                        or abs(item.center[1]-author.center[1]) > height*.6
                        or not -height <= gap <= height*4
                        or item.center[0] <= author.center[0]):
                    continue
            try:
                candidates.add(date(*map(int, hit.groups())))
            except ValueError:
                continue
    return next(iter(candidates)) if len(candidates) == 1 else None


def key_valid_now(items, published, current):
    """Use explicit validity on the official banner; never hardcode daily expiry."""
    if published is None or not 0 <= (current.date()-published).days <= 1:
        return False
    body = "".join(i.text for i in items).replace(" ","")
    explicit = re.search(r"(?:有效期|截止)[^\d]{0,8}(?:(20\d{2})[年./-])?(\d{1,2})[月./-](\d{1,2})(?:日)?[^\d]{0,4}(\d{1,2})[:：点时](\d{2})?",body)
    relative = re.search(r"(?:金钥匙|有效期|截止).{0,12}?(?:次日|明日|明天)(上午|下午|晚上|中午)?(\d{1,2})[:：点时](\d{2})?",body)
    try:
        if explicit:
            year,month,day,hour,minute = explicit.groups()
            expiry = datetime(int(year or published.year),int(month),int(day),int(hour),int(minute or 0),tzinfo=current.tzinfo)
            return current < expiry <= current + timedelta(days=2)
        if relative:
            period,hour,minute = relative.groups()
            hour = int(hour)
            if period in {"下午", "晚上", "中午"} and hour < 12:
                hour += 12
            if period == "上午" and hour == 12:
                hour = 0
            target = published+timedelta(days=1)
            expiry = datetime(target.year,target.month,target.day,hour,int(minute or 0),tzinfo=current.tzinfo)
            return current < expiry
    except ValueError:
        return False
    return False


def extract_key_from_article_image(image: Path, items=None) -> str | None:
    """Read the large answer printed right of the '今日金钥匙' banner."""
    if items is None:
        items = read_text(image)
    anchors = find_text(items, "今日金钥匙", exact=True)
    if len(anchors) != 1:
        return None
    frame = cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return None
    height, width = frame.shape[:2]
    left, top, right, bottom = anchors[0].bounds
    x1 = min(width - 1, max(right + 100, width // 2))
    x2 = min(width, width - max(30, int(width * 0.10)))
    y1 = max(0, top - 65)
    y2 = min(height, bottom + 125)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = frame[y1:y2, x1:x2]
    def candidate(pixels, minimum):
        results, _ = engine()(pixels)
        words = [(re.sub(r"\s+", "", str(raw)), float(confidence))
                 for _, raw, confidence in results or []]
        words = [(word, score) for word, score in words if re.fullmatch(r"[\u4e00-\u9fff]{2,8}", word)]
        return words[0][0] if len(words) == 1 and words[0][1] >= minimum else None

    answer = candidate(crop, .65)
    if answer:
        return answer
    # Textured banners can confuse color OCR. Keep the complete answer region
    # and require two higher-confidence, agreeing readings; never lower the bar.
    gray = candidate(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), .75)
    if not gray:
        return None
    padded = candidate(cv2.copyMakeBorder(crop, 30, 30, 30, 30,
                                         cv2.BORDER_CONSTANT, value=(255,255,255)), .75)
    return gray if gray == padded else None
