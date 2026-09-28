import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from assistant.config import Config
from assistant.daily_scheduler import sync_daily_task,TASK_NAME

class DailySchedulerTests(unittest.TestCase):
    def test_daily_task_uses_normal_user_and_tracks_pause_and_time(self):
        for enabled,time in [(True,'05:00'),(False,'07:25')]:
            with self.subTest(enabled=enabled),tempfile.TemporaryDirectory(prefix='space path ') as folder:
                service=Mock();d=service.NewTask.return_value
                sync_daily_task(Config(enabled=enabled,daily_time=time),Path(folder),service=service)
                self.assertEqual(0,d.Principal.RunLevel)
                self.assertEqual(3,d.Principal.LogonType)
                self.assertEqual(enabled,d.Settings.Enabled)
                self.assertEqual(2,d.Settings.MultipleInstances)
                self.assertIn('T'+time+':00+08:00',d.Triggers.Create.return_value.StartBoundary)
                action=d.Actions.Create.return_value
                self.assertTrue(action.Path.endswith('pythonw.exe'))
                self.assertIn('--scheduled-start',action.Arguments)
                self.assertIn('"'+str(Path(folder).resolve())+'"',action.Arguments)
                registration=service.GetFolder.return_value.RegisterTaskDefinition.call_args.args
                self.assertEqual(TASK_NAME,registration[0]);self.assertIsNone(registration[4]);self.assertEqual(3,registration[5])
