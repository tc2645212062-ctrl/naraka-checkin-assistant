import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from assistant.desktop.models import OcrItem,WindowInfo
from assistant.desktop.game_tab import selected_game
from assistant.desktop.recognition import read_image
from assistant.desktop.automation import DesktopAdapter
from assistant.config import Config

class GameTabTests(unittest.TestCase):
    def fixture(self,name):
        p=Path(__file__).parent/'fixtures'/('game-tabs-'+name+'.png')
        items=[OcrItem(i['text'],i['confidence'],tuple(map(tuple,i['polygon']))) for i in json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))]
        return p,items
    def test_real_mobile_and_pc_tabs(self):
        for name,want in [('mobile','手游'),('pc','端游'),('gold-pc','端游')]:
            p,items=self.fixture(name);self.assertEqual(want,selected_game(read_image(p),items))
        self.assertIsNone(selected_game(read_image(p),[]))
    def test_mobile_calendar_cannot_be_clicked_or_marked_pc_success(self):
        p,items=self.fixture('mobile')
        with tempfile.TemporaryDirectory() as folder,patch('assistant.desktop.automation.win.click_window_point') as click:
            a=DesktopAdapter(Path(folder));a.config=Config(game='端游');a.window=WindowInfo(1,2,'永劫无间','Chrome',True,False,(0,0,642,1180));a.snapshot=Mock(return_value=(a.window,p,items))
            result=a.sign_calendar(a.config)
            self.assertEqual('unrecognized',result.status);self.assertIn('手游',result.reason);click.assert_not_called()

    def test_home_platform_is_selected_before_entering_welfare(self):
        from assistant.desktop.automation import StopAutomation
        def item(text,y):
            return OcrItem(text,.9,((70,y),(120,y),(120,y+20),(70,y+20)))
        window=WindowInfo(1,2,'永劫无间','Chrome',True,False,(0,0,642,1180))
        home=(window,Path('home.png'),[item('福利中心',650)])
        ready=(window,Path('welfare.png'),[item('已签到',340)])
        with tempfile.TemporaryDirectory() as folder,patch('assistant.desktop.automation.read_image',return_value=None),patch('assistant.desktop.automation.match',return_value=None),patch('assistant.desktop.automation.time.sleep'),patch('assistant.desktop.game_tab.selected_game',side_effect=['手游','端游','端游']):
            a=DesktopAdapter(Path(folder));a.config=Config(game='端游')
            a.open_mini=Mock(return_value=window);a.recover_namecard_dialog=Mock()
            a.snapshot=Mock(side_effect=[home,home,ready,ready]);a.text_click=Mock();a.assert_game=Mock()
            self.assertIs(ready,a.welfare())
            self.assertEqual(['端游','福利中心'],[c.args[1] for c in a.text_click.call_args_list])

    def test_failed_home_switch_never_enters_welfare(self):
        from assistant.desktop.automation import StopAutomation
        window=WindowInfo(1,2,'永劫无间','Chrome',True,False,(0,0,642,1180))
        label=OcrItem('福利中心',.9,((70,650),(120,650),(120,670),(70,670)))
        home=(window,Path('home.png'),[label])
        with tempfile.TemporaryDirectory() as folder,patch('assistant.desktop.automation.read_image',return_value=None),patch('assistant.desktop.automation.match',return_value=None),patch('assistant.desktop.automation.time.sleep'),patch('assistant.desktop.game_tab.selected_game',return_value='手游'):
            a=DesktopAdapter(Path(folder));a.config=Config(game='端游')
            a.open_mini=Mock(return_value=window);a.recover_namecard_dialog=Mock()
            a.snapshot=Mock(return_value=home);a.text_click=Mock()
            with self.assertRaises(StopAutomation):a.welfare()
            a.text_click.assert_called_once_with(home,'端游')

    def test_localized_tabs_recover_omitted_text_without_guessing(self):
        from assistant.desktop.game_tab import recognize_tabs
        p,items=self.fixture('mobile');frame=read_image(p)
        original=[i for i in items if i.text!='端游']
        tabs=[i for i in items if i.text in ('端游','手游')]
        raw=[(i.polygon,i.text,.8) for i in tabs]
        with patch('assistant.desktop.game_tab.engine',return_value=Mock(return_value=(raw,None))):
            recovered=recognize_tabs(frame,original)
            self.assertEqual('手游',selected_game(frame,recovered))
        with patch('assistant.desktop.game_tab.engine',return_value=Mock(return_value=(raw[:1],None))):
            self.assertEqual(original,recognize_tabs(frame,original))

    def test_verified_welfare_page_is_reused_without_back_navigation(self):
        def item(text,y):
            return OcrItem(text,.9,((70,y),(120,y),(120,y+20),(70,y+20)))
        window=WindowInfo(1,2,'永劫无间','Chrome',True,False,(0,0,642,1180))
        ready=(window,Path('welfare.png'),[item('福利中心',65),item('已签到',350),item('每日登录永劫无间游戏',800)])
        import numpy as np
        with tempfile.TemporaryDirectory() as folder,patch('assistant.desktop.automation.read_image',return_value=np.zeros((1180,642,3))),patch('assistant.desktop.automation.match',return_value=None),patch('assistant.desktop.game_tab.selected_game',return_value='端游'),patch('assistant.desktop.automation.win.click_window_point') as click:
            a=DesktopAdapter(Path(folder));a.config=Config(game='端游')
            a.open_mini=Mock(return_value=window);a.recover_namecard_dialog=Mock()
            a.snapshot=Mock(return_value=ready);a.assert_game=Mock()
            self.assertIs(ready,a.welfare())
            click.assert_not_called();a.snapshot.assert_called_once()
