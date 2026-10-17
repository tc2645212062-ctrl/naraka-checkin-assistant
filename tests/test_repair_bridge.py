import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from assistant.config import BEIJING, Config
from assistant.repair_bridge import Journal, action, after_run, check, kick, write_json
from assistant.storage import Store


class RepairBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.instant = datetime(2026, 10, 17, 5, 10, tzinfo=BEIJING)
        self.config = Config()
        self.config.enabled_tasks = ['calendar', 'login', 'card', 'poster', 'read', 'key']
        write_json(self.data/'config.json', asdict(self.config))
        write_json(self.data/'repair/config.json', {'enabled': True,
                   'thread_id': '00000000-0000-4000-8000-000000000001', 'max_daily_jobs': 2})
        self.store = Store(self.data/'history.sqlite3')
        self.dashboard = {'settings': {'pausedAll': False}, 'tasks': [
            {'id': 'naraka_checkin', 'enabled': True, 'ownership': 'workbench', 'status': 'partial', 'nextRun': None}]}
        self.client = Mock()
        self.client.dashboard.side_effect = lambda: self.dashboard
        self.client.run.return_value = {'runId': 'workbench-retry', 'status': 'queued'}
        self.send = Mock(return_value='queue-receipt')

    def run_record(self, status='partial', key='unrecognized', finished=True, source='desktop', instant=None):
        instant = instant or self.instant
        run_id = self.store.create_run(instant.date().isoformat(), self.config.scope, instant.isoformat(), 'workbench-scheduled')
        for task in self.config.enabled_tasks:
            self.store.record_task(run_id, task, task, key if task=='key' else 'success', 'test result')
        if finished:
            self.store.finish(run_id, instant.isoformat(), status, 'test failure', source, complete=status in {'success','already_signed'})
        return run_id

    def poll(self, instant=None):
        return check(self.data, self.client, self.send, instant or self.instant)

    def job(self):
        return Journal(self.data/'repair').rows()[0]

    def test_ended_five_of_six_creates_one_report_and_one_message(self):
        self.run_record()
        self.assertEqual('queued', self.poll()['state'])
        self.assertEqual('already_queued_or_working', self.poll()['state'])
        self.send.assert_called_once()
        report=json.loads((self.data/'repair/reports'/f"{self.job()['id']}.json").read_text(encoding='utf-8'))
        self.assertEqual(6,len(report['snapshot']['steps']))

    def test_five_of_six_during_running_does_not_wake(self):
        self.run_record(finished=False)
        self.assertEqual('no_repair_needed',self.poll()['state'])
        self.send.assert_not_called()

    def test_verified_six_of_six_does_not_use_model(self):
        self.run_record(status='success',key='success')
        self.assertEqual('no_repair_needed',self.poll()['state'])
        self.send.assert_not_called()

    def test_disabled_subtask_is_not_counted_as_failure(self):
        self.config.enabled_tasks.remove('key')
        write_json(self.data/'config.json',asdict(self.config))
        self.run_record(status='success')
        self.assertEqual('no_repair_needed',self.poll()['state'])

    def test_paused_disabled_or_running_task_does_not_wake(self):
        self.run_record()
        variants=[('pause',True),('enabled',False),('status','running'),('status','queued'),('ownership','legacy')]
        for key,value in variants:
            original=json.loads(json.dumps(self.dashboard))
            if key=='pause':self.dashboard['settings']['pausedAll']=value
            else:self.dashboard['tasks'][0][key]=value
            self.assertEqual('paused_or_busy',self.poll()['state'])
            self.dashboard=original
        self.send.assert_not_called()

    def test_manual_expired_activity_and_waiting_do_not_request_code_changes(self):
        for status in ('manual_required','waiting_release','activity_missing','precondition','desktop_busy','interrupted'):
            self.run_record(key=status)
            self.assertEqual('no_repair_needed',self.poll()['state'],status)
        self.send.assert_not_called()

    def test_simulated_and_old_results_do_not_wake(self):
        self.run_record(source='simulation')
        self.assertEqual('no_repair_needed',self.poll()['state'])
        self.run_record(instant=self.instant-timedelta(days=1))
        self.assertEqual('no_repair_needed',self.poll()['state'])
        self.send.assert_not_called()

    def test_zero_of_six_real_workbench_exception_is_eligible(self):
        run_id=self.store.create_run(self.instant.date().isoformat(),self.config.scope,self.instant.isoformat(),'workbench-scheduled')
        self.store.finish(run_id,self.instant.isoformat(),'error','exception','adapter')
        self.assertEqual('queued',self.poll()['state'])

    def test_native_network_retry_is_given_priority(self):
        self.run_record(status='network_error',key='network_error')
        self.dashboard['tasks'][0]['nextRun']=(self.instant+timedelta(minutes=5)).isoformat()
        self.assertEqual('native_retry_pending',self.poll()['state'])
        self.send.assert_not_called()

    def test_transport_failure_is_persisted_and_not_retried_immediately(self):
        self.run_record()
        self.send.side_effect=TimeoutError('offline')
        self.assertEqual('delivery_pending',self.poll()['state'])
        self.assertEqual('already_queued_or_working',self.poll()['state'])
        self.assertEqual(1,self.send.call_count)
        self.send.side_effect=None
        self.assertEqual('queued',self.poll(self.instant+timedelta(minutes=31))['state'])
        self.assertEqual(2,self.job()['attempts'])

    def test_concurrent_checks_only_dispatch_once(self):
        self.run_record()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.poll(),range(2)))
        self.assertEqual(1,len(Journal(self.data/'repair').rows()))
        self.send.assert_called_once()

    def test_receipt_is_idempotent_and_blocks_parallel_repair(self):
        self.run_record(); self.poll(); job=self.job()
        first=action(self.data,'claim',job['id'],self.client,self.instant)
        second=action(self.data,'claim',job['id'],self.client,self.instant)
        self.assertTrue(first['claimed']); self.assertFalse(second['claimed'])
        self.run_record()
        self.poll(self.instant+timedelta(minutes=6))
        self.send.assert_called_once()

    def test_pause_after_delivery_prevents_claim_and_retry(self):
        self.run_record();self.poll();job=self.job()
        self.dashboard['settings']['pausedAll']=True
        self.assertFalse(action(self.data,'claim',job['id'],self.client,self.instant)['claimed'])
        self.assertEqual('cancelled',self.job()['phase'])
        action(self.data,'retry',job['id'],self.client,self.instant)
        self.client.run.assert_not_called()

    def test_retry_requires_claim_and_posts_only_once(self):
        self.run_record();self.poll();job=self.job()
        self.assertEqual('retry_already_submitted_or_unclaimed',action(self.data,'retry',job['id'],self.client,self.instant)['state'])
        action(self.data,'claim',job['id'],self.client,self.instant)
        self.assertEqual('retrying',action(self.data,'retry',job['id'],self.client,self.instant)['state'])
        self.assertEqual('retry_already_submitted_or_unclaimed',action(self.data,'retry',job['id'],self.client,self.instant)['state'])
        self.client.run.assert_called_once()

    def test_lost_post_response_never_blindly_resubmits(self):
        self.run_record();self.poll();job=self.job()
        action(self.data,'claim',job['id'],self.client,self.instant)
        self.client.run.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):action(self.data,'retry',job['id'],self.client,self.instant)
        self.assertEqual('retry_unknown',self.job()['phase'])
        action(self.data,'retry',job['id'],self.client,self.instant)
        self.client.run.assert_called_once()

    def test_finish_rejects_five_of_six_then_accepts_real_completion(self):
        self.run_record();self.poll();job=self.job()
        with self.assertRaises(ValueError):action(self.data,'finish',job['id'],self.client,self.instant)
        self.run_record(status='success',key='success')
        self.assertEqual('resolved',action(self.data,'finish',job['id'],self.client,self.instant)['state'])

    def test_completed_record_short_circuit_can_close_job(self):
        self.run_record();self.poll()
        self.run_record(status='success',key='success')
        run_id=self.store.create_run(self.instant.date().isoformat(),self.config.scope,self.instant.isoformat(),'workbench-manual')
        self.store.finish(run_id,self.instant.isoformat(),'already_signed','prior','desktop',complete=True)
        self.poll()
        self.assertEqual('resolved',self.job()['phase'])

    def test_cannot_finish_from_manual_or_simulated_success(self):
        self.run_record();self.poll();job=self.job()
        self.run_record(status='success',key='success',source='manual')
        with self.assertRaises(ValueError):action(self.data,'finish',job['id'],self.client,self.instant)

    def test_daily_limit_applies_to_distinct_failed_runs(self):
        for index in range(2):
            self.run_record();self.poll();job=self.job()
            action(self.data,'block',job['id'],self.client,self.instant,reason='test exhausted')
        self.run_record()
        self.assertEqual('deduplicated_or_daily_limit',self.poll()['state'])
        self.assertEqual(2,self.send.call_count)

    def test_quota_deferral_persists_until_reset(self):
        self.run_record();self.poll();job=self.job()
        action(self.data,'claim',job['id'],self.client,self.instant)
        due=self.instant+timedelta(hours=5)
        action(self.data,'defer',job['id'],self.client,self.instant,until=due.isoformat())
        self.poll(self.instant+timedelta(hours=1))
        self.assertFalse(action(self.data,'claim',job['id'],self.client,self.instant+timedelta(hours=1))['claimed'])
        self.send.assert_called_once()
        self.assertEqual('queued',self.poll(due)['state'])

    def test_expired_repair_lease_does_not_start_second_modifier(self):
        self.run_record();self.poll();job=self.job()
        action(self.data,'claim',job['id'],self.client,self.instant)
        self.assertEqual('repair_lease_expired_no_parallel_work',self.poll(self.instant+timedelta(hours=3))['state'])
        self.assertEqual('attention',self.job()['phase'])
        self.send.assert_called_once()

    def test_report_redacts_credentials_without_corrupting_json(self):
        run_id=self.run_record()
        self.store.log(run_id,self.instant.isoformat(),'token=abc123; Cookie=secret-cookie')
        self.poll()
        report=(self.data/'repair/reports'/f"{self.job()['id']}.json").read_text(encoding='utf-8')
        json.loads(report)
        self.assertNotIn('abc123',report);self.assertNotIn('secret-cookie',report)

    def test_native_heartbeat_prepares_without_queuing_itself(self):
        self.run_record()
        result=check(self.data,self.client,send=None,instant=self.instant)
        self.assertEqual('prepared',result['state'])
        self.assertEqual(0,self.job()['attempts'])
        self.assertTrue(action(self.data,'claim',result['job_id'],self.client,self.instant)['claimed'])

    def test_native_heartbeat_respects_quota_reset(self):
        self.run_record();self.poll();job=self.job()
        action(self.data,'claim',job['id'],self.client,self.instant)
        action(self.data,'defer',job['id'],self.client,self.instant,until=(self.instant+timedelta(hours=5)).isoformat())
        self.assertEqual('waiting_for_reset',check(self.data,self.client,send=None,instant=self.instant+timedelta(hours=1))['state'])

    def test_event_waits_for_workbench_finish_without_duplicate_launch(self):
        run_id=self.run_record()
        busy=json.loads(json.dumps(self.dashboard));busy['tasks'][0]['status']='running'
        self.client.dashboard.side_effect=[busy,self.dashboard,self.dashboard]
        with patch('assistant.repair_bridge.time.sleep') as sleep,patch('assistant.repair_bridge.dispatch',self.send),patch('assistant.repair_bridge.now',return_value=self.instant):
            # check's default dispatcher is bound at definition; replace check's
            # boundary so the test cannot reach the real Codex executable.
            with patch('assistant.repair_bridge.check',return_value={'state':'queued'}) as perform:
                self.assertEqual('queued',after_run(self.data,run_id,self.client)['state'])
        sleep.assert_called_once_with(3)
        perform.assert_called_once()

    def test_event_only_starts_delivery_for_real_incomplete_run(self):
        run_id=self.run_record()
        with patch('assistant.repair_bridge.subprocess.Popen') as launch,patch('assistant.repair_bridge.now',return_value=self.instant):
            self.assertTrue(kick(self.data,run_id))
            args=launch.call_args.args[0]
            self.assertIn('after-run',args)
            self.assertIn(str(run_id),args)
        run_id=self.run_record(status='success',key='success')
        with patch('assistant.repair_bridge.subprocess.Popen') as launch,patch('assistant.repair_bridge.now',return_value=self.instant):
            self.assertFalse(kick(self.data,run_id))
            launch.assert_not_called()

    def test_engine_event_occurs_after_cleanup_and_released_lock(self):
        from assistant.adapters import Result
        from assistant.engine import Engine
        engine=Engine(self.store,clock=lambda:self.instant)
        adapter=Mock();adapter.check.return_value=Result('unrecognized','test','desktop')
        order=[]
        adapter.close.side_effect=lambda:order.append('closed')
        def receive(*args):
            self.assertEqual(['closed'],order)
            self.assertFalse(engine.lock.locked())
            order.append('event')
        with patch('assistant.repair_bridge.kick',side_effect=receive):
            result=engine.run(self.config,adapter)
        self.assertEqual('unrecognized',result['status'])
        self.assertEqual(['closed','event'],order)

    def test_event_delivery_failure_cannot_change_business_result(self):
        from assistant.adapters import Result
        from assistant.engine import Engine
        adapter=Mock();adapter.check.return_value=Result('unrecognized','test','desktop')
        with patch('assistant.repair_bridge.kick',side_effect=RuntimeError('offline')):
            result=Engine(self.store,clock=lambda:self.instant).run(self.config,adapter)
        self.assertEqual('unrecognized',result['status'])
        self.assertIsNone(self.store.completion(self.instant.date().isoformat(),self.config.scope))

    def test_fast_receiver_claim_is_not_overwritten_by_queue_success(self):
        self.run_record()
        def send(*args):
            Journal(self.data/'repair').claim(self.job()['id'],self.instant)
            return 'receipt'
        check(self.data,self.client,send, self.instant)
        self.assertEqual('working',self.job()['phase'])

    def test_fast_receiver_claim_survives_lost_queue_receipt(self):
        self.run_record()
        def send(*args):
            Journal(self.data/'repair').claim(self.job()['id'],self.instant)
            raise TimeoutError('receipt lost after delivery')
        result=check(self.data,self.client,send,self.instant)
        self.assertEqual('claimed_during_delivery',result['state'])
        self.assertEqual('working',self.job()['phase'])


if __name__=='__main__':
    unittest.main()
