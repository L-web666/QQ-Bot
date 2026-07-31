import re
import time
from functools import wraps
from config import config
from privacy_log import privacy_manager, logger


class TokenExpiredError(Exception):
    """访问令牌已过期，需重新获取"""
    pass


# ===================== 重试装饰器 =====================
def retry(max_retries=None, delay=1):
    if max_retries is None:
        max_retries = config["RETRY_COUNT"]

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for i in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except TokenExpiredError:
                    raise
                except Exception as e:
                    if i == max_retries - 1:
                        raise e
                    err_detail = f"{type(e).__name__}: {str(e)[:100]}"
                    err_detail = privacy_manager.mask_sensitive_content(err_detail)
                    logger.warning(f"请求失败，{delay}秒后重试({i+1}/{max_retries}) | 错误：{err_detail}",
                                   extra={"user_id": kwargs.get("user_id", "system")})
                    await asyncio.sleep(delay)
        return wrapper
    return decorator


import asyncio


# ===================== 思考内容清理（已修复误删正文BUG） =====================
def remove_thinking(content: str) -> str:
    if not content:
        return ""

    # 第1层：清理成对的think标签、中文思考标签（有明确闭合标记，安全使用DOTALL）
    content = re.sub(r'<\s*think\s*>.*?</\s*think\s*>', '', content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r'<\s*/?\s*think\s*>', '', content, flags=re.IGNORECASE)
    content = re.sub(r'\[\s*思考\s*\].*?\[\s*/思考\s*\]', '', content, flags=re.DOTALL)

    think_keywords = r'(?:思考|总结|推理|思路|分析|解题|问题思考|思考过程|分析过程)'

    # 第2层：仅删除单行的加粗思考类标题，不跨行删除正文（修复误删BUG）
    content = re.sub(
        rf'\n?\*\*[^*\n]*?{think_keywords}[^*\n]*?\*\*[:：]?.*$',
        '',
        content,
        flags=re.MULTILINE | re.IGNORECASE
    )

    # 第3层：仅删除单行的三级问题标题，不跨行删除正文（修复误删BUG）
    content = re.sub(
        r'\n###\s+\*\*.*?(?:知道|请问|问题).*?\*\*.*$',
        '',
        content,
        flags=re.MULTILINE
    )

    # 第4层：逐行清理零散的思考标题行（仅删标题行，不删后续内容）
    lines = content.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if re.match(rf'^\*\*.*?{think_keywords}.*?\*\*[:：]?$', stripped, flags=re.IGNORECASE):
            continue
        cleaned_lines.append(line)
    content = '\n'.join(cleaned_lines)

    # 第5层：收尾清理多余空行
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = content.strip()
    return content


# ===================== 关键词匹配 =====================
def check_keyword(msg: str) -> str:
    msg_strip = msg.strip()
    msg_lower = msg_strip.lower()
    if msg_strip in config["KEYWORD_EXACT"]:
        logger.info(f"🔑 匹配精确关键词", extra={"user_id": "system"})
        return config["KEYWORD_EXACT"][msg_strip]
    for kw, reply in config["KEYWORD_EXACT"].items():
        if kw.lower() == msg_lower:
            logger.info(f"🔑 匹配精确关键词（大小写兼容）", extra={"user_id": "system"})
            return reply
    msg_lower_content = msg.lower()
    for keyword, reply in config["KEYWORD_FUZZY"].items():
        if keyword.lower() in msg_lower_content:
            logger.info(f"🔑 匹配模糊关键词", extra={"user_id": "system"})
            return config["KEYWORD_FUZZY"][keyword]
    return ""


# ===================== 敏感词检测（已修复中文失效BUG） =====================
def check_sensitive_word(msg: str, is_admin: bool) -> str:
    if is_admin:
        return ""
    # 仅保留中文、英文字母、数字，移除所有标点符号干扰
    cleaned_msg = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]+', '', msg).lower()
    for word in config["SENSITIVE_WORDS"]:
        if word.lower() in cleaned_msg:
            return "⚠️ 消息包含违规内容，已拒绝回复，请文明发言！"
    return ""
