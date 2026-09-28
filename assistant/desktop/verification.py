"""Distinguish live verification labels from a verified article's advice."""

TOKENS = ("验证码", "安全验证", "滑块", "扫码登录", "扫描二维码登录", "账号受限", "操作过于频繁")


def verification_prompt(items, verified_article=False):
    for token in TOKENS:
        for item in items:
            text = "".join(item.text.split())
            if token not in text:
                continue
            # Naraka's account-protection paragraph recommends QR login in
            # internet cafes. It isn't a live login form. Only this contextual
            # advice is exempt; standalone prompts and other challenges stop.
            if (verified_article and token == "扫码登录" and len(text) > 30
                    and "登录游戏时" in text and "使用扫码登录" in text):
                continue
            return token
    return None
