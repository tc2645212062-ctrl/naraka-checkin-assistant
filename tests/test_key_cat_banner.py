import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from assistant.desktop.key import extract_key_from_article_image
from assistant.desktop.models import OcrItem
from assistant.desktop.vision import read_text


IMAGE = Path(__file__).parent / 'fixtures/cat-key-banner.png'


def text(value, confidence, bounds):
    left, top, right, bottom = bounds
    return OcrItem(value, confidence, ((left, top), (right, top),
                                      (right, bottom), (left, bottom)))


class CatBannerTests(unittest.TestCase):
    def test_public_banner_reads_whole_answer(self):
        self.assertEqual('休闲一下', extract_key_from_article_image(IMAGE))

    def test_gray_whole_frame_must_agree_with_color(self):
        items = read_text(IMAGE)
        answer = next(i for i in items if i.text == '休闲一下')
        reader = Mock(return_value=([[answer.polygon, '不同口令', .95]], None))
        with patch('assistant.desktop.key.engine', return_value=reader):
            self.assertIsNone(extract_key_from_article_image(IMAGE, items))
        self.assertEqual(1, reader.call_count)

    def test_gray_confirmation_requires_high_confidence(self):
        items = read_text(IMAGE)
        answer = next(i for i in items if i.text == '休闲一下')
        reader = Mock(return_value=([[answer.polygon, answer.text, .74]], None))
        with patch('assistant.desktop.key.engine', return_value=reader):
            self.assertIsNone(extract_key_from_article_image(IMAGE, items))
        self.assertEqual(1, reader.call_count)

    def test_multiple_answers_on_anchor_row_are_ambiguous(self):
        items = read_text(IMAGE)
        items.append(text('其他口令', .95, (720, 660, 805, 720)))
        reader = Mock(return_value=([], None))
        with patch('assistant.desktop.key.engine', return_value=reader):
            self.assertIsNone(extract_key_from_article_image(IMAGE, items))
        reader.assert_not_called()

    def test_matching_body_text_is_not_a_banner_confirmation(self):
        items = read_text(IMAGE)
        unrelated = text('休闲一下', .99, (814, 900, 1165, 988))
        reader = Mock(return_value=([[unrelated.polygon, unrelated.text, .99]], None))
        with patch('assistant.desktop.key.engine', return_value=reader):
            self.assertIsNone(extract_key_from_article_image(IMAGE, items))
        self.assertEqual(1, reader.call_count)

    def test_missing_or_duplicate_anchor_never_submits(self):
        items = read_text(IMAGE)
        anchor = next(i for i in items if i.text == '今日金钥匙')
        reader = Mock()
        for page in ([i for i in items if i is not anchor], items + [anchor]):
            with patch('assistant.desktop.key.engine', return_value=reader):
                self.assertIsNone(extract_key_from_article_image(IMAGE, page))
        reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
