import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'native/dist/GameInputBroker.exe'
GAME = r'E:\Naraka\program\NarakaBladepoint.exe'
LAUNCHER = r'E:\Naraka\tool\LootHoarder.exe'


@unittest.skipUnless(EXE.is_file(), 'Build-GameInput.ps1 required')
class NativeOwnershipTests(unittest.TestCase):
    def check(self, **changes):
        game = {'pid':200, 'born':1100, 'path':GAME, 'parent':100}
        launcher = {'pid':100, 'born':900, 'path':LAUNCHER, 'parent':1}
        probe = {'game':game, 'launcher':launcher, 'requested':1000,
                 'baseline':{}, 'ancestors':[dict(launcher)]}
        probe.update(changes)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'probe.json'
            path.write_text(json.dumps(probe), encoding='utf-8')
            checked = subprocess.run([str(EXE), '--check-owner', str(path)], capture_output=True, timeout=5)
        self.assertEqual(0, checked.returncode, checked.stderr.decode(errors='replace'))
        result = json.loads(checked.stdout)
        self.assertIs(False, result['input_sent'])
        return result['owned']

    def test_new_game_from_the_recorded_launcher_is_owned(self):
        self.assertTrue(self.check())

    def test_existing_user_game_is_never_owned(self):
        self.assertFalse(self.check(baseline={'200':1100}))

    def test_unreadable_existing_game_is_not_claimed(self):
        self.assertFalse(self.check(baseline={'200':0}))

    def test_game_created_before_accepted_launch_is_not_owned(self):
        self.assertFalse(self.check(requested=1200))

    def test_wrong_game_path_is_not_owned(self):
        self.assertFalse(self.check(game={'pid':200,'born':1100,'path':r'C:\other\NarakaBladepoint.exe','parent':100}))

    def test_wrong_launcher_path_is_not_owned(self):
        self.assertFalse(self.check(launcher={'pid':100,'born':900,'path':r'C:\other\LootHoarder.exe','parent':1}))

    def test_launcher_pid_reuse_does_not_match(self):
        self.assertFalse(self.check(ancestors=[{'pid':100,'born':901,'path':LAUNCHER,'parent':1}]))

    def test_unrelated_parent_chain_does_not_match(self):
        self.assertFalse(self.check(ancestors=[{'pid':999,'born':900,'path':LAUNCHER,'parent':1}]))

    def test_intermediate_child_must_connect_to_the_same_launcher(self):
        game = {'pid':200,'born':1100,'path':GAME,'parent':150}
        parent = {'pid':150,'born':1050,'path':r'E:\Naraka\program\helper.exe','parent':100}
        root = {'pid':100,'born':900,'path':LAUNCHER,'parent':1}
        self.assertTrue(self.check(game=game, ancestors=[parent,root]))
        parent['parent'] = 999
        self.assertFalse(self.check(game=game, ancestors=[parent,root]))

