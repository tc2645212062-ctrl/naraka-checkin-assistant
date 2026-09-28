import unittest
from datetime import date, datetime
from pathlib import Path

from assistant.config import BEIJING
from assistant.desktop.key import key_valid_now, publication_date
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop.task_workflows import Snapshot
from assistant.desktop.workflows import WorkflowDriver


def item(text,y=0):
    return OcrItem(text,.99,((100,y),(500,y),(500,y+20),(100,y+20)))


class KeyTests(unittest.TestCase):
    def test_today_still_requires_recognized_validity(self):
        current = datetime(2026,9,23,19,tzinfo=BEIJING)
        self.assertFalse(key_valid_now([item("今日金钥匙")],current.date(),current))
        self.assertTrue(key_valid_now([item("每日金钥匙在次日下午6点前有效")],current.date(),current))

    def test_yesterday_key_requires_visible_expiry(self):
        current = datetime(2026,9,23,11,0,tzinfo=BEIJING)
        self.assertTrue(key_valid_now([item("有效期至2026年9月23日18:00")],date(2026,9,22),current))
        self.assertFalse(key_valid_now([item("今日金钥匙")],date(2026,9,22),current))
        self.assertFalse(key_valid_now([item("有效期至2026年9月23日10:00")],date(2026,9,22),current))

    def test_relative_expiry_is_based_on_publication_date(self):
        self.assertTrue(key_valid_now([item("有效期截止至明日18点")],date(2026,9,22),datetime(2026,9,23,11,tzinfo=BEIJING)))
        self.assertFalse(key_valid_now([item("有效期截止至明日18点")],date(2026,9,22),datetime(2026,9,23,19,tzinfo=BEIJING)))
        self.assertTrue(key_valid_now([item("每日金钥匙在次日下午6点前有效")],date(2026,9,22),datetime(2026,9,23,11,tzinfo=BEIJING)))
        self.assertFalse(key_valid_now([item("每日金钥匙在次日下午6点前有效")],date(2026,9,22),datetime(2026,9,23,18,tzinfo=BEIJING)))

    def test_older_articles_and_missing_publication_rejected(self):
        current = datetime(2026,9,23,11,tzinfo=BEIJING)
        self.assertFalse(key_valid_now([item("有效期至2026年9月24日18:00")],date(2026,9,21),current))
        self.assertFalse(key_valid_now([],None,current))

    def test_publication_date_from_author_line(self):
        self.assertEqual(date(2026,9,22),publication_date([item("劫宝 永劫无间 2026年9月22日21:14浙江")]))

    def test_publication_date_split_across_author_row(self):
        author=OcrItem('劫宝·永劫无间',.9,((179,210),(342,210),(342,235),(179,235)))
        published=OcrItem('2026年9月24日20:51 浙江',.9,((329,209),(737,209),(737,235),(329,235)))
        unrelated=OcrItem('2026年9月25日',.9,((329,409),(737,409),(737,435),(329,435)))
        self.assertEqual(date(2026,9,24),publication_date([author,published]))
        self.assertIsNone(publication_date([author,unrelated]))
        self.assertIsNone(publication_date([published]))

    def test_timestamp_belongs_to_following_card(self):
        old = item("济南最终兵器，出场！",320)
        newest = item("全体起立！锁定央视见证亚运首金",770)
        page = Snapshot("latest",WindowInfo(1,1,"永劫无间","Qt",True,False,(0,0,920,970)),Path("unused"),[old,item("昨天21:14",450),newest])
        self.assertEqual(newest,WorkflowDriver._latest_key_article_item(page))


if __name__ == "__main__":
    unittest.main()


class TexturedBannerTests(unittest.TestCase):
    def test_real_textured_banner_uses_consistent_readings(self):
        from assistant.desktop.key import extract_key_from_article_image
        image=Path(__file__).parent/'fixtures/textured-key-banner.png'
        self.assertEqual('特权网吧',extract_key_from_article_image(image))

    def test_disagreeing_fallback_readings_do_not_submit(self):
        from assistant.desktop.key import extract_key_from_article_image
        from unittest.mock import Mock,patch
        image=Path(__file__).parent/'fixtures/textured-key-banner.png'
        reader=Mock(side_effect=[([[[], '特权网吧', .58]],None),([[[], '不同答案', .9]],None),([[[], '特权网吧', .9]],None)])
        with patch('assistant.desktop.key.engine',return_value=reader):
            self.assertIsNone(extract_key_from_article_image(image))
