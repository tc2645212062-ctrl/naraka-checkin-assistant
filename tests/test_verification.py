import unittest
from types import SimpleNamespace
from assistant.desktop.verification import verification_prompt

class VerificationTests(unittest.TestCase):
    def test_official_article_advice_is_not_login_form(self):
        body=SimpleNamespace(text='在网吧、公司等公共场所登录游戏时，使用扫码登录（官网版本）或Steam令牌登录（Steam版本）')
        self.assertIsNone(verification_prompt([body],verified_article=True))
        self.assertEqual('扫码登录',verification_prompt([body]))
        self.assertEqual('扫码登录',verification_prompt([body,SimpleNamespace(text='请使用扫码登录继续访问')],True))
        self.assertEqual('安全验证',verification_prompt([body,SimpleNamespace(text='安全验证')],True))
