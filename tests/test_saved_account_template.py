from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'native/dist/GameInputBroker.exe'


@unittest.skipUnless(EXE.is_file(), 'Build-GameInput.ps1 required')
class SavedTemplateTests(unittest.TestCase):
    def score(self, crop=None, size=(2560,1600), offset=0):
        frame = Image.new('RGB', size, (50,50,50))
        if crop is not None:
            x,y,w,h = (round(value*size[i%2]/base) for i,(value,base) in enumerate(((1225,2560),(886,1600),(112,2560),(39,1600))))
            frame.paste(crop.resize((w,h)), (x+offset,y))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'frame.png';frame.save(path)
            result=subprocess.run([str(EXE),'--match','saved_account',str(path)],capture_output=True,timeout=5)
        self.assertEqual(0,result.returncode)
        return float(result.stdout)

    def test_current_observed_button_and_legacy_both_pass(self):
        for name in ('saved_account','saved_account_current'):
            with Image.open(ROOT/'native/templates'/f'{name}.png') as crop:
                self.assertGreaterEqual(self.score(crop),.78)

    def test_current_button_keeps_passing_at_recorded_scaled_size(self):
        with Image.open(ROOT/'native/templates/saved_account_current.png') as crop:
            self.assertGreaterEqual(self.score(crop,size=(1996,1248)),.78)

    def test_blank_and_distant_controls_are_rejected(self):
        self.assertLess(self.score(),.78)
        with Image.open(ROOT/'native/templates/saved_account_current.png') as crop:
            self.assertLess(self.score(crop,offset=100),.78)

    def test_cursor_without_label_cannot_pass(self):
        with Image.open(ROOT/'native/templates/saved_account_current.png') as source:
            crop=Image.new('RGB',source.size,(50,50,50))
            crop.paste(source.crop((48,0,77,39)),(48,0))
            self.assertLess(self.score(crop),.78)

    def test_wrong_button_label_is_rejected(self):
        crop=Image.new('RGB',(112,39),(50,50,50))
        font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',23)
        ImageDraw.Draw(crop).text((3,0),'取消登录',font=font,fill=(220,235,245))
        self.assertLess(self.score(crop),.78)
