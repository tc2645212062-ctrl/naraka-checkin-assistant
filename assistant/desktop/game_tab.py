"""Read the selected welfare platform; both tab labels are always present."""
import numpy as np

from .vision import find_text, engine
from .models import OcrItem


def selected_game(frame, items):
    if frame is None:
        return None
    height, width = frame.shape[:2]
    selected = []
    for title in ("端游", "手游"):
        labels = [i for i in find_text(items, title, exact=True) if i.center[1] < height*.23]
        if len(labels) != 1:
            return None
        item = labels[0]
        x, y = item.center
        size = max(1, item.bounds[3]-item.bounds[1])
        patch = frame[max(0,round(y-size*.9)):min(height,round(y+size*.9)),
                      max(0,round(x-size*1.6)):min(width,round(x+size*1.6))]
        if not patch.size:
            return None
        blue, green, red = np.median(patch.reshape(-1,3), axis=0)
        # Current selected tab is brown; inactive tabs are neutral. A redesign
        # or two highlighted tabs is unknown, never assumed to be the PC game.
        if red-blue > 25 and red-green > 15 and 55 < red < 220:
            selected.append(title)
    return selected[0] if len(selected) == 1 else None


def recognize_tabs(frame, items):
    """Recover small tab text that full-page OCR can omit; never infer positions."""
    if frame is None:
        return items
    height, width = frame.shape[:2]
    existing = [i for i in items if i.text in ("端游", "手游") and i.center[1] < height*.23]
    if not existing or {i.text for i in existing} == {"端游", "手游"}:
        return items
    raw, _ = engine()(frame[:int(height*.16), :int(width*.48)])
    tabs = [OcrItem(text, float(confidence), tuple(map(tuple, polygon)))
            for polygon, text, confidence in (raw or [])
            if text in ("端游", "手游") and float(confidence) >= .55]
    if len(tabs) != 2 or {i.text for i in tabs} != {"端游", "手游"}:
        return items
    left, right = sorted(tabs, key=lambda i: i.center[0])
    size = max(left.bounds[3]-left.bounds[1], right.bounds[3]-right.bounds[1])
    if (left.text != "端游" or right.text != "手游"
            or abs(left.center[1]-right.center[1]) > size*.5
            or not size < right.center[0]-left.center[0] < size*8):
        return items
    return [i for i in items if i not in existing] + tabs
