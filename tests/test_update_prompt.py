import unittest
from assistant.desktop.models import OcrItem
from assistant.desktop.update_prompt import defer_update_button


def item(text,x,y):
    return OcrItem(text,.99,((x-40,y-10),(x+40,y-10),(x+40,y+10),(x-40,y+10)))


class UpdatePromptTests(unittest.TestCase):
    def test_defers_known_update_without_installing(self):
        later=item('稍后处理',424,582)
        items=[item('新版本',77,30),item('忽略本次更新',178,582),later,item('更新',670,582)]
        self.assertIs(later,defer_update_button(items,847,686))

    def test_mentions_in_chat_or_missing_buttons_are_not_dialog(self):
        items=[item('新版本',77,330),item('忽略本次更新',178,582),item('稍后处理',424,582),item('更新',670,582)]
        self.assertIsNone(defer_update_button(items,847,686))
        self.assertIsNone(defer_update_button(items[1:],847,686))
