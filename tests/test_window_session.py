import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from assistant.desktop.models import WindowInfo
from assistant.desktop.session import WindowSession
from assistant.desktop.automation import DesktopAdapter


def window(handle, pid=10, title='微信'):
    return WindowInfo(handle,pid,title,'Qt',True,False,(0,0,1000,800))


class WindowSessionTests(unittest.TestCase):
    def test_closes_new_minimizes_reused_leaves_untouched(self):
        existing,new,untouched=WindowInfo(1,10,'微信','Qt',True,True,(0,0,1000,800)),window(2,title='永劫无间'),window(3)
        with patch('assistant.desktop.session.win.enumerate_wechat_windows',side_effect=[[existing,untouched],[existing,new,untouched]]), patch.object(WindowSession,'game_windows',return_value=[]), patch('assistant.desktop.session.win.desktop_available',return_value=True), patch('assistant.desktop.session.win.close_window') as close, patch('assistant.desktop.session.win.win32gui.ShowWindow') as show:
            session=WindowSession();session.remember(existing);session.remember(new);session.finish()
            close.assert_called_once_with(new)
            show.assert_called_once_with(1,6)

    def test_reused_handle_for_another_process_is_untouched(self):
        with patch('assistant.desktop.session.win.enumerate_wechat_windows',side_effect=[[],[window(1,99)]]), patch.object(WindowSession,'game_windows',return_value=[]), patch('assistant.desktop.session.win.desktop_available',return_value=True), patch('assistant.desktop.session.win.close_window') as close:
            session=WindowSession();session.remember(window(1));session.finish();close.assert_not_called()

    def test_secure_desktop_does_not_cleanup(self):
        with patch('assistant.desktop.session.win.enumerate_wechat_windows',return_value=[]), patch.object(WindowSession,'game_windows',return_value=[]), patch('assistant.desktop.session.win.desktop_available',return_value=False), patch('assistant.desktop.session.win.close_window') as close:
            session=WindowSession();session.remember(window(1));session.finish();close.assert_not_called()

    def test_cleanup_exception_still_releases_automation_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter=DesktopAdapter(Path(folder));adapter.window_session=Mock();adapter.desktop_lock=Mock()
            adapter.window_session.finish.side_effect=RuntimeError('window vanished')
            adapter.close();adapter.desktop_lock.close.assert_called_once()

    def test_verification_page_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter=DesktopAdapter(Path(folder));adapter.window_session=Mock();adapter.desktop_lock=Mock();adapter.keep_windows=True
            adapter.close();adapter.window_session.finish.assert_not_called();adapter.desktop_lock.close.assert_called_once()

    def test_only_new_game_is_minimized(self):
        old,new=window(4),window(5)
        with patch('assistant.desktop.session.win.enumerate_wechat_windows',return_value=[]), patch.object(WindowSession,'game_windows',side_effect=[[old],[old,new],[old,new]]), patch('assistant.desktop.session.win.desktop_available',return_value=True), patch('assistant.desktop.session.win.win32gui.ShowWindow') as show, patch('assistant.desktop.session.win.close_window') as close:
            session=WindowSession();session.remember_started_game();session.finish()
            show.assert_called_once_with(5,6);close.assert_not_called()


    def test_game_can_be_minimized_before_wechat_cleanup(self):
        old,new=window(4),window(5)
        with patch('assistant.desktop.session.win.enumerate_wechat_windows',return_value=[]),patch.object(WindowSession,'game_windows',side_effect=[[old],[old,new],[old,new]]),patch('assistant.desktop.session.win.desktop_available',return_value=True),patch('assistant.desktop.session.win.win32gui.ShowWindow') as show,patch('assistant.desktop.session.win.close_window') as close:
            session=WindowSession();session.remember(window(8));session.remember_started_game()
            session.minimize_started_games()
            show.assert_called_once_with(5,6);close.assert_not_called()
            self.assertIn(8,session.touched)
            self.assertEqual({},session.games)
