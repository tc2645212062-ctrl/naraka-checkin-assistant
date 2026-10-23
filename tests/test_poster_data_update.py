import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from assistant.config import Config
from assistant.desktop.automation import DesktopAdapter
from assistant.desktop.models import OcrItem, WindowInfo
from assistant.desktop.task_workflows import Snapshot, TaskWorkflows, PosterPageNotReady


def observation(labels=None):
    window=WindowInfo(1,2,'永劫无间','Chrome',True,False,(0,0,642,1180))
    raw=json.loads((Path(__file__).parent/'fixtures/poster-data-update.json').read_text(encoding='utf-8'))
    items=[OcrItem(row['text'],row['confidence'],tuple(map(tuple,row['polygon']))) for row in raw]
    if labels is not None:
        items=[OcrItem(label,.99,((100,50+i*45),(400,50+i*45),(400,75+i*45),(100,75+i*45))) for i,label in enumerate(labels)]
    return Snapshot('offline',window,Path('unused'),items)


class PosterDataUpdateTests(unittest.TestCase):
    def test_real_arrow_label_is_a_known_gallery_for_recovery(self):
        page=observation()
        self.assertEqual('藏品馆',DesktopAdapter.known_task_route((page.window,page.image,page.items)))

    def test_duplicate_share_or_quoted_body_is_not_a_known_route(self):
        for labels in (['藏品馆','←分享藏品馆','分享藏品馆'],['藏品馆','不要分享藏品馆']):
            page=observation(labels)
            self.assertIsNone(DesktopAdapter.known_task_route((page.window,page.image,page.items)))

    def test_visible_server_data_update_stops_wait_without_another_generation(self):
        driver=TaskWorkflows();driver.snapshot=Mock(return_value=observation())
        with patch('assistant.desktop.task_workflows.time.sleep'):
            with self.assertRaises(PosterPageNotReady) as caught:
                driver._wait_poster_page(observation().window,'poster',lambda _:False,'海报生成',attempts=20)
        self.assertEqual('precondition',caught.exception.status)
        self.assertIn('数据更新',str(caught.exception))
        driver.snapshot.assert_called_once()

    def test_gallery_already_showing_update_does_not_click_share(self):
        driver=TaskWorkflows();driver.snapshot=Mock(return_value=observation())
        driver.task_button=Mock(return_value=SimpleNamespace(text='去完成'))
        driver.click_item=Mock();driver.click_text=Mock()
        with patch('assistant.desktop.task_workflows.time.sleep'):
            with self.assertRaises(PosterPageNotReady):driver.complete_poster(observation())
        driver.click_text.assert_not_called()

    def test_server_wording_on_unknown_page_is_not_classified_as_gallery(self):
        page=observation(['文章标题','检测到数据存在未生效的更新，请稍后再试'])
        driver=TaskWorkflows();driver.snapshot=Mock(return_value=page)
        with self.assertRaises(PosterPageNotReady) as caught:
            driver._wait_poster_page(page.window,'poster',lambda _:False,'海报生成',attempts=1)
        self.assertEqual('entry_unavailable',caught.exception.status)

    def test_verified_recovery_keeps_waiting_poster_but_completes_other_task(self):
        page=observation(); config=Config(enabled_tasks=['poster','read'])
        with tempfile.TemporaryDirectory() as folder:
            adapter=DesktopAdapter(Path(folder));adapter.config=config;adapter.guard=Mock()
            adapter.window=page.window;adapter.welfare=Mock(return_value=(page.window,page.image,page.items))
            adapter.snapshot=Mock(return_value=(page.window,page.image,page.items));adapter.restore_task_route=Mock()
            adapter.results={task:('not_signed','') for task in config.enabled_tasks}
            driver=Mock();done={'read':False}
            driver.task_button.side_effect=lambda _,label:SimpleNamespace(text='已完成' if label==config.task_labels['read'] and done['read'] else '去完成')
            driver.complete_reading.side_effect=lambda _:done.update(read=True)
            driver.complete_poster.side_effect=PosterPageNotReady('藏品数据更新尚未生效',status='precondition')
            with patch('assistant.desktop.workflows.WorkflowDriver',return_value=driver):result=adapter.sign(config)
            self.assertEqual('partial',result.status)
            self.assertEqual('precondition',adapter.results['poster'][0])
            self.assertEqual('success',adapter.results['read'][0])
            adapter.restore_task_route.assert_called_once();driver.complete_poster.assert_called_once()
