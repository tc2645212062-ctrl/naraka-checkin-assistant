import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter


class TaskBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = DesktopAdapter(Path(self.temp.name), manual=True)
        self.adapter.guard = lambda: None
        self.adapter.config = Config()
        self.adapter.welfare = lambda: (None, Path("unused"), [])

    def test_completed_rows_not_executed(self):
        self.adapter.results = {k:("already_signed", "page") for k in Config().enabled_tasks}
        with patch("assistant.desktop.workflows.WorkflowDriver.complete_namecard") as card:
            result = self.adapter.sign(Config())
        self.assertEqual("success", result.status)
        card.assert_not_called()

    def test_login_precondition_is_partial_not_success(self):
        self.adapter.results = {k:("already_signed", "page") for k in Config().enabled_tasks}
        self.adapter.results["login"] = ("not_signed", "page")
        with patch("assistant.desktop.workflows.WorkflowDriver.task_button", return_value=SimpleNamespace(text="去完成")):
            result = self.adapter.sign(Config())
        self.assertEqual("partial", result.status)
        self.assertEqual("precondition", self.adapter.results["login"][0])

    def test_enabled_task_selection_changes_daily_identity(self):
        self.assertNotEqual(Config().scope, Config(enabled_tasks=["calendar"]).scope)

    def test_game_ready_still_requires_miniprogram_claim_verification(self):
        from assistant.game_login import GameState
        config=Config(enabled_tasks=['login'],auto_game_login=True)
        with patch('assistant.game_login.ensure_game_login',return_value=GameState('ready','lobby')) as login, patch('assistant.desktop.workflows.WorkflowDriver.task_button',side_effect=[SimpleNamespace(text=t) for t in ['去完成','可领取','已完成']]), patch('assistant.desktop.workflows.WorkflowDriver.click_item') as click:
            result=self.adapter.sign(config)
        self.assertEqual('success',result.status)
        login.assert_called_once()
        click.assert_called_once()

    def test_claim_ready_does_not_start_game(self):
        config=Config(enabled_tasks=['login'],auto_game_login=True)
        with patch('assistant.game_login.ensure_game_login') as login, patch('assistant.desktop.workflows.WorkflowDriver.task_button',side_effect=[SimpleNamespace(text=t) for t in ['可领取','已完成']]), patch('assistant.desktop.workflows.WorkflowDriver.click_item'):
            self.assertEqual('success',self.adapter.sign(config).status)
        login.assert_not_called()

    def test_game_manual_verification_is_not_success(self):
        from assistant.game_login import GameState
        config=Config(enabled_tasks=['login'],auto_game_login=True)
        with patch('assistant.game_login.ensure_game_login',return_value=GameState('manual_required','captcha')), patch('assistant.desktop.workflows.WorkflowDriver.task_button',return_value=SimpleNamespace(text='去完成')), patch('assistant.desktop.workflows.WorkflowDriver.click_item') as click:
            self.assertEqual('manual_required',self.adapter.sign(config).status)
        click.assert_not_called()

    def test_unavailable_key_waits_without_submission(self):
        from assistant.desktop.workflows import KeyUnavailable
        self.adapter.results = {k:("already_signed", "page") for k in Config().enabled_tasks}
        self.adapter.results["key"] = ("not_signed", "page")
        with patch("assistant.desktop.workflows.WorkflowDriver.task_button", return_value=SimpleNamespace(text="去完成")), patch("assistant.desktop.workflows.WorkflowDriver.find_today_key", side_effect=KeyUnavailable("expired")), patch("assistant.desktop.workflows.WorkflowDriver.submit_key") as submit:
            result = self.adapter.sign(Config())
        self.assertEqual("partial", result.status)
        self.assertEqual("waiting_release",self.adapter.results["key"][0])
        submit.assert_not_called()


class ShareChooserTests(unittest.TestCase):
    def test_share_chooser_ignores_other_wechat_windows(self):
        from assistant.desktop.task_workflows import TaskWorkflows
        from assistant.desktop.models import WindowInfo
        main=WindowInfo(1,10,'微信','Qt',True,False,(0,0,1400,1000))
        mini=WindowInfo(2,11,'永劫无间','Chrome',True,False,(0,0,640,1100))
        chooser=WindowInfo(3,10,'微信发送给','Qt51514QWindowIcon',True,False,(0,0,1155,915))
        unrelated=WindowInfo(4,10,'微信','Qt51514QWindowIcon',True,False,(0,0,847,686))
        driver=TaskWorkflows(); driver.main=main; driver.auxiliary_handles=set()
        with patch('assistant.desktop.task_workflows.enumerate_wechat_windows',return_value=[main,mini,chooser,unrelated]), patch('assistant.desktop.task_workflows.focus_window',side_effect=lambda w:w):
            self.assertEqual(chooser,driver._wait_for_share_dialog(mini))

    def test_recovery_only_cancels_identified_naraka_share(self):
        from assistant.desktop.automation import StopAutomation
        from unittest.mock import Mock
        chooser=SimpleNamespace(visible=True,title='微信发送给',class_name='Qt51514')
        with tempfile.TemporaryDirectory() as temp:
            adapter=DesktopAdapter(Path(temp)); adapter.config=Config()
            adapter.text_click=Mock()
            with patch('assistant.desktop.automation.win.enumerate_wechat_windows',return_value=[chooser]):
                adapter.snapshot=lambda *_:(chooser,Path('fake'),[SimpleNamespace(text=t) for t in ['发送给','另一个小程序','取消']])
                with self.assertRaises(StopAutomation):adapter.recover_namecard_dialog()
                adapter.text_click.assert_not_called()
                adapter.snapshot=lambda *_:(chooser,Path('fake'),[SimpleNamespace(text=t) for t in ['发送给','永劫无间','取消']])
                adapter.recover_namecard_dialog()
                self.assertEqual('取消',adapter.text_click.call_args.args[1])


if __name__ == '__main__':
    unittest.main()


class NamecardAcknowledgementTests(unittest.TestCase):
    def run_card(self, delayed, never=False):
        from assistant.desktop.task_workflows import TaskWorkflows,Snapshot
        from assistant.desktop.models import OcrItem
        from unittest.mock import Mock
        def item(text):return OcrItem(text,.9,((0,0),(50,0),(50,20),(0,20)))
        mini=object()
        def snap(*text):return Snapshot('test',mini,Path('unused'),[item(t) for t in text])
        welfare=snap('分享一次名片','去完成');result=snap('分享一次名片','已完成')
        driver=TaskWorkflows();driver.click_item=Mock();driver.click_text=Mock()
        driver._wait_for_share_dialog=Mock(return_value=mini)
        driver.refresh_welfare_from_home=Mock(return_value=result)
        pages=[snap('我的名片','分享名片'),snap('取消')]+[snap('正在生成')]*delayed
        if not never:pages.append(snap('游戏名片已分享','确认'))
        driver.snapshot=Mock(side_effect=pages)
        with patch('assistant.desktop.task_workflows.focus_window',return_value=mini),patch('assistant.desktop.task_workflows.time.sleep'):
            if never:
                from assistant.desktop.windows import AutomationSafetyError
                with self.assertRaises(AutomationSafetyError):driver.complete_namecard(welfare)
                driver.click_text.assert_not_called();driver.refresh_welfare_from_home.assert_not_called()
            else:self.assertIs(result,driver.complete_namecard(welfare))
        self.assertEqual(['去完成','分享名片','取消'],[call.args[1].text for call in driver.click_item.call_args_list])

    def test_delayed_ack_is_observed_without_generating_twice(self):
        self.run_card(3)

    def test_missing_ack_does_not_become_success(self):
        self.run_card(12,True)
