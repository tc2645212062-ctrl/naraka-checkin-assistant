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

class ArticleScanTests(unittest.TestCase):
    def make_driver(self):
        from unittest.mock import Mock
        window=WindowInfo(1,1,'微信','Chrome',True,False,(0,0,1400,1360))
        adapter=Mock();adapter.day=date(2026,10,3);adapter.verified_articles=set()
        driver=WorkflowDriver(adapter);driver.open_official_account=Mock(return_value=window)
        driver._latest_key_article_item=Mock(return_value=item('官方最新文章'))
        driver.click_item=Mock()
        def capture(window,name):
            items=[item('劫宝 永劫无间 2026年10月2日21:00')] if 'top' in name else [item('文章正文',200)]
            return Snapshot(name,window,Path(name+'.png'),items)
        driver.snapshot=Mock(side_effect=capture)
        return driver,window

    def test_key_scroll_overlaps_banner_instead_of_skipping_it(self):
        from unittest.mock import patch
        driver,window=self.make_driver()
        with patch('assistant.desktop.workflows.win.enumerate_wechat_windows',return_value=[window]),patch('assistant.desktop.workflows.win.win32gui.GetForegroundWindow',return_value=1),patch('assistant.desktop.workflows.win.focus_window',return_value=window),patch('assistant.desktop.workflows.win.press_keys'),patch('assistant.desktop.workflows.win.scroll_window') as scroll,patch('assistant.desktop.workflows.time.sleep'),patch('assistant.desktop.workflows.extract_key_from_article_image',side_effect=[None,'测试口令']),patch('assistant.desktop.workflows.key_valid_now',return_value=True):
            self.assertEqual('测试口令',driver.find_today_key())
        self.assertEqual(4,scroll.call_args_list[-1].args[1])

    def test_repeated_article_footer_stops_scanning(self):
        from unittest.mock import patch
        from assistant.desktop.windows import AutomationSafetyError
        driver,window=self.make_driver()
        with patch('assistant.desktop.workflows.win.enumerate_wechat_windows',return_value=[window]),patch('assistant.desktop.workflows.win.win32gui.GetForegroundWindow',return_value=1),patch('assistant.desktop.workflows.win.focus_window',return_value=window),patch('assistant.desktop.workflows.win.press_keys'),patch('assistant.desktop.workflows.win.scroll_window') as scroll,patch('assistant.desktop.workflows.time.sleep'),patch('assistant.desktop.workflows.extract_key_from_article_image',return_value=None):
            with self.assertRaises(AutomationSafetyError):driver.find_today_key()
        self.assertLessEqual(scroll.call_count,6)

    def test_tail_key_is_found_without_scanning_article_body(self):
        from unittest.mock import patch
        driver,window=self.make_driver()
        with patch('assistant.desktop.workflows.win.enumerate_wechat_windows',return_value=[window]),patch('assistant.desktop.workflows.win.win32gui.GetForegroundWindow',return_value=1),patch('assistant.desktop.workflows.win.focus_window',return_value=window),patch('assistant.desktop.workflows.win.press_keys') as keys,patch('assistant.desktop.workflows.win.scroll_window'),patch('assistant.desktop.workflows.time.sleep'),patch('assistant.desktop.workflows.extract_key_from_article_image',return_value='测试口令'),patch('assistant.desktop.workflows.key_valid_now',return_value=True):
            self.assertEqual('测试口令',driver.find_today_key())
        keys.assert_any_call(window,'^{END}')
        names=[c.args[1] for c in driver.snapshot.call_args_list]
        self.assertIn('key-tail-00',names)
        self.assertFalse(any('key-page-' in n for n in names))

    def test_missing_tail_falls_back_to_article_start_once(self):
        from unittest.mock import patch
        driver,window=self.make_driver()
        with patch('assistant.desktop.workflows.win.enumerate_wechat_windows',return_value=[window]),patch('assistant.desktop.workflows.win.win32gui.GetForegroundWindow',return_value=1),patch('assistant.desktop.workflows.win.focus_window',return_value=window),patch('assistant.desktop.workflows.win.press_keys') as keys,patch('assistant.desktop.workflows.win.scroll_window'),patch('assistant.desktop.workflows.time.sleep'),patch('assistant.desktop.workflows.extract_key_from_article_image',side_effect=[None,None,None,'测试口令']),patch('assistant.desktop.workflows.key_valid_now',return_value=True):
            self.assertEqual('测试口令',driver.find_today_key())
        self.assertEqual(2,len([c for c in keys.call_args_list if c.args[1]=='^{HOME}']))
        names=[c.args[1] for c in driver.snapshot.call_args_list]
        self.assertIn('key-tail-02',names);self.assertIn('key-page-00',names)
