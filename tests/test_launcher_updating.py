import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from assistant.game_login import classify_page,ensure_game_login


class UpdatingTests(unittest.TestCase):
    def test_visible_launcher_update_is_distinct_from_unknown_page(self):
        state=classify_page('launcher',['永劫无间','正在更新','2.5%','更新信息'])
        self.assertEqual('updating',state.status);self.assertEqual('',state.action)

    def test_known_update_does_not_exhaust_unknown_frame_budget_or_click(self):
        calls=[];clock=[0.0];pages=iter([['正在更新','2.5%']]*9 + [['当前可用内存不足','继续启动','启动游戏']])
        class Broker:
            def __init__(self,_):pass
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def request(self,command):
                calls.append(command)
                return {'kind':'launcher','png':base64.b64encode(b'offline-frame').decode()}
        with tempfile.TemporaryDirectory() as folder, \
             patch('assistant.game_login.time.monotonic',side_effect=lambda:clock[0]), \
             patch('assistant.game_login.time.sleep',side_effect=lambda delay:clock.__setitem__(0,clock[0]+delay)):
            state=ensure_game_login(Path(folder),client_factory=Broker,read_page=lambda _:next(pages))
        self.assertEqual('precondition',state.status)
        self.assertIn('内存',state.reason)
        self.assertEqual(['observe']*10,calls)

    def test_update_text_cannot_hide_manual_verification(self):
        self.assertEqual('manual_required',classify_page('launcher',['正在更新','验证码']).status)
