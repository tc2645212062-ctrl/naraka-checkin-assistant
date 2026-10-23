from __future__ import annotations
import time
from pathlib import Path
from dataclasses import dataclass
from .models import WindowInfo, OcrItem
from .vision import find_text, nearest_to, combined_text
from .windows import *
from .key import extract_key_from_article_image, publication_date
from .poster import gallery_share_button, data_update_pending
KEY_SCAN_PAGES = 48

@dataclass
class Snapshot:
    name: str
    window: WindowInfo
    image: Path
    items: list[OcrItem]

class PosterPageNotReady(AutomationSafetyError):
    """The poster route is unknown; do not attempt subsequent task routes."""
    def __init__(self, message, status='entry_unavailable'):
        super().__init__(message)
        self.status=status

class TaskWorkflows:
    def _wait_poster_page(self, mini, name, ready, reason, attempts=12):
        for attempt in range(attempts):
            page = self.snapshot(mini, name)
            if self._poster_gallery_ready(page) and data_update_pending(page.items):
                raise PosterPageNotReady('小程序提示藏品数据更新尚未生效，请稍后再试；未重复生成或发送海报',
                                         status='precondition')
            if ready(page):
                return page
            if attempt + 1 < attempts:
                time.sleep(.6)
        raise PosterPageNotReady(f"{reason}等待超时；未重复生成或发送海报")

    @staticmethod
    def _poster_gallery_ready(page):
        header = [item for item in find_text(page.items, "藏品馆", exact=True)
                  if item.center[1] < page.window.height * .15]
        return len(header) == 1 and gallery_share_button(page.items) is not None

    def task_button(self, snapshot: Snapshot, task_text: str) -> OcrItem:
        tasks = find_text(snapshot.items, task_text)
        buttons = [
            item
            for label in ("去完成", "可领取", "已完成", "未完成")
            for item in find_text(snapshot.items, label, exact=True)
        ]
        if len(tasks) != 1:
            raise AutomationSafetyError(f"Expected one task row containing {task_text!r}, found {len(tasks)}.")
        button = nearest_to(tasks[0], buttons, max_vertical_distance=75)
        if button is None:
            raise AutomationSafetyError(f"No status button was recognized for task {task_text!r}.")
        return button

    def complete_login_claim(self, welfare: Snapshot) -> Snapshot:
        button = self.task_button(welfare, "登录永劫无间游戏")
        if button.text == "可领取":
            self.click_item(welfare, button)
            time.sleep(1.5)
            welfare = self.snapshot(welfare.window, "06-after-login-claim")
        self.log("login_task", status=self.task_button(welfare, "登录永劫无间游戏").text)
        return welfare

    def _return_to_welfare_after_reading(self, mini: WindowInfo) -> Snapshot:
        """Verify the reading result from either supported back-navigation path."""
        returned = self.snapshot(mini, "13-reading-return")
        if find_text(returned.items, "每日阅读文章"):
            return self.wait_for_welfare(mini, "13-welfare-after-reading")
        if find_text(returned.items, "福利中心", exact=True):
            # Current client returns article readers to the mini-program home;
            # use the same verified route as the completed namecard flow.
            return self.refresh_welfare_from_home(mini, "13-welfare-after-reading")
        raise AutomationSafetyError("The reading article did not return to a recognized Naraka page.")

    def complete_reading(self, welfare: Snapshot) -> Snapshot:
        button = self.task_button(welfare, "每日阅读文章")
        if button.text == "已完成":
            self.log("reading_task", status="already-complete")
            return welfare
        before = {window.handle for window in enumerate_wechat_windows()}
        self.click_item(welfare, button)
        time.sleep(2.0)
        # The route may stay in the mini-program or open a WeChat reader.
        # Welfare itself contains the brand in its login task: brand text alone
        # is not evidence of an article. Observe before scrolling or going back.
        for attempt in range(12):
            foreground = win32gui.GetForegroundWindow()
            readers = [window for window in enumerate_wechat_windows()
                       if window.handle != welfare.window.handle and window.visible
                       and window.title == "微信" and window.class_name.startswith("Chrome")
                       and window.width > 700 and window.height > 400
                       and (window.handle not in before or window.handle == foreground)]
            if len(readers) > 1:
                raise AutomationSafetyError("发现多个文章阅读窗口，停止选择")
            host = readers[0] if readers else welfare.window
            article = self.snapshot(host, "11-reading-article")
            if not find_text(article.items, "福利中心", exact=True) and publication_date(article.items) is not None:
                break
            if attempt + 1 < 12:
                time.sleep(.6)
        else:
            raise AutomationSafetyError("阅读任务未在限定时间内打开可验证的官方文章；未滚动或返回")
        if host.handle != welfare.window.handle and host.handle not in before:
            self.auxiliary_handles.add(host.handle)
        for _ in range(5):
            scroll_window(host, -8)
            time.sleep(0.5)
        self.snapshot(host, "12-reading-scrolled")
        if host.handle == welfare.window.handle:
            focus_window(welfare.window)
            self.back(welfare.window)
        elif host.handle not in before:
            close_window(host)
        focus_window(welfare.window)
        time.sleep(2.0)
        result = self._return_to_welfare_after_reading(welfare.window)
        if self.task_button(result, "每日阅读文章").text != "已完成":
            raise AutomationSafetyError("Reading task was not marked complete after returning.")
        self.log("reading_task", status="complete")
        return result

    def complete_poster(self, welfare: Snapshot) -> Snapshot:
        button = self.task_button(welfare, "分享无间镖客海报")
        if button.text == "已完成":
            self.log("poster_task", status="already-complete")
            return welfare
        self.click_item(welfare, button)
        time.sleep(1.5)
        gallery = self._wait_poster_page(welfare.window, "14-gallery", self._poster_gallery_ready, "进入藏品馆")
        self.click_text(gallery, "分享藏品馆")
        time.sleep(1.0)
        # Generation can still be in progress after text and controls render.
        # Observe only; never click generation again or select a recipient.
        self._wait_poster_page(welfare.window, "15-poster-generated",
                               lambda page: find_text(page.items, "图片预览", exact=True)
                               and find_text(page.items, "转发给朋友"), "海报生成", attempts=20)
        # Back twice: preview -> gallery -> Welfare Center. Never click forward/send.
        focus_window(welfare.window)
        self.back(welfare.window)
        time.sleep(0.7)
        self._wait_poster_page(welfare.window, "16-gallery-return", self._poster_gallery_ready, "返回藏品馆")
        self.back(welfare.window)
        time.sleep(1.0)
        result = self.wait_for_welfare(welfare.window, "17-welfare-after-poster")
        if self.task_button(result, "分享无间镖客海报").text != "已完成":
            raise AutomationSafetyError("Poster generation did not credit the task.")
        self.log("poster_task", status="complete-without-sending")
        return result

    def _official_window(self) -> WindowInfo:
        matches = [
            window for window in enumerate_wechat_windows()
            if window.visible and window.title == "永劫无间"
            and window.class_name.startswith("Qt") and window.width > 850
        ]
        if len(matches) != 1:
            raise AutomationSafetyError(f"Expected one official account window, found {len(matches)}.")
        self.auxiliary_handles.add(matches[0].handle)
        return focus_window(matches[0])

    def open_official_account(self) -> WindowInfo:
        self.main = restore_existing_wechat(self.main)
        home = self._main_search_home("18-wechat-search-home")
        search = self.search_fields(home)
        if len(search) != 1:
            raise AutomationSafetyError("The WeChat search box was not recognized.")
        focus_window(self.main)
        click_window_point(self.main, *search[0].center)
        press_keys(self.main, "^a")
        paste_text(self.main, "永劫无间")
        time.sleep(1.3)
        results = self.snapshot(self.main, "19-official-search")
        section = find_text(results.items, "公众号", exact=True)
        next_section = find_text(results.items, "最近使用过的小程序")
        matches = find_text(results.items, "永劫无间", exact=True)
        if len(section) != 1 or len(next_section) != 1:
            raise AutomationSafetyError("Official-account search sections were not recognized.")
        choices = [item for item in matches if section[0].center[1] < item.center[1] < next_section[0].center[1]]
        if len(choices) != 1:
            raise AutomationSafetyError("The official Naraka account result is ambiguous.")
        click_window_point(self.main, *choices[0].center)
        time.sleep(1.5)
        return self._official_window()

    @staticmethod
    def _latest_key_article_item(latest: Snapshot) -> OcrItem:
        """Choose the newest official article, including a prior-evening post.

        The key cycle can be published in an article received "昨天" and used
        by today's mini-program task.  The newest chat card is immediately
        above its relative timestamp; older absolute-date cards are refused.
        """
        markers = [*find_text(latest.items, "今天"), *find_text(latest.items, "昨天")]
        if not markers:
            raise AutomationSafetyError("The official account has no message marked today or yesterday.")
        marker = min(markers, key=lambda item: item.center[1])
        article_titles = [
            item
            for item in latest.items
            if marker.center[1] - 350 < item.center[1] < marker.center[1] - 25
            and len(item.text) >= 8
            and "永劫无间" not in item.text
        ]
        if not article_titles:
            raise AutomationSafetyError("No official article card was recognized above the newest timestamp.")
        return min(article_titles, key=lambda item: item.center[1])

    def find_today_key(self) -> str:
        official = self.open_official_account()
        # A freshly opened official-account chat is at its latest message.
        latest = self.snapshot(official, "20-official-latest")
        article_title = self._latest_key_article_item(latest)
        self.log("official_key_source", timestamp="today-or-yesterday", title=article_title.text)
        self.click_item(latest, article_title)
        time.sleep(1.5)
        readers = [
            window for window in enumerate_wechat_windows()
            if window.visible and window.title == "微信" and window.class_name.startswith("Chrome")
        ]
        if len(readers) != 1:
            raise AutomationSafetyError("The official article reader was not recognized.")
        reader = focus_window(readers[0])
        self.auxiliary_handles.add(reader.handle)
        top = self.snapshot(reader, "21-today-article-top")
        if not find_text(top.items, "永劫无间"):
            raise AutomationSafetyError("The opened latest card is not a recognizable official Naraka article.")
        for index in range(KEY_SCAN_PAGES):
            page = self.snapshot(reader, f"22-key-scan-{index:02d}")
            answer = extract_key_from_article_image(page.image)
            if answer is not None:
                self.log("today_key_found", answer=answer, image=str(page.image.resolve()))
                return answer
            scroll_window(reader, -8)
            time.sleep(0.5)
        raise AutomationSafetyError("Today's article did not reveal an unambiguous gold key.")

    def submit_key(self, welfare: Snapshot, answer: str) -> Snapshot:
        button = self.task_button(welfare, "金钥匙")
        if button.text == "已完成":
            self.log("key_task", status="already-complete")
            return welfare
        self.click_item(welfare, button)
        dialog = self.snapshot(welfare.window, "23-key-dialog")
        field = find_text(dialog.items, "请输入金钥匙答题", exact=True)
        submit = find_text(dialog.items, "提交", exact=True)
        if len(field) != 1 or len(submit) != 1:
            raise AutomationSafetyError("Gold-key input dialog was not recognized.")
        click_window_point(welfare.window, *field[0].center)
        paste_text(welfare.window, answer)
        click_window_point(welfare.window, *submit[0].center)
        time.sleep(1.5)
        outcome = self.snapshot(welfare.window, "24-key-result")
        if not find_text(outcome.items, "回答正确"):
            raise AutomationSafetyError("The gold-key answer was submitted but not confirmed correct.")
        self.click_text(outcome, "太棒了", exact=True)
        result = self.wait_for_welfare(welfare.window, "25-welfare-after-key")
        if self.task_button(result, "金钥匙").text != "已完成":
            raise AutomationSafetyError("Gold-key task did not become complete.")
        self.log("key_task", status="complete", answer=answer)
        return result

    def _wait_for_share_dialog(self, mini: WindowInfo, timeout: float = 8.0) -> WindowInfo:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            candidates = [
                window
                for window in enumerate_wechat_windows()
                if window.visible
                and window.handle not in {self.main.handle, mini.handle}
                and window.title == "微信发送给"
                and window.class_name.startswith("Qt")
                and window.pid == self.main.pid
                and window.width >= 700
                and window.height >= 450
            ]
            if len(candidates) == 1:
                self.auxiliary_handles.add(candidates[0].handle)
                return focus_window(candidates[0])
            time.sleep(0.4)
        raise AutomationSafetyError("The WeChat share chooser did not appear.")

    def complete_namecard(self, welfare: Snapshot) -> Snapshot:
        mini = welfare.window
        button = self.task_button(welfare, "分享一次名片")
        if button.text == "已完成":
            self.log("namecard_task", status="already-complete")
            return welfare
        self.click_item(welfare, button)
        time.sleep(1.2)
        card = self.snapshot(mini, "07-namecard")
        if not find_text(card.items, "我的名片") or not find_text(card.items, "分享名片"):
            raise AutomationSafetyError("The game namecard generator did not open.")
        share = find_text(card.items, "分享名片")
        self.click_item(card, share[-1])
        chooser = self._wait_for_share_dialog(mini)
        chooser_shot = self.snapshot(chooser, "08-namecard-share-chooser")
        cancel = find_text(chooser_shot.items, "取消", exact=True)
        if len(cancel) != 1:
            raise AutomationSafetyError("The share chooser cancel button was not recognized; refusing to select a recipient.")
        self.click_item(chooser_shot, cancel[0])
        time.sleep(1.2)
        # The service acknowledgement can arrive several seconds after the
        # chooser closes. Observe only; never repeat share generation.
        for attempt in range(12):
            confirm = self.snapshot(focus_window(mini), "09-namecard-generated")
            if find_text(confirm.items, "游戏名片已分享") and find_text(confirm.items, "确认", exact=True):
                break
            time.sleep(.6)
        else:
            raise AutomationSafetyError("等待名片生成反馈超时，结果未知；未重复生成或发送名片。")
        self.click_text(confirm, "确认", exact=True)
        time.sleep(0.8)
        return self.refresh_welfare_from_home(mini, "10-welfare-after-namecard")
