"""Recognize only the observed optional WeChat update dialog."""
from .vision import find_text


def defer_update_button(items, width, height):
    header=find_text(items,'新版本',exact=True)
    later=find_text(items,'稍后处理',exact=True)
    ignore=find_text(items,'忽略本次更新',exact=True)
    update=find_text(items,'更新',exact=True)
    if not all(len(matches)==1 for matches in (header,later,ignore,update)):
        return None
    if not (header[0].center[1] < height*.25
            and height*.65 < later[0].center[1] < height
            and ignore[0].center[0] < later[0].center[0] < update[0].center[0]
            and abs(ignore[0].center[1]-later[0].center[1]) < height*.05
            and abs(update[0].center[1]-later[0].center[1]) < height*.05):
        return None
    return later[0]
