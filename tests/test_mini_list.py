import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter, StopAutomation
from assistant.desktop.models import OcrItem, WindowInfo

def replay_items():
    data=json.loads((Path(__file__).parent/'fixtures/mini-list-scrolled.json').read_text(encoding='utf-8'))
    return [OcrItem(i['text'],i['confidence'],tuple(tuple(p) for p in i['polygon'])) for i in data['items']]

class ScrolledMiniListTests(unittest.TestCase):
    def test_recent_is_preferred_when_both_sections_are_visible(self):
        from assistant.desktop.mini_list import select_mini_entry
        items=replay_items()+[OcrItem('最近使用',.99,((572,60),(661,60),(661,85),(572,85)))]
        selected=select_mini_entry(items,'永劫无间')
        self.assertEqual((634,186),selected.center)

    def test_common_header_without_aligned_list_link_is_not_enough(self):
        from assistant.desktop.mini_list import mini_list_ready
        items=[i for i in replay_items() if not i.text.startswith('我的小程序')]
        self.assertFalse(mini_list_ready(items))
        items.append(OcrItem('我的小程序',.99,((1378,700),(1504,700),(1504,725),(1378,725))))
        self.assertFalse(mini_list_ready(items))

    def test_target_outside_common_section_is_not_selected(self):
        from assistant.desktop.mini_list import select_mini_entry
        items=[i for i in replay_items() if not (i.text=='永劫无间' and i.center[1]>400)]
        # An identically named recommendation below the next section is not
        # the trusted common entry. The clipped recent target also stays out.
        items.append(OcrItem('永劫无间',.99,((590,700),(678,700),(678,725),(590,725))))
        self.assertIsNone(select_mini_entry(items,'永劫无间'))

    def test_sidebar_or_chat_name_is_not_a_common_entry(self):
        from assistant.desktop.mini_list import select_mini_entry
        items=[i for i in replay_items() if not (i.text=='永劫无间' and i.center[1]>400)]
        items.append(OcrItem('永劫无间',.99,((100,434),(188,434),(188,459),(100,459))))
        self.assertIsNone(select_mini_entry(items,'永劫无间'))

    def test_duplicate_common_targets_stop_before_any_click(self):
        main=WindowInfo(10,20,'微信','Qt',True,False,(0,0,1590,1138))
        items=replay_items()+[OcrItem('永劫无间',.99,((800,434),(888,434),(888,459),(800,459)))]
        page=(main,Path('offline-only'),items)
        with tempfile.TemporaryDirectory() as folder:
            adapter=DesktopAdapter(Path(folder));adapter.config=Config()
            adapter.existing_mini=lambda:None;adapter.ensure_main=lambda:main
            adapter.mini_list_snapshot=Mock(return_value=page)
            adapter.click=Mock();adapter.text_click=Mock()
            with self.assertRaises(StopAutomation) as caught:adapter.open_mini()
            self.assertEqual('entry_unavailable',caught.exception.status)
            adapter.click.assert_not_called();adapter.text_click.assert_not_called()

    def test_real_failed_observation_opens_unique_common_target_without_reclicking_sidebar(self):
        main=WindowInfo(10,20,'微信','Qt',True,False,(0,0,1590,1138))
        items=replay_items();page=(main,Path('offline-only'),items)
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.time.sleep'):
            adapter=DesktopAdapter(Path(folder));adapter.config=Config()
            adapter.existing_mini=lambda:None;adapter.ensure_main=lambda:main
            adapter.mini_list_snapshot=Mock(return_value=page)
            adapter.text_click=Mock();adapter.click=Mock();adapter.wait_mini=lambda:main
            result=adapter.open_mini()
        self.assertEqual(main,result)
        adapter.text_click.assert_not_called()
        adapter.click.assert_called_once()
        clicked=adapter.click.call_args.args[1]
        self.assertEqual('永劫无间',clicked.text)
        self.assertGreater(clicked.center[1],400)

    def test_scrolled_list_in_separate_window_does_not_focus_blank_main(self):
        main=WindowInfo(10,20,'微信','Qt',True,False,(0,0,1590,1138))
        child=WindowInfo(11,21,'微信','Chrome_WidgetWin_0',True,False,(0,0,1590,1138))
        page=(child,Path('offline-only'),replay_items())
        with tempfile.TemporaryDirectory() as folder, patch('assistant.desktop.automation.win.enumerate_wechat_windows',return_value=[main,child]):
            adapter=DesktopAdapter(Path(folder));adapter.config=Config()
            adapter.snapshot=Mock(side_effect=[page,(main,Path('unused'),[])])
            self.assertEqual(page,adapter.mini_list_snapshot(main))
            adapter.snapshot.assert_called_once_with(child,'mini-list-window',inspect_only=True)
