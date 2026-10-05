from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..adapters import Result
from ..config import now
from . import windows as win
from .recognition import calendar, match, read_image
from .vision import combined_text, find_text, read_text


class StopAutomation(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class DesktopAdapter:
    """A bounded daily calendar task. Never sends shares or performs exchanges."""
    def __init__(self, evidence: Path, event=lambda message: None, cancel=None, manual=False):
        self.evidence = evidence
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.event, self.cancel, self.manual = event, cancel, manual
        self.step = 0
        self.window = None
        self.before = None
        self.day = now().date()
        self.config = None
        self.task_event = lambda task_id, title, status, reason: None
        self.results = {}
        self.welfare_snapshot = None
        self.desktop_lock = None
        self.window_session = None
        self.keep_windows = False
        self.verified_articles = set()

    def guard(self):
        if self.cancel and self.cancel.is_set():
            raise StopAutomation("interrupted", "用户停止了本轮任务")
        if not win.desktop_available():
            raise StopAutomation("desktop_busy", "Windows 已锁屏或交互桌面不可用，停止操作")
        if now().date() != self.day:
            raise StopAutomation("unrecognized", "执行跨越北京时间零点，停止使用旧日期")

    def snapshot(self, window, name, inspect_only=False):
        self.guard()
        if self.window_session and not inspect_only:
            self.window_session.remember(window)
        window = win.focus_window(window)
        win.assert_target_foreground(window)
        self.step += 1
        path = self.evidence / f"{self.step:02d}-{name}.png"
        win.capture_window(window, path)
        items = read_text(path, minimum_confidence=.45)
        if name in {"load-home", "welfare-open", "welfare-ready", "before-sign", "after-sign"}:
            from .game_tab import recognize_tabs
            items = recognize_tabs(read_image(path), items)
        path.with_suffix(".json").write_text(json.dumps([i.as_dict() for i in items], ensure_ascii=False), encoding="utf-8")
        self.event(f"截图 / OCR：{path.name}")
        if not inspect_only:
            self.check_page(window, items)
        return window, path, items

    def check_page(self, window, items):
        text = combined_text(items)
        from .verification import verification_prompt
        token = verification_prompt(items, window.handle in self.verified_articles)
        if token:
            raise StopAutomation("manual_required", f"微信页面要求：{token}，已停止自动操作")
        for token in ("网络连接失败", "网络异常", "网络不可用", "连接超时", "加载失败"):
            if token in text:
                raise StopAutomation("network_error", f"页面提示：{token}")
        for token in ("活动已结束", "活动不存在", "活动已下线"):
            if token in text:
                raise StopAutomation("activity_missing", f"页面提示：{token}")

    def click(self, snap, item):
        self.guard()
        win.click_window_point(snap[0], *item.center)
        self.event(f"点击已识别控件：{item.text}")
        time.sleep(.7)

    def text_click(self, snap, text):
        choices = find_text(snap[2], text, exact=True)
        if len(choices) != 1:
            raise StopAutomation("unrecognized", f"未唯一识别按钮：{text}")
        self.click(snap, choices[0])

    def existing_mini(self):
        candidates = [w for w in win.enumerate_wechat_windows()
                      if w.title == self.config.mini_title and "Chrome" in w.class_name and w.area > 200000]
        if len(candidates) > 1:
            raise StopAutomation("unrecognized", "发现多个同名小程序窗口")
        return candidates[0] if candidates else None

    def wait_mini(self):
        for _ in range(24):
            self.guard()
            mini = self.existing_mini()
            if mini:
                return mini
            time.sleep(.5)
        raise StopAutomation("entry_unavailable", "小程序窗口未出现，请检查名称或快捷方式")

    def ensure_main(self):
        import subprocess
        from pywinauto import Desktop
        if not win.wechat_processes():
            if not self.config.wechat_exe or not Path(self.config.wechat_exe).is_file():
                raise StopAutomation("entry_unavailable", "未找到微信；请在设置中选择 Weixin.exe")
            subprocess.Popen([self.config.wechat_exe], cwd=str(Path(self.config.wechat_exe).parent))
        login_clicked = False
        update_deferred = False
        for _ in range(30):
            self.guard()
            logins = [w for w in Desktop(backend="uia").windows() if "LoginWindow" in w.class_name() and w.window_text() == "微信"]
            if logins:
                buttons = [c for c in logins[0].descendants(control_type="Button") if c.window_text() == "进入微信"]
                if buttons and not login_clicked:
                    login_window = next((w for w in win.enumerate_wechat_windows() if w.handle == logins[0].handle), None)
                    if login_window is None:
                        raise StopAutomation("manual_required", "登录窗口已经变化，停止输入")
                    login_window = win.focus_window(login_window)
                    rect = buttons[0].rectangle()
                    win.click_window_point(login_window, (rect.left+rect.right)//2-login_window.rect[0], (rect.top+rect.bottom)//2-login_window.rect[1])
                    login_clicked = True
                    self.event("使用微信已保存的登录：进入微信")
                elif not buttons and not login_clicked:
                    raise StopAutomation("manual_required", "微信需要扫码或手机确认登录")
            try:
                main = win.choose_main_window(win.enumerate_wechat_windows())
                main = win.restore_existing_wechat(main)
                snap = self.snapshot(main, "wechat-start-page")
                from .update_prompt import defer_update_button
                later = defer_update_button(snap[2], main.width, main.height)
                if later:
                    if update_deferred:
                        raise StopAutomation("entry_unavailable", "微信更新提示未关闭，停止重复操作")
                    self.click(snap, later)
                    self.event("已选择微信更新提示的稍后处理，未安装更新")
                    update_deferred = True
                    continue
                return main
            except win.AutomationSafetyError:
                time.sleep(.5)
        raise StopAutomation("manual_required", "微信登录尚未完成，可能需要手机确认")

    def recover_wechat(self, main):
        from .recovery import restart_main
        from ..config import data_directory
        self.guard()
        try:
            restart_main(main, self.config.wechat_exe,
                         data_directory()/"wechat-recovery.json", self.day, self.event)
        except Exception as exc:
            raise StopAutomation("entry_unavailable", str(exc)) from exc
        time.sleep(3)
        return self.ensure_main()

    def mini_list_snapshot(self, main):
        # WeChat versions can host the list in a separate Chrome window.
        # The main window then stays blank; repeatedly focusing it hides the list.
        candidates = [w for w in win.enumerate_wechat_windows()
                      if w.visible and w.title == "微信" and w.class_name.startswith("Chrome")
                      and (w.minimized or w.area > 200000)]
        for window in candidates:
            page = self.snapshot(window, "mini-list-window", inspect_only=True)
            if find_text(page[2], "最近使用", exact=True) and find_text(page[2], "我的常用", exact=True):
                if self.window_session:
                    self.window_session.remember(page[0])
                self.check_page(page[0], page[2])
                return page
        return self.snapshot(main, "mini-list")

    def open_mini(self, recover=True):
        mini = self.existing_mini()
        if mini:
            return mini
        main = self.ensure_main()
        if self.config.shortcut:
            if not Path(self.config.shortcut).is_file():
                raise StopAutomation("entry_unavailable", "配置的小程序快捷方式已不存在")
            os.startfile(self.config.shortcut)
            return self.wait_mini()
        snap = self.mini_list_snapshot(main)
        def listed(page):
            return bool(find_text(page[2], "最近使用", exact=True) and find_text(page[2], "我的常用", exact=True))
        def discovery(page):
            return bool(find_text(page[2], "发现", exact=True) and find_text(page[2], "朋友圈", exact=True)
                        and find_text(page[2], "小程序", exact=True))
        if not listed(snap):
            if not discovery(snap):
                frame = read_image(snap[1])
                sidebar = frame[:, :min(130, frame.shape[1]//6)]
                hit = match(sidebar, "discover", (.67, .8, 1, 1.2, 1.33), .90)
                if not hit:
                    raise StopAutomation("entry_unavailable", "未识别微信发现入口；截图已保存")
                win.click_window_point(snap[0], *hit[1])
                for _ in range(12):
                    time.sleep(.5)
                    snap = self.snapshot(main, "discover")
                    if discovery(snap) or listed(snap):
                        break
                else:
                    if recover and any(i.center[1] < 130 and i.center[0] < main.width*.45 for i in find_text(snap[2], "搜索")):
                        self.recover_wechat(main)
                        return self.open_mini(recover=False)
                    raise StopAutomation("entry_unavailable", "点击发现后页面未就绪，未继续点击")
            if not listed(snap):
                self.text_click(snap, "小程序")
                for _ in range(15):
                    time.sleep(.7)
                    snap = self.mini_list_snapshot(main)
                    if listed(snap):
                        break
                else:
                    raise StopAutomation("network_error", "微信小程序列表加载超时，将按连接失败策略重试")
        recent = find_text(snap[2], "最近使用", exact=True)
        common = find_text(snap[2], "我的常用", exact=True)
        if len(recent) != 1:
            raise StopAutomation("entry_unavailable", "微信最近使用小程序列表未出现")
        lower = recent[0].center[1]
        upper = min([i.center[1] for i in common if i.center[1] > lower] or [lower + 450])
        matches = [i for i in find_text(snap[2], self.config.mini_title, exact=True) if lower < i.center[1] < upper]
        if len(matches) != 1:
            raise StopAutomation("entry_unavailable", "最近使用列表中没有唯一的目标小程序；首次请在微信打开一次或配置快捷方式")
        self.click(snap, matches[0])
        return self.wait_mini()

    def recover_namecard_dialog(self):
        # A previous interrupted run may leave the chooser above the mini-app.
        # Cancel only a visibly identified Naraka share, never select recipients.
        choosers = [w for w in win.enumerate_wechat_windows()
                    if w.visible and w.title == "微信发送给" and w.class_name.startswith("Qt")]
        if not choosers:
            return
        if len(choosers) != 1:
            raise StopAutomation("desktop_busy", "存在多个微信分享窗口，停止恢复")
        snap = self.snapshot(choosers[0], "pending-namecard-share")
        if not find_text(snap[2], self.config.mini_title, exact=True) or not find_text(snap[2], "发送给", exact=True):
            raise StopAutomation("desktop_busy", "当前分享窗口不能确认属于永劫无间，未操作")
        self.text_click(snap, "取消")

    def welfare(self, force_refresh=False):
        mini = self.open_mini()
        self.recover_namecard_dialog()
        entered = False
        game_clicked = False
        for attempt in range(15):
            snap = self.snapshot(mini, "load-home")
            text = combined_text(snap[2])
            if find_text(snap[2], "游戏名片已分享") and find_text(snap[2], "确认", exact=True):
                self.text_click(snap, "确认")
                continue
            if find_text(snap[2], self.config.success_text, exact=True) and find_text(snap[2], "太棒了", exact=True):
                self.text_click(snap, "太棒了")
                continue
            if "手机体验小程序" in "".join(text.split()) and find_text(snap[2], "确定", exact=True):
                self.text_click(snap, "确定")
                continue
            frame = read_image(snap[1])
            close = match(frame, "popup-close", (.8, 1, 1.2), .95)
            if close:
                win.click_window_point(snap[0], *close[1])
                time.sleep(.6)
                continue
            labels = find_text(snap[2], self.config.welfare_label, exact=True)
            if labels:
                from .game_tab import selected_game
                if (not force_refresh and labels[0].center[1] <= snap[0].height*.2
                        and find_text(snap[2], "已签到")
                        and selected_game(frame, snap[2]) == self.config.game):
                    self.assert_game(snap)
                    if self.config.expected_role and not find_text(snap[2], self.config.expected_role):
                        raise StopAutomation("manual_required", "页面角色与设置的角色关键词不匹配")
                    return snap
                if labels[0].center[1] <= snap[0].height*.2 and not entered:
                    # Reload via the observed home entry, without touching the
                    # other platform's tab or its account/task state.
                    hit = match(frame[:max(110,frame.shape[0]//8), :max(80,frame.shape[1]//6)], "back", (.67,.8,1,1.2,1.33), .90)
                    if not hit:
                        raise StopAutomation("unrecognized", "未识别福利中心返回入口，未切换其他平台")
                    win.click_window_point(snap[0], *hit[1])
                    time.sleep(.7)
                    continue
                if labels[0].center[1] > snap[0].height*.2:
                    # The welfare route loads the platform saved by the home
                    # page. Switching during that load can be overwritten.
                    selected = selected_game(frame, snap[2])
                    if selected != self.config.game:
                        if selected is not None and not game_clicked:
                            self.text_click(snap, self.config.game)
                            game_clicked = True
                        time.sleep(.6)
                        continue
                    self.text_click(snap, self.config.welfare_label)
                    entered = True
                    snap = self.snapshot(mini, "welfare-open")
                for _ in range(12):
                    snap = self.snapshot(mini, "welfare-ready")
                    if find_text(snap[2], "已签到") and selected_game(read_image(snap[1]), snap[2]) == self.config.game:
                        self.assert_game(snap)
                        if self.config.expected_role and not find_text(snap[2], self.config.expected_role):
                            raise StopAutomation("manual_required", "页面角色与设置的角色关键词不匹配")
                        return snap
                    time.sleep(.6)
                if "未绑定" in combined_text(snap[2]) or "立即绑定" in combined_text(snap[2]):
                    raise StopAutomation("manual_required", "所选端游 / 手游尚未绑定游戏账号")
                raise StopAutomation("unrecognized", f"无法确认福利中心已选中{self.config.game}并加载日历，未执行领取")
            time.sleep(.7)
        raise StopAutomation("entry_unavailable", "小程序首页没有可识别的福利中心入口")

    def assert_game(self, snap):
        from .game_tab import selected_game
        selected = selected_game(read_image(snap[1]), snap[2])
        if selected != self.config.game:
            raise StopAutomation("unrecognized", f"页面平台为{selected or '无法识别'}，配置要求{self.config.game}；已阻止操作")
        if self.config.game == "端游" and find_text(snap[2], "登录永劫无间手游"):
            raise StopAutomation("unrecognized", "任务内容属于手游，与端游配置不符；已阻止操作")

    def check(self, config):
        self.config = config
        self.event(f"证据目录：{self.evidence}")
        try:
            from ..config import data_directory
            from ..platform import InstanceLock
            self.desktop_lock = InstanceLock(data_directory()/"automation.lock")
            if not self.desktop_lock.acquire():
                return Result("desktop_busy", "另一个助手实例正在操作微信", "desktop")
            self.guard()
            if not self.manual and win.idle_seconds() < config.idle_seconds:
                return Result("desktop_busy", "用户正在操作桌面，稍后自动重试。", "desktop")
            from .session import WindowSession
            self.window_session = WindowSession(self.event)
            snap = self.welfare()
            self.window = snap[0]
            self.welfare_snapshot = snap
            from .workflows import WorkflowDriver, TITLES
            workflow = WorkflowDriver(self)
            pending = False
            for task_id in config.enabled_tasks:
                if task_id == "calendar":
                    self.before = calendar(read_image(snap[1]), snap[2], self.day)
                    done = self.before.status == "already_signed"
                    reason = self.before.detail
                else:
                    try:
                        button = workflow.task_button(workflow.wrap(snap), config.task_labels[task_id])
                        done = button.text == "已完成"
                        reason = f"页面任务状态：{button.text}"
                    except win.AutomationSafetyError:
                        done = False
                        reason = "当前页面未唯一识别任务行，执行阶段再次核对"
                self.record_task(task_id, "already_signed" if done else "not_signed", reason)
                pending = pending or not done
            return Result("not_signed" if pending else "already_signed", f"已独立检查 {len(config.enabled_tasks)} 项任务。证据：{self.evidence}", "desktop", True)
        except StopAutomation as exc:
            self.keep_windows = exc.status == "manual_required"
            return Result(exc.status, str(exc), "desktop")
        except (ValueError, win.AutomationSafetyError) as exc:
            return Result("unrecognized", str(exc), "desktop")

    def close(self):
        try:
            if self.window_session and not self.keep_windows and not (self.cancel and self.cancel.is_set()):
                self.window_session.finish()
        except Exception as exc:
            self.event("窗口清理失败，签到结果已保留：" + str(exc))
        finally:
            if self.desktop_lock:
                self.desktop_lock.close()

    def record_task(self, task_id, status, reason):
        from .workflows import TITLES
        self.results[task_id] = (status, reason)
        self.task_event(task_id, TITLES[task_id], status, reason)
        self.event(f"{TITLES[task_id]}：{status}；{reason}")

    def sign(self, config):
        from .workflows import WorkflowDriver, TITLES, KeyUnavailable
        workflow = WorkflowDriver(self)
        actions = {"login": workflow.complete_login_claim, "card": workflow.complete_namecard,
                   "poster": workflow.complete_poster, "read": workflow.complete_reading}
        # Finish the small WeChat tasks before launching the foreground game.
        task_order = [k for k in config.enabled_tasks if k != "login"]
        if "login" in config.enabled_tasks:
            task_order.append("login")
        for task_id in task_order:
            self.guard()
            if self.results.get(task_id, (None,))[0] == "already_signed":
                continue
            try:
                if task_id == "calendar":
                    result = self.sign_calendar(config)
                    self.record_task(task_id, result.status, result.reason)
                    continue
                snap = workflow.wrap(self.welfare())
                button = workflow.task_button(snap, config.task_labels[task_id])
                if button.text == "已完成":
                    self.record_task(task_id, "already_signed", "执行前复查已完成，未重复操作")
                    continue
                if task_id == "login" and button.text != "可领取":
                    if not config.auto_game_login or config.game != "端游":
                        self.record_task(task_id, "precondition", "尚未满足登录游戏条件；自动登录端游未启用")
                        continue
                    from ..game_login import ensure_game_login
                    game_result = ensure_game_login(self.evidence/"game", self.event, self.cancel)
                    self.guard()
                    if game_result.status == "ready" and self.window_session:
                        self.window_session.remember_started_game()
                        self.window_session.minimize_started_games()
                    if game_result.status == "manual_required":
                        self.keep_windows = True
                    if game_result.status != "ready":
                        self.record_task(task_id, game_result.status, game_result.reason)
                        if game_result.status in {"manual_required", "interrupted", "desktop_busy"}:
                            return Result(game_result.status, game_result.reason, "desktop")
                        continue
                    self.event("已识别游戏大厅，等待小程序登录积分状态同步")
                    for refresh in range(7):
                        self.guard()
                        snap = workflow.wrap(self.welfare(force_refresh=True))
                        button = workflow.task_button(snap, config.task_labels[task_id])
                        if button.text in {"可领取", "已完成"}:
                            break
                        if refresh < 6:
                            for _ in range(10):
                                self.guard()
                                time.sleep(1)
                    if button.text == "已完成":
                        self.record_task(task_id, "already_signed", "登录后小程序已显示完成，未重复领取")
                        continue
                    if button.text != "可领取":
                        self.record_task(task_id, "precondition", "已进入游戏大厅，但小程序仍未满足领取条件，稍后重试")
                        continue
                if button.text == "可领取":
                    workflow.click_item(snap, button)
                elif task_id == "key":
                    try:
                        answer = workflow.find_today_key()
                    except KeyUnavailable as exc:
                        self.record_task(task_id, "waiting_release", str(exc))
                        continue
                    snap = workflow.wrap(self.welfare())
                    workflow.submit_key(snap, answer)
                else:
                    actions[task_id](snap)
                final = workflow.wrap(self.welfare())
                if workflow.task_button(final, config.task_labels[task_id]).text != "已完成":
                    raise win.AutomationSafetyError("动作结束后任务未标记已完成")
                self.record_task(task_id, "success", "操作后已复查页面：已完成")
            except StopAutomation as exc:
                self.keep_windows = exc.status == "manual_required"
                self.record_task(task_id, exc.status, str(exc))
                if exc.status in {"manual_required", "network_error", "interrupted"}:
                    return Result(exc.status, f"{TITLES[task_id]}：{exc}。其他任务结果见日志。", "desktop")
            except Exception as exc:
                self.record_task(task_id, "unrecognized", str(exc))
        failed = [(TITLES[k], v[1]) for k,v in self.results.items() if v[0] not in {"success", "already_signed"}]
        if failed:
            return Result("partial", "；".join(f"{name}：{reason}" for name,reason in failed), "desktop")
        return Result("success", f"{len(config.enabled_tasks)} 项每日任务均已完成并验证。证据：{self.evidence}", "desktop", True)

    def sign_calendar(self, config):
        try:
            # A fresh capture closes the check/action gap; no blind retry after clicking.
            snap = self.snapshot(self.window, "before-sign")
            self.assert_game(snap)
            before = calendar(read_image(snap[1]), snap[2], self.day)
            if before.status == "already_signed":
                return Result("already_signed", "操作前再次检查发现今日已签到，未点击。", "desktop", True)
            self.guard()
            win.click_window_point(snap[0], *before.point)
            self.event("已点击当天签到格一次，等待成功证据；不会重复点击。")
            success_seen = False
            for _ in range(10):
                time.sleep(.6)
                snap = self.snapshot(self.window, "after-sign")
                if find_text(snap[2], config.success_text, exact=True):
                    success_seen = True
                    if find_text(snap[2], "太棒了", exact=True):
                        self.text_click(snap, "太棒了")
                        continue
                try:
                    after = calendar(read_image(snap[1]), snap[2], self.day)
                    if after.status == "already_signed" and (success_seen or after.days == before.days + 1):
                        return Result("success", f"当天日历变为已签到，且成功提示或累计天数变化已验证。证据：{self.evidence}", "desktop", True)
                except ValueError:
                    pass
            return Result("unrecognized", f"已点击一次但无法确认成功，禁止重复提交。请查看证据：{self.evidence}", "desktop")
        except StopAutomation as exc:
            self.keep_windows = exc.status == "manual_required"
            return Result(exc.status, str(exc), "desktop")
        except (ValueError, win.AutomationSafetyError) as exc:
            return Result("unrecognized", str(exc), "desktop")
