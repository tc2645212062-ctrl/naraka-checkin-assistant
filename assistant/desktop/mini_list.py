"""Recognize visible mini-program sections without assuming a scroll position."""
from .vision import find_text


def mini_list_ready(items):
    recent = find_text(items, '最近使用', exact=True)
    common = find_text(items, '我的常用', exact=True)
    if recent and common:
        return True  # Existing layout, including separate Chrome list windows.
    if len(common) != 1:
        return False
    header = common[0]
    tolerance = max(24, header.bounds[3] - header.bounds[1])
    # A scrolled list can clip the recent heading. Require the matching
    # right-hand link on the common card rather than accepting a chat word.
    links = [item for item in find_text(items, '我的小程序')
             if abs(item.center[1] - header.center[1]) <= tolerance
             and item.center[0] > header.bounds[2] + 40]
    return len(links) == 1


def select_mini_entry(items, title):
    """Prefer recent, then common; reject duplicates within either section."""
    if not mini_list_ready(items):
        return None
    headings = {name: find_text(items, name, exact=True)
                for name in ('最近使用', '我的常用', '小游戏')}
    for section in ('最近使用', '我的常用'):
        headers = headings[section]
        if not headers:
            continue
        if len(headers) != 1:
            raise ValueError(f'小程序列表的“{section}”分区标题不唯一，停止选择')
        header = headers[0]
        lower = header.center[1]
        next_headers = [item.center[1] for group in headings.values()
                        for item in group if item.center[1] > lower]
        upper = min(next_headers or [lower + max(220, 18 * (header.bounds[3] - header.bounds[1]))])
        targets = [item for item in find_text(items, title, exact=True)
                   if lower < item.center[1] < upper and item.center[0] >= header.bounds[0] - 40]
        if len(targets) > 1:
            raise ValueError(f'“{section}”分区出现多个同名小程序，停止选择')
        if targets:
            return targets[0]
    return None
