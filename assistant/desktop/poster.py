"""Recognize the gallery's observed arrow label and explicit data-update wait."""
from .vision import normalize, combined_text


def gallery_share_button(items):
    matches=[item for item in items
             if normalize(item.text).lstrip('←→↗↖↑↓')=='分享藏品馆']
    return matches[0] if len(matches)==1 else None


def data_update_pending(items):
    text=normalize(combined_text(items))
    return '数据存在未生效的更新' in text and '请稍后再试' in text
