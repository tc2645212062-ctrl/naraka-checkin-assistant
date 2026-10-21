"""Recognize only the observed optional WeChat update dialog."""
from .vision import find_text


def defer_mini_restart_button(items, width, height):
    text = ''.join(''.join(item.text.split()) for item in items)
    if '检测到有新版本' not in text or '是否重启应用' not in text:
        return None
    cancel = find_text(items, '取消', exact=True)
    confirm = find_text(items, '确定', exact=True)
    if len(cancel) != 1 or len(confirm) != 1:
        raise ValueError('小程序重启提示的取消/确定按钮未唯一识别')
    left, right = cancel[0].center, confirm[0].center
    if not (width * .2 < left[0] < width * .5 < right[0] < width * .8
            and height * .25 < left[1] < height * .8
            and abs(left[1] - right[1]) < height * .04):
        raise ValueError('小程序重启提示的按钮位置不符合已验证布局')
    return cancel[0]


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
