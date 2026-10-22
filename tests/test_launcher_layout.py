import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'native/dist/GameInputBroker.exe'
CURRENT = ROOT / 'tests/fixtures/launcher-current.png'


class LauncherLayoutTests(unittest.TestCase):
    def score(self, path):
        result = subprocess.run([str(EXE), '--match', 'launcher_start', str(path)],
                                capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        return float(result.stdout)

    def check(self, path):
        result = subprocess.run([str(EXE), '--check-launcher', str(path)], capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        record = json.loads(result.stdout)
        self.assertFalse(record['input_sent'])
        return record

    def test_current_real_button_passes_without_lowering_threshold(self):
        self.assertGreater(self.score(CURRENT), .99)

    def test_legacy_control_and_its_original_anchor_remain_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.png'
            image = Image.new('RGB', (1538, 932), (25, 25, 25))
            with Image.open(ROOT / 'native/templates/launcher_start.png') as label:
                image.paste(label, (1237, 788))
            image.save(path)
            self.assertGreater(self.score(path), .99)
            result = self.check(path)
            self.assertTrue(result['allowed'])
            self.assertLess(abs(result['point_x'] - 1305), 6)

    def test_scaled_current_window_keeps_matching_at_the_scaled_anchor(self):
        with tempfile.TemporaryDirectory() as folder, Image.open(CURRENT) as image:
            path = Path(folder) / 'scaled.png'
            image.resize((1920, 1162)).save(path)
            self.assertGreater(self.score(path), .9)

    def test_current_click_point_stays_inside_the_observed_start_label(self):
        result = self.check(CURRENT)
        self.assertTrue(result['allowed'])
        self.assertGreaterEqual(result['point_x'], 1284)
        self.assertLessEqual(result['point_x'], 1407)
        self.assertGreaterEqual(result['point_y'], 796)
        self.assertLessEqual(result['point_y'], 831)
        self.assertGreaterEqual(result['match'], .78)

    def test_blank_and_distant_controls_are_rejected_by_both_layouts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'negative.png'
            image = Image.new('RGB', (1536, 930), (25, 25, 25))
            image.save(path)
            self.assertFalse(self.check(path)['allowed'])
            with Image.open(ROOT / 'native/templates/launcher_start_current.png') as label:
                image.paste(label, (1050, 786))
            image.save(path)
            self.assertFalse(self.check(path)['allowed'])

    def test_wrong_text_at_the_current_anchor_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'wrong.png'
            image = Image.new('RGB', (1536, 930), (25, 25, 25))
            with Image.open(ROOT / 'native/templates/launcher_start_current.png') as label:
                image.paste(ImageOps.mirror(label), (1277, 786))
            image.save(path)
            self.assertFalse(self.check(path)['allowed'])

    def test_new_broker_version_requires_the_updated_installed_binary(self):
        result = subprocess.run([str(EXE), '--version'], capture_output=True, timeout=5)
        self.assertEqual(0, result.returncode)
        self.assertEqual(b'5', result.stdout.strip())
