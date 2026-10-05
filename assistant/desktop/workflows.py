from __future__ import annotations

import time
import re
from datetime import timedelta

from . import windows as win
from .key import article_is_today, extract_key_from_article_image, publication_date, key_valid_now
from ..config import now
from .recognition import match, read_image
from .task_workflows import Snapshot, TaskWorkflows
from .models import OcrItem
from .vision import find_text, combined_text, normalize

TITLES = {"calendar": "日历签到", "login": "登录游戏领取", "card": "名片任务", "poster": "海报任务", "read": "阅读文章", "key": "金钥匙答题"}


class KeyUnavailable(RuntimeError):
    """No valid answer was submitted; checking again later is safe."""


class WorkflowDriver(TaskWorkflows):
    def __init__(self, adapter):
        self.adapter = adapter
        self.auxiliary_handles = set()
        try:
            self._main = win.choose_main_window(win.enumerate_wechat_windows())
        except win.AutomationSafetyError:
            self._main = None

    @property
    def main(self):
        if self._main is None:
            self._main = self.adapter.ensure_main()
        return self._main

    @main.setter
    def main(self, value):
        self._main = value

    def wrap(self, snap):
        return Snapshot(snap[1].stem, *snap)

    def snapshot(self, window, name):
        return self.wrap(self.adapter.snapshot(window, name))

    def log(self, event, **details):
        self.adapter.event(f"{event}：{details}")

    def click_item(self, snapshot, item):
        self.adapter.guard()
        window = win.focus_window(snapshot.window)
        win.click_window_point(window, *item.center)
        self.adapter.event(f"点击任务控件：{item.text}")
        time.sleep(.8)

    def click_text(self, snapshot, text, exact=False):
        matches = find_text(snapshot.items, text, exact=exact)
        if len(matches) != 1:
            raise win.AutomationSafetyError(f"按钮不唯一：{text}")
        self.click_item(snapshot, matches[0])

    def task_button(self, snapshot, task_text):
        defaults = {"每日阅读文章": "read", "分享无间镖客海报": "poster", "分享一次名片": "card", "金钥匙": "key", "登录永劫无间游戏": "login"}
        mapped = self.adapter.config.task_labels.get(defaults.get(task_text, ""), task_text)
        return super().task_button(snapshot, mapped)

    def back(self, window):
        page = self.snapshot(window, "before-back")
        frame = read_image(page.image)
        hit = match(frame[:max(110,frame.shape[0]//8), :max(80,frame.shape[1]//6)], "back", (.67,.8,1,1.2,1.33), .90)
        if not hit:
            raise win.AutomationSafetyError("未识别小程序返回箭头")
        win.click_window_point(page.window, *hit[1])
        time.sleep(.6)

    def wait_for_welfare(self, mini, name, timeout=12):
        return self.wrap(self.adapter.welfare())

    def refresh_welfare_from_home(self, mini, name):
        return self.wrap(self.adapter.welfare())

    def _main_search_home(self, name, recover=True):
        page = self.snapshot(win.restore_existing_wechat(self.main), name)
        if self.search_fields(page):
            return page
        frame = read_image(page.image)
        hit = match(frame[:, :min(130, frame.shape[1]//6)], "chat", (.67,.8,1,1.2,1.33), .88)
        if not hit:
            raise win.AutomationSafetyError("未识别微信聊天导航图标")
        win.click_window_point(page.window, *hit[1])
        for attempt in range(5):
            time.sleep(1)
            page = self.snapshot(self.main, name+f"-chat-{attempt}")
            if self.search_fields(page):
                return page
        # Only recover a known discovery page which failed to leave after
        # the observed chat navigation click. Unknown dialogs are never killed.
        if recover and all(find_text(page.items, label, exact=True)
                           for label in ("发现", "朋友圈", "小程序")):
            self.main = self.adapter.recover_wechat(self.main)
            return self._main_search_home(name+"-recovered", recover=False)
        raise win.AutomationSafetyError("未识别微信搜索框")

    def search_fields(self, page):
        matches = find_text(page.items, "搜索") + find_text(page.items, self.adapter.config.mini_title, exact=True)
        fields = [i for i in matches if 100 < i.center[0] < page.window.width*.45 and 40 < i.center[1] < 130]
        if fields:
            return fields
        frame = read_image(page.image)
        hit = match(frame[:150,:frame.shape[1]//2], "search", (.67,.8,1,1.2,1.33), .93)
        if hit:
            x,y = hit[1]
            return [OcrItem("搜索", hit[0], ((x+22,y-9),(x+72,y-9),(x+72,y+9),(x+22,y+9)))]
        return []

    def find_today_key(self):
        # Validate publication date together with the official banner's expiry;
        # yesterday's answer can remain valid until the stated deadline.
        official = self.open_official_account()
        for _ in range(2):
            win.scroll_window(official, -80)
            time.sleep(.5)
        latest = self.snapshot(official, "official-latest")
        title = self._latest_key_article_item(latest)
        self.click_item(latest, title)
        time.sleep(1.5)
        readers = [w for w in win.enumerate_wechat_windows() if w.visible and w.title == "微信" and w.class_name.startswith("Chrome")]
        foreground = [w for w in readers if w.handle == win.win32gui.GetForegroundWindow()]
        if len(foreground) == 1:
            readers = foreground
        if len(readers) != 1:
            raise win.AutomationSafetyError("无法唯一识别官方文章窗口")
        reader = readers[0]
        self.auxiliary_handles.add(reader.handle)
        reader = win.focus_window(reader)
        win.press_keys(reader, "^{HOME}")
        time.sleep(.5)
        published = None
        for attempt in range(15):
            top = self.snapshot(reader, f"key-article-top-{attempt:02d}")
            published = publication_date(top.items)
            if published is not None:
                break
            if attempt == 2:
                for _ in range(4):
                    win.scroll_window(reader, 80)
                    time.sleep(.15)
            time.sleep(.7)
        if published is None:
            raise win.AutomationSafetyError("文章加载后仍无法识别官方署名和发布日期")
        self.adapter.verified_articles.add(reader.handle)
        self.adapter.event("已核验官方文章日期，优先从文章末尾向上寻找金钥匙")
        win.press_keys(reader, "^{END}")
        time.sleep(.5)
        answer = self._scan_key_region(reader, published, title, "key-tail", 4, 14)
        if answer:
            return answer
        self.adapter.event("文章末尾未识别完整金钥匙，回到顶部进行一次全文扫描")
        win.press_keys(reader, "^{HOME}")
        time.sleep(.5)
        answer = self._scan_key_region(reader, published, title, "key-page", -4, 96)
        if answer:
            return answer
        raise win.AutomationSafetyError("最新官方文章扫描结束，未识别可确认的金钥匙；请查看文章证据，未提交")

    def _scan_key_region(self, reader, published, title, prefix, scroll_steps, limit):
        previous_signature = None
        unchanged = 0
        for index in range(limit):
            self.adapter.guard()
            page = self.snapshot(reader, f"{prefix}-{index:02d}")
            answer = extract_key_from_article_image(page.image, page.items)
            if answer:
                if not key_valid_now(page.items, published, now()):
                    raise KeyUnavailable("最新文章的金钥匙已过期或有效期不明，等待新的有效答案；未提交")
                self.log("gold_key_source", title=title.text, date=str(self.adapter.day), evidence=str(page.image))
                return answer
            signature = tuple(normalize(i.text) for i in page.items
                              if 70 < i.center[1] < page.window.height-80)
            unchanged = unchanged+1 if signature and signature == previous_signature else 0
            previous_signature = signature
            if unchanged >= 2:
                self.adapter.event("文章滚动后连续两次内容未变化，已结束本段扫描")
                break
            # A full-screen wheel jump can skip an entire key banner between
            # captures. Half the former step preserves overlap on this reader.
            win.scroll_window(reader, scroll_steps)
            time.sleep(.5)
        return None

    @staticmethod
    def _latest_key_article_item(latest):
        markers = [*find_text(latest.items, "今天"), *find_text(latest.items, "昨天")]
        if not markers:
            markers = [i for i in latest.items if re.fullmatch(r"(?:上午|下午)?\d{1,2}:\d{2}",i.text.replace(" ",""))]
        if not markers:
            candidates = [i for i in latest.items if 120 < i.center[1] < latest.window.height-100 and len(i.text)>=8]
            if candidates:
                # At the bottom, the date above a tall card may be off-screen.
                # The article's own publication date must still be verified.
                return max(candidates,key=lambda item:item.center[1])
            raise win.AutomationSafetyError("公众号没有可识别的最新文章卡片")
        marker = max(markers, key=lambda item:item.center[1])
        # WeChat timestamps precede the next article card, not the previous one.
        candidates = [i for i in latest.items if marker.center[1]+20 < i.center[1] < latest.window.height-100 and len(i.text)>=8]
        if not candidates:
            raise win.AutomationSafetyError("最新日期下面没有完整可点击的文章卡片")
        return min(candidates,key=lambda item:item.center[1])
