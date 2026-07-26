import asyncio
import websockets
import aiohttp
import json
import time
import re
import logging
import os
import base64
from datetime import datetime
from typing import Dict, List, Set, Tuple, Optional
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from functools import wraps
from aiohttp import ClientResponseError, ClientError, web

# ===================== 基础路径定义 =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_README_PATH = os.path.join(BASE_DIR, "config配置说明.txt")
DEFAULT_CONTEXTS_PATH = os.path.join(BASE_DIR, "contexts.json")


# 生成本次启动专属日志文件
def get_new_log_file() -> str:
    """自动创建logs目录，返回带启动时间的日志文件路径"""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
    time_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_name = f"{time_str}.txt"
    return os.path.join(LOG_DIR, log_name)


# ===================== 日志系统 =====================
class UserIdLogFilter(logging.Filter):
    """给所有日志自动补充user_id字段，避免格式化时报错"""
    def filter(self, record):
        if not hasattr(record, "user_id"):
            record.user_id = "system"
        return True


# 每次启动生成全新独立日志文件
current_log_file = get_new_log_file()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [用户%(user_id)s] %(message)s",
    handlers=[
        logging.FileHandler(current_log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
_log_filter = UserIdLogFilter()
for _handler in logging.root.handlers:
    _handler.addFilter(_log_filter)
logger = logging.getLogger(__name__)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
# 启动时打印日志路径，方便定位
logger.info(f"📄 本次启动日志文件：{current_log_file}", extra={"user_id": "system"})

# ===================== 默认内置配置 =====================
DEFAULT_CONFIG = {
    "APPID": "你的APPID",
    "APPSECRET": "你的APPSECRET",
    "OLLAMA_URL": "http://127.0.0.1:11434/api/chat",
    "DEFAULT_MODEL": "deepseek-r1:1.5b",
    "MAX_CONTEXT_LENGTH": 10,
    "RETRY_COUNT": 3,
    "OLLAMA_TIMEOUT": 300,
    "GLOBAL_SYSTEM_PROMPT": "你是友好的中文AI助手，请全程使用简体中文回答，不要夹杂英文单词。回答要简洁准确，不知道的内容直接说明，不要编造虚假信息。",
    "ENABLE_GROUP_AT_FILTER": True,
    "MAX_RECONNECT": 10,
    "RECONNECT_DELAY": 10,
    "MESSAGE_MAX_LENGTH": 1500,
    "HEARTBEAT_RATIO": 0.85,
    "ADMIN_IDS": ["管理员OpenID1", "管理员OpenID2"],
    "KEYWORD_EXACT": {
        "你好": "你好呀！😊 我是你的AI助手，有什么可以帮你的吗？",
        "帮助": "📖 我可以帮你做这些事：\n1. 聊天解答各种问题\n2. 写代码/写文案/写报告\n3. 中英文翻译\n\n📌 常用指令：\n- /清空上下文：重置当前对话\n- /查看上下文：查看对话状态\n\n📌 管理员指令：\n- /查看模型：列出所有可用模型\n- /切换模型 模型名：切换到指定模型",
        "菜单": "📋 功能菜单：\n- 聊天：直接发送问题即可\n- /清空上下文：重置对话历史\n- 帮助：查看完整帮助信息"
    },
    "KEYWORD_FUZZY": {
        "谢谢": "不客气！😊 有问题随时找我~",
        "再见": "再见！👋 下次再聊~",
        "晚安": "晚安！🌙 做个好梦~"
    },
    "SENSITIVE_WORDS": ["违规词示例", "敏感测试"],
    "RATE_LIMIT_SECONDS": 10,
    "RATE_LIMIT_MAX_COUNT": 3,
    "ADMIN_BYPASS_LIMIT": True,
    "ENABLE_REPORT": True,
    "REPORT_BACKEND_URL": "http://127.0.0.1:54188",
    "REPORT_TIMEOUT": 3,
    "REPORT_BODY_MAX_LENGTH": 500,
    "ENABLE_WEB_ADMIN": True,
    "WEB_ADMIN_PORT": 8080,
    "WEB_ADMIN_USERNAME": "admin",
    "WEB_ADMIN_PASSWORD": "admin123",
    "WEB_ADMIN_ALLOW_REMOTE": False,
    "ENABLE_CONTEXT_PERSISTENCE": True,
    "CONTEXT_SAVE_INTERVAL": 30
}
config = DEFAULT_CONFIG.copy()


class TokenExpiredError(Exception):
    """访问令牌已过期，需重新获取"""
    pass


# ===================== 配置生成函数 =====================
def generate_default_config(config_path: str = DEFAULT_CONFIG_PATH) -> bool:
    """自动生成标准config.json"""
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)
        print(f"[自动创建] 已生成默认配置文件：{config_path}")
        return True
    except Exception as e:
        print(f"[错误] 自动创建配置文件失败：{e}")
        print(f"[错误] 请检查文件夹写入权限：{os.path.dirname(config_path)}")
        return False


def generate_config_readme(readme_path: str = DEFAULT_README_PATH) -> bool:
    """自动生成配置说明文档"""
    readme_content = '''========================================
QQ机器人 配置文件详细说明
修改config.json后重启程序即可生效
========================================
一、基础必填配置
--------------------------------------------------
APPID
  说明：QQ开放平台申请到的机器人AppID，必填项
  格式：字符串，纯数字
  示例："123456789"
APPSECRET
  说明：QQ开放平台申请到的机器人密钥，必填项
  格式：字符串，字母数字混合
  注意：请勿泄露给他人
OLLAMA_URL
  说明：本地Ollama服务的对话接口地址
  默认值："http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL
  说明：机器人启动时默认使用的大模型名称
  要求：必须是Ollama已下载的模型
MAX_CONTEXT_LENGTH
  说明：单个用户最多保留的对话轮数
  单位：轮，默认值：10
RETRY_COUNT
  说明：网络请求失败后的自动重试次数，默认值：3
OLLAMA_TIMEOUT
  说明：Ollama模型推理请求超时时间，单位秒，默认300
GLOBAL_SYSTEM_PROMPT
  说明：AI全局人设/回答规则，所有对话全程生效
二、高级运行配置
--------------------------------------------------
ENABLE_GROUP_AT_FILTER
  说明：群聊是否只有被@时才回复，true/false
MAX_RECONNECT
  说明：连接断开后最大自动重连次数，默认10
RECONNECT_DELAY
  说明：每次重连之间的等待时间，单位秒，默认10
MESSAGE_MAX_LENGTH
  说明：单条消息最大字符数，超出自动分段
HEARTBEAT_RATIO
  说明：心跳间隔系数，建议0.8-0.9
三、管理员配置
--------------------------------------------------
ADMIN_IDS
  说明：管理员用户OpenID列表，可使用高级指令
四、关键词回复配置
--------------------------------------------------
KEYWORD_EXACT
  说明：精确匹配关键词回复，完全一致才触发
KEYWORD_FUZZY
  说明：模糊匹配关键词回复，包含即触发
五、安全防护配置
--------------------------------------------------
SENSITIVE_WORDS
  说明：敏感词列表，命中后拒绝回复
RATE_LIMIT_SECONDS
  说明：防刷屏限流的时间窗口，单位秒
RATE_LIMIT_MAX_COUNT
  说明：时间窗口内最多允许的消息条数
ADMIN_BYPASS_LIMIT
  说明：管理员是否不受限流约束，true/false
六、后台管理网站配置
--------------------------------------------------
ENABLE_WEB_ADMIN
  说明：是否开启Web后台管理页面，默认true
WEB_ADMIN_PORT
  说明：后台管理网站端口号，默认8080
WEB_ADMIN_USERNAME
  说明：后台登录用户名，默认admin
WEB_ADMIN_PASSWORD
  说明：后台登录密码，默认admin123
WEB_ADMIN_ALLOW_REMOTE
  说明：是否允许外网访问后台，默认false
七、后台上报配置
--------------------------------------------------
ENABLE_REPORT
  说明：是否开启HTTP请求上报，默认false
REPORT_BACKEND_URL
  说明：后台接收上报数据的接口地址
REPORT_TIMEOUT
  说明：上报请求超时时间，单位秒
REPORT_BODY_MAX_LENGTH
  说明：上报内容最大长度，超出截断
八、对话持久化配置
--------------------------------------------------
ENABLE_CONTEXT_PERSISTENCE
  说明：是否开启对话上下文持久化，重启后自动恢复，默认true
CONTEXT_SAVE_INTERVAL
  说明：上下文自动保存到磁盘的间隔时间，单位秒，默认30
  注意：设置过短会增加磁盘IO，过长可能丢失少量最新对话
========================================
修改注意事项
1. 所有字符串用英文双引号包裹
2. 列表最后一项后面不要加逗号
3. 布尔值使用JSON原生格式：true/false（不加引号，不用大写）
4. 修改后保存文件，重启程序生效
========================================
'''
    try:
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme_content)
        print(f"[自动创建] 已生成配置说明文档：{readme_path}")
        return True
    except Exception as e:
        print(f"[错误] 自动创建说明文档失败：{e}")
        return False


# ===================== 加载配置文件 =====================
def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> bool:
    """加载配置文件，不存在则自动生成；加载后同步运行时变量"""
    global config, current_model, max_context_length
    if not os.path.exists(config_path):
        print(f"[提示] 未找到配置文件，路径：{config_path}")
        print("[提示] 正在自动生成默认配置和说明文档...")
        gen_ok = generate_default_config(config_path)
        generate_config_readme()
        if not gen_ok:
            print("[警告] 配置文件生成失败，将使用内置默认配置启动")
            return False
        print("[提示] 配置文件已生成，请打开上述路径的config.json填写APPID和APPSECRET")
        print("[提示] 配置项说明可查看同目录的 config配置说明.txt")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config.update(user_config)
        current_model = config["DEFAULT_MODEL"]
        max_context_length = config["MAX_CONTEXT_LENGTH"]
        print(f"[成功] 配置文件加载完成，路径：{config_path}")
        return True
    except json.JSONDecodeError as e:
        print(f"[错误] 配置文件格式错误：{e}")
        print("[提示] 请检查双引号、逗号是否正确，不要使用中文标点")
        print("[警告] 将使用内置默认配置启动")
        return False
    except Exception as e:
        print(f"[错误] 读取配置文件失败：{e}")
        print("[警告] 将使用内置默认配置启动")
        return False


# ===================== 全局运行时变量 =====================
user_contexts: Dict[str, List[Dict]] = {}
processed_messages: Set[str] = set()
user_rate_limit: Dict[str, Tuple[float, int]] = {}
bot_id: str = ""
current_model: str = config["DEFAULT_MODEL"]
max_context_length: int = config["MAX_CONTEXT_LENGTH"]
web_admin_running = False
bot_online_status = False
# 全局token状态字典，供异步任务中的send_msg检查和刷新
token_state: Dict[str, object] = {"value": "", "expire": 0}
contexts_dirty = False

# ===================== 对话持久化 =====================
def save_contexts(filepath: str = DEFAULT_CONTEXTS_PATH) -> bool:
    """将当前所有用户对话上下文保存到JSON文件"""
    global contexts_dirty
    try:
        data = {
            "save_time": int(time.time()),
            "model": current_model,
            "contexts": {uid: msgs for uid, msgs in user_contexts.items()}
        }
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
        contexts_dirty = False
        logger.debug(f"💾 已保存 {len(user_contexts)} 个用户的对话上下文", extra={"user_id": "system"})
        return True
    except Exception as e:
        logger.error(f"保存对话上下文失败：{e}", extra={"user_id": "system"})
        return False


def load_contexts(filepath: str = DEFAULT_CONTEXTS_PATH) -> bool:
    """从JSON文件加载之前保存的用户对话上下文"""
    global user_contexts, contexts_dirty
    if not config.get("ENABLE_CONTEXT_PERSISTENCE", False):
        logger.info("ℹ️ 对话持久化已关闭，跳过加载", extra={"user_id": "system"})
        return False
    if not os.path.exists(filepath):
        logger.info("ℹ️ 未找到对话上下文文件，将从空记录开始", extra={"user_id": "system"})
        save_contexts(filepath)
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved_contexts = data.get("contexts", {})
        if not isinstance(saved_contexts, dict):
            logger.warning("上下文文件格式异常，跳过加载", extra={"user_id": "system"})
            return False
        user_contexts.clear()
        loaded_count = 0
        for uid, msgs in saved_contexts.items():
            if isinstance(msgs, list) and len(msgs) > 0:
                user_contexts[uid] = msgs
                loaded_count += 1
        contexts_dirty = False
        save_time = data.get("save_time", 0)
        save_age = int(time.time() - save_time) if save_time else 0
        logger.info(f"💾 已加载 {loaded_count} 个用户的对话上下文（上次保存于 {save_age} 秒前）", extra={"user_id": "system"})
        return True
    except json.JSONDecodeError as e:
        logger.error(f"对话上下文文件格式错误：{e}", extra={"user_id": "system"})
        return False
    except Exception as e:
        logger.error(f"加载对话上下文失败：{e}", extra={"user_id": "system"})
        return False


async def periodic_flush_contexts():
    """定期将对话上下文刷盘，仅在内容变化时写入"""
    while True:
        try:
            interval = config.get("CONTEXT_SAVE_INTERVAL", 30)
            await asyncio.sleep(interval)
            if contexts_dirty and config.get("ENABLE_CONTEXT_PERSISTENCE", False):
                save_contexts()
        except asyncio.CancelledError:
            if contexts_dirty and config.get("ENABLE_CONTEXT_PERSISTENCE", False):
                save_contexts()
            break
        except Exception as e:
            logger.error(f"定期保存上下文异常：{e}", extra={"user_id": "system"})
            await asyncio.sleep(10)


# ===================== HTTP请求上报函数 =====================
async def report_http_request(
    session: aiohttp.ClientSession,
    scene: str,
    method: str,
    url: str,
    req_body: Optional[str] = None,
    resp_status: Optional[int] = None,
    resp_body: Optional[str] = None,
    cost_ms: Optional[float] = None,
    user_id: str = "system"
):
    if not config["ENABLE_REPORT"]:
        return
    try:
        req_body_snippet = req_body[:config["REPORT_BODY_MAX_LENGTH"]] if req_body else ""
        resp_body_snippet = resp_body[:config["REPORT_BODY_MAX_LENGTH"]] if resp_body else ""
        payload = {
            "timestamp": int(time.time() * 1000),
            "scene": scene,
            "method": method.upper(),
            "url": url,
            "request_body": req_body_snippet,
            "response_status": resp_status,
            "response_body": resp_body_snippet,
            "cost_ms": round(cost_ms, 2) if cost_ms else None,
            "user_id": user_id
        }

        async def _do_report():
            try:
                timeout = aiohttp.ClientTimeout(total=config["REPORT_TIMEOUT"])
                async with session.post(
                    config["REPORT_BACKEND_URL"],
                    json=payload,
                    timeout=timeout
                ) as res:
                    logger.debug(f"【后台上报】场景:{scene} 状态:{res.status}", extra={"user_id": "system"})
            except Exception:
                pass
        asyncio.create_task(_do_report())
    except Exception:
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
                    logger.warning(f"请求失败，{delay}秒后重试({i+1}/{max_retries}) | 错误：{err_detail}",
                                   extra={"user_id": kwargs.get("user_id", "system")})
                    await asyncio.sleep(delay)
        return wrapper
    return decorator


# ===================== 工具函数 =====================
def remove_thinking(content: str) -> str:
    if not content:
        return ""

    # ========== 第1层：清理所有think标签（成对/单独残留都清） ==========
    # 成对的think标签
    content = re.sub(r'<\s*think\s*>.*?</\s*think\s*>', '', content, flags=re.DOTALL | re.IGNORECASE)
    # 单独残留的开头/闭合标签（兜底不配对的情况）
    content = re.sub(r'<\s*/?\s*think\s*>', '', content, flags=re.IGNORECASE)
    # 中文方括号思考标记
    content = re.sub(r'\[\s*思考\s*\].*?\[\s*/思考\s*\]', '', content, flags=re.DOTALL)

    # ========== 第2层：清理所有思考/总结/分析类标题+对应内容 ==========
    # 匹配所有加粗的思考类标题（总结、思考过程、问题思考、推理、思路、分析等）
    think_keywords = r'(?:思考|总结|推理|思路|分析|解题|问题思考|思考过程|分析过程)'
    # 匹配 **标题: ** 格式，连同后面的内容一起删除，直到遇到下一个大标题或正文段落
    content = re.sub(
        rf'\n?\*\*[^*\n]*?{think_keywords}[^*\n]*?\*\*[:：]?.*?(?=\n\n|\Z)',
        '',
        content,
        flags=re.DOTALL | re.IGNORECASE
    )

    # ========== 第3层：清理三级标题类的重复问题 ==========
    # 匹配 ### 开头的、包含“你知道/请问/问题”等的重复问题标题及后续内容
    content = re.sub(
        r'\n###\s+\*\*.*?(?:知道|请问|问题).*?\*\*.*?(?=\n{2,}|\Z)',
        '',
        content,
        flags=re.DOTALL
    )

    # ========== 第4层：清理零散的思考行碎片 ==========
    lines = content.split('\n')
    cleaned_lines = []
    skip_next = False
    for line in lines:
        stripped = line.strip()
        # 跳过纯思考标题行
        if re.match(rf'^\*\*.*?{think_keywords}.*?\*\*[:：]?$', stripped, flags=re.IGNORECASE):
            skip_next = True  # 连带着下一行内容也跳过
            continue
        if skip_next:
            if stripped == '':
                skip_next = False
            continue
        cleaned_lines.append(line)
    content = '\n'.join(cleaned_lines)

    # ========== 第5层：收尾清理 ==========
    # 清理多余空行
    content = re.sub(r'\n{3,}', '\n\n', content)
    # 清理行首行尾空白
    content = content.strip()

    return content


def check_keyword(msg: str) -> str:
    msg_strip = msg.strip()
    msg_lower = msg_strip.lower()
    if msg_strip in config["KEYWORD_EXACT"]:
        logger.info(f"🔑 匹配精确关键词：{msg_strip}", extra={"user_id": "system"})
        return config["KEYWORD_EXACT"][msg_strip]
    for kw, reply in config["KEYWORD_EXACT"].items():
        if kw.lower() == msg_lower:
            logger.info(f"🔑 匹配精确关键词（大小写兼容）：{kw}", extra={"user_id": "system"})
            return reply
    msg_lower_content = msg.lower()
    for keyword, reply in config["KEYWORD_FUZZY"].items():
        if keyword.lower() in msg_lower_content:
            logger.info(f"🔑 匹配模糊关键词：{keyword}", extra={"user_id": "system"})
            return config["KEYWORD_FUZZY"][keyword]
    return ""


def check_sensitive_word(msg: str, is_admin: bool) -> str:
    if is_admin:
        return ""
    cleaned_msg = re.sub(r'[\s\W_]+', '', msg).lower()
    for word in config["SENSITIVE_WORDS"]:
        if word.lower() in cleaned_msg:
            return "⚠️ 消息包含违规内容，已拒绝回复，请文明发言！"
    return ""


def check_rate_limit(user_id: str, is_admin: bool) -> str:
    if is_admin and config["ADMIN_BYPASS_LIMIT"]:
        return ""
    global user_rate_limit
    now = time.time()
    # 每次检查时顺便清理过期条目，防止内存泄漏
    expired_keys = [uid for uid, (t, _) in user_rate_limit.items()
                    if now - t > config["RATE_LIMIT_SECONDS"]]
    for uid in expired_keys:
        del user_rate_limit[uid]
    if user_id not in user_rate_limit:
        user_rate_limit[user_id] = (now, 1)
        return ""
    first_time, count = user_rate_limit[user_id]
    if now - first_time > config["RATE_LIMIT_SECONDS"]:
        user_rate_limit[user_id] = (now, 1)
        return ""
    new_count = count + 1
    user_rate_limit[user_id] = (first_time, new_count)
    if new_count > config["RATE_LIMIT_MAX_COUNT"]:
        return f"⚠️ 发言过快，请等待 {config['RATE_LIMIT_SECONDS']} 秒后再试！"
    return ""


# 全局token刷新函数，供send_msg在异步任务中调用
@retry(max_retries=2, delay=1)
async def refresh_token_if_needed(session: aiohttp.ClientSession):
    """检查全局token是否即将过期，提前300秒刷新，避免会话超时"""
    # 剩余5分钟即刷新，防止token过期导致网关会话失效
    if time.time() < token_state["expire"] - 300:
        return
    new_token, new_expire = await get_token(session)
    token_state["value"] = new_token
    token_state["expire"] = new_expire
    logger.info("🔄 预刷新访问令牌，避免会话超时", extra={"user_id": "system"})


# ===================== 网络请求函数 =====================
@retry(max_retries=3, delay=1)
async def send_msg(session: aiohttp.ClientSession, token: str, event_type: str,
                   openid: str = None, guild_id: str = None, channel_id: str = None,
                   content: str = None, user_id: str = "unknown",
                   msg_id: str = None, group_openid: str = None) -> bool:
    # 发送前检查token是否过期，过期则自动刷新
    if time.time() > token_state["expire"]:
        try:
            await refresh_token_if_needed(session)
            token = token_state["value"]
        except Exception:
            logger.warning("send_msg中token刷新失败，使用传入的token继续尝试", extra={"user_id": user_id})

    headers = {
        "Authorization": f"QQBot {token}",
        "Content-Type": "application/json"
    }
    target_info = ""
    url = ""
    start_time = time.time()

    def build_payload(use_msg_id: bool) -> dict:
        """构造请求体，可控制是否携带msg_id"""
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            payload = {"msg_type": 0, "content": content}
        elif event_type == "C2C_MESSAGE_CREATE":
            payload = {"msg_type": 0, "content": content}
        elif event_type == "DIRECT_MESSAGE_CREATE":
            payload = {"content": content}
        else:
            payload = {"content": content}
        if use_msg_id and msg_id:
            payload["msg_id"] = msg_id
        return payload

    async def _do_send(payload: dict) -> Tuple[bool, Optional[int], Optional[str]]:
        """执行单次发送，返回 (是否成功, 错误码, 错误信息)"""
        try:
            req_body_str = json.dumps(payload, ensure_ascii=False)
            async with session.post(url, headers=headers, json=payload, timeout=10) as res:
                cost_ms = (time.time() - start_time) * 1000
                resp_text = await res.text()
                await report_http_request(
                    session, "send_msg", "POST", url,
                    req_body=req_body_str,
                    resp_status=res.status,
                    resp_body=resp_text,
                    cost_ms=cost_ms,
                    user_id=user_id
                )
                if res.status in (200, 201):
                    return True, None, None
                # 解析错误码
                err_code = None
                err_msg = ""
                try:
                    resp_json = json.loads(resp_text)
                    err_code = resp_json.get("code") or resp_json.get("err_code")
                    err_msg = resp_json.get("message", "")
                except Exception:
                    err_msg = resp_text[:200]
                return False, err_code, err_msg
        except ClientResponseError as e:
            return False, None, str(e)
        except ClientError as e:
            raise  # 网络错误抛出，走外层重试

    # 构造目标地址
    try:
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            if not group_openid:
                raise ValueError("群消息缺少group_openid参数")
            url = f"https://api.sgroup.qq.com/v2/groups/{group_openid}/messages"
            target_info = f"群聊目标:{group_openid}"
        elif event_type == "C2C_MESSAGE_CREATE":
            if not openid:
                raise ValueError("C2C消息缺少openid参数")
            url = f"https://api.sgroup.qq.com/v2/users/{openid}/messages"
            target_info = f"私聊目标:{openid}"
        elif event_type == "DIRECT_MESSAGE_CREATE":
            if not guild_id:
                raise ValueError("私信消息缺少guild_id参数")
            url = f"https://api.sgroup.qq.com/v2/dms/{guild_id}/messages"
            target_info = f"私信频道:{guild_id}"
        else:
            if not channel_id:
                raise ValueError("群消息缺少channel_id参数")
            url = f"https://api.sgroup.qq.com/v2/channels/{channel_id}/messages"
            target_info = f"群聊频道:{channel_id}"
    except ValueError as e:
        logger.error(f"【消息参数错误】{target_info} | {e}", extra={"user_id": user_id})
        return False

    # 第一步：带msg_id发送（回复引用）
    success, err_code, err_msg = await _do_send(build_payload(use_msg_id=True))
    if success:
        return True

    # 第二步：如果是去重错误，自动移除msg_id重试一次
    if err_code == 40054005:
        logger.warning(f"【消息触发去重】{target_info}，自动移除msg_id重试", extra={"user_id": user_id})
        success, err_code, err_msg = await _do_send(build_payload(use_msg_id=False))
        if success:
            return True

    # 第三步：判断是否为不可重试的业务错误
    unrecoverable_codes = {
        40034105,  # 机器人无发言权限
        40054005,  # 消息去重
        40013002,  # 参数错误
        401,       # 未授权
        403        # 无权限
    }
    if err_code in unrecoverable_codes or (err_code is None and isinstance(err_msg, str) and "400" in err_msg):
        logger.error(f"【消息发送失败】{target_info} | 错误码:{err_code} | 原因:{err_msg}", extra={"user_id": user_id})
        return False

    # 其他错误（网络异常、服务端5xx等）抛出异常，走外层重试
    raise Exception(f"发送失败，错误码:{err_code}，原因:{err_msg}")


async def send_long_message(session: aiohttp.ClientSession, token: str, event_type: str,
                           openid: str = None, guild_id: str = None, channel_id: str = None,
                           content: str = None, user_id: str = "unknown",
                           msg_id: str = None, group_openid: str = None) -> bool:
    if len(content) <= config["MESSAGE_MAX_LENGTH"]:
        return await send_msg(
            session, token, event_type,
            openid, guild_id, channel_id, content, user_id,
            msg_id=msg_id, group_openid=group_openid
        )

    # 分段处理
    chunks = []
    current_chunk = ""
    lines = content.split('\n')
    for line in lines:
        if len(current_chunk) + len(line) + 1 <= config["MESSAGE_MAX_LENGTH"]:
            current_chunk += (line + '\n')
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = line + '\n'
    if current_chunk:
        chunks.append(current_chunk.strip())

    success_count = 0
    for i, chunk in enumerate(chunks):
        chunk_content = f"【{i+1}/{len(chunks)}】\n{chunk}" if len(chunks) > 1 else chunk
        # 仅第一段携带msg_id做回复引用，后续分段不带，避免去重
        current_msg_id = msg_id if i == 0 else None
        if await send_msg(
            session, token, event_type,
            openid, guild_id, channel_id, chunk_content, user_id,
            msg_id=current_msg_id, group_openid=group_openid
        ):
            success_count += 1
            await asyncio.sleep(0.5)
    return success_count == len(chunks)

@retry()
async def get_token(session: aiohttp.ClientSession) -> Tuple[str, int]:
    url = "https://bots.qq.com/app/getAppAccessToken"
    data = {"appId": config["APPID"], "clientSecret": config["APPSECRET"]}
    start_time = time.time()
    try:
        req_body_str = json.dumps(data, ensure_ascii=False)
        async with session.post(url, json=data, timeout=10) as res:
            cost_ms = (time.time() - start_time) * 1000
            resp_text = await res.text()
            await report_http_request(
                session, "get_token", "POST", url,
                req_body=req_body_str,
                resp_status=res.status,
                resp_body=resp_text,
                cost_ms=cost_ms
            )
            if res.status != 200:
                logger.error(f"【获取令牌失败】HTTP状态码:{res.status} | 响应:{resp_text[:200]}",
                             extra={"user_id": "system"})
            res.raise_for_status()
            result = await res.json()
            access_token = result["access_token"]
            try:
                expires_in = int(result.get("expires_in", 7200))
            except (ValueError, TypeError):
                expires_in = 7200
                logger.warning("【令牌时长异常】接口返回expires_in非数字，已使用默认7200秒", extra={"user_id": "system"})
            expire_time = time.time() + expires_in - 300
            return access_token, expire_time
    except ClientResponseError as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_token", "POST", url,
            req_body=req_body_str,
            resp_status=e.status,
            resp_body=str(e),
            cost_ms=cost_ms
        )
        logger.error(f"【获取令牌HTTP错误】状态码:{e.status} | 详情:{str(e)}",
                     extra={"user_id": "system"})
        raise e
    except Exception as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_token", "POST", url,
            req_body=req_body_str,
            resp_status=None,
            resp_body=str(e),
            cost_ms=cost_ms
        )
        logger.error(f"【获取令牌异常】错误类型:{type(e).__name__} | 详情:{str(e)}",
                     extra={"user_id": "system"})
        raise e


@retry(max_retries=2, delay=1)
async def get_gateway(session: aiohttp.ClientSession, token: str) -> str:
    url = "https://api.sgroup.qq.com/gateway"
    headers = {"Authorization": f"QQBot {token}"}
    start_time = time.time()
    try:
        async with session.get(url, headers=headers, timeout=10) as res:
            cost_ms = (time.time() - start_time) * 1000
            resp_text = await res.text()
            await report_http_request(
                session, "get_gateway", "GET", url,
                resp_status=res.status,
                resp_body=resp_text,
                cost_ms=cost_ms
            )
            if res.status == 500 and "token not exist or expire" in resp_text:
                logger.warning("【网关获取失败】令牌已过期，准备刷新后重试", extra={"user_id": "system"})
                raise TokenExpiredError("access token expired")
            if res.status != 200:
                logger.error(f"【获取网关失败】HTTP状态码:{res.status} | 响应:{resp_text[:200]}",
                             extra={"user_id": "system"})
            res.raise_for_status()
            return (await res.json())["url"]
    except TokenExpiredError:
        raise
    except ClientResponseError as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_gateway", "GET", url,
            resp_status=e.status,
            resp_body=str(e),
            cost_ms=cost_ms
        )
        logger.error(f"【获取网关HTTP错误】状态码:{e.status} | 详情:{str(e)}",
                     extra={"user_id": "system"})
        raise e
    except Exception as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_gateway", "GET", url,
            resp_status=None,
            resp_body=str(e),
            cost_ms=cost_ms
        )
        logger.error(f"【获取网关异常】错误类型:{type(e).__name__} | 详情:{str(e)}",
                     extra={"user_id": "system"})
        raise e


@retry()
async def get_available_models(session: aiohttp.ClientSession) -> List[str]:
    url = "http://127.0.0.1:11434/api/tags"
    start_time = time.time()
    try:
        async with session.get(url, timeout=5) as res:
            cost_ms = (time.time() - start_time) * 1000
            resp_text = await res.text()
            await report_http_request(
                session, "get_models", "GET", url,
                resp_status=res.status,
                resp_body=resp_text,
                cost_ms=cost_ms
            )
            if res.status != 200:
                logger.error(f"【获取模型列表失败】Ollama状态码:{res.status} | 响应:{resp_text[:200]}",
                             extra={"user_id": "system"})
            res.raise_for_status()
            models = [m["name"] for m in (await res.json())["models"]]
            return models
    except ClientResponseError as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_models", "GET", url,
            resp_status=e.status,
            resp_body=str(e),
            cost_ms=cost_ms
        )
        logger.error(f"【获取模型列表HTTP错误】状态码:{e.status} | 详情:{str(e)}",
                     extra={"user_id": "system"})
        raise e
    except ClientError as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_models", "GET", url,
            resp_status=None,
            resp_body=str(e),
            cost_ms=cost_ms
        )
        logger.error(f"【Ollama连接失败】无法连接本地Ollama服务 | 错误:{str(e)}",
                     extra={"user_id": "system"})
        raise e
    except Exception as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_models", "GET", url,
            resp_status=None,
            resp_body=str(e),
            cost_ms=cost_ms
        )
        logger.error(f"【获取模型列表异常】错误类型:{type(e).__name__} | 详情:{str(e)}",
                     extra={"user_id": "system"})
        raise e


# ===================== 核心AI回复逻辑 =====================
@retry()
async def ollama_reply(session: aiohttp.ClientSession, user_id: str, msg: str, is_admin: bool) -> str:
    global current_model, max_context_length, user_contexts
    limit_tip = check_rate_limit(user_id, is_admin)
    if limit_tip:
        logger.warning(f"用户触发限流：{limit_tip}", extra={"user_id": user_id})
        return limit_tip
    sensitive_tip = check_sensitive_word(msg, is_admin)
    if sensitive_tip:
        logger.warning(f"检测到敏感词，内容：{msg[:50]}", extra={"user_id": user_id})
        return sensitive_tip

    clear_commands = ["/清空上下文", "/重置对话", "/clear", "/清除上下文"]
    if msg.strip() in clear_commands:
        if user_id in user_contexts:
            user_contexts[user_id] = [{"role": "system", "content": config["GLOBAL_SYSTEM_PROMPT"]}]
            contexts_dirty = True
            return "✅ 你的上下文已成功清空，可以开始新的对话了"
        else:
            return "ℹ️ 你还没有任何对话记录，无需清空"

    context_commands = ["/查看上下文", "/上下文状态", "/context"]
    if msg.strip() in context_commands:
        if user_id in user_contexts and len(user_contexts[user_id]) > 1:
            rounds = (len(user_contexts[user_id]) - 1) // 2
            return f"📊 当前上下文状态：\n- 对话轮数：{rounds}轮\n- 最大保留：{max_context_length}轮\n- 当前总消息数：{len(user_contexts[user_id])-1}条"
        else:
            return "ℹ️ 你还没有任何对话记录"

    if is_admin:
        model_list_commands = ["/查看模型", "/模型列表", "/models"]
        if msg.strip() in model_list_commands:
            try:
                models = await get_available_models(session)
                model_list = "\n".join([f"- {model}" for model in models])
                return f"🤖 可用模型列表：\n{model_list}\n\n当前使用：{current_model}"
            except Exception as e:
                logger.error(f"获取模型列表失败：{e}", extra={"user_id": user_id})
                return "❌ 无法获取模型列表，请检查Ollama服务是否正常"

        if msg.startswith(("/切换模型", "/switch")):
            try:
                if msg.startswith("/切换模型"):
                    target_model = msg.split("/切换模型")[1].strip()
                else:
                    target_model = msg.split("/switch")[1].strip()
                if not target_model:
                    return "❌ 格式错误，请使用：/切换模型 模型名"
                models = await get_available_models(session)
                if target_model not in models:
                    return f"❌ 模型 {target_model} 不存在\n可用模型：\n{chr(10).join(models)}"
                current_model = target_model
                # 使用list()创建键的副本，避免遍历字典时修改
                for uid in list(user_contexts):
                    user_contexts[uid] = [{"role": "system", "content": config["GLOBAL_SYSTEM_PROMPT"]}]
                contexts_dirty = True
                logger.info(f"🔄 管理员{user_id}已切换模型为：{current_model}", extra={"user_id": user_id})
                return f"✅ 已成功切换模型为：{current_model}\n所有用户上下文已自动清空"
            except Exception as e:
                logger.error(f"切换模型失败：{e}", extra={"user_id": user_id})
                return "❌ 切换模型失败，请检查Ollama服务是否正常"

        current_model_commands = ["/当前模型", "/current"]
        if msg.strip() in current_model_commands:
            return f"🤖 当前使用的模型：{current_model}\n📌 默认模型：{config['DEFAULT_MODEL']}"

        clear_all_commands = ["/清空所有上下文", "/清除所有上下文", "/清空全部上下文", "/全局清空", "/clear all"]
        if msg.strip() in clear_all_commands:
            user_contexts.clear()
            contexts_dirty = True
            logger.info(f"🔧 管理员{user_id}清空了所有用户上下文", extra={"user_id": user_id})
            return "✅ 管理员操作：所有用户的上下文已全部清空"

        if msg.startswith(("/清空用户上下文", "/清除用户上下文")):
            try:
                if msg.startswith("/清空用户上下文"):
                    target_id = msg.split("/清空用户上下文")[1].strip()
                else:
                    target_id = msg.split("/清除用户上下文")[1].strip()
                if not target_id:
                    return "❌ 格式错误，请使用：/清空用户上下文 用户ID"
                if target_id in user_contexts:
                    user_contexts[target_id] = [{"role": "system", "content": config["GLOBAL_SYSTEM_PROMPT"]}]
                    contexts_dirty = True
                    logger.info(f"🔧 管理员{user_id}清空了用户{target_id}的上下文", extra={"user_id": user_id})
                    return f"✅ 管理员操作：用户 {target_id} 的上下文已清空"
                else:
                    return f"ℹ️ 用户 {target_id} 没有对话记录"
            except Exception:
                return "❌ 格式错误，请使用：/清空用户上下文 用户ID"

        if msg.strip() in ["/查看在线用户", "/用户数", "/在线人数", "/查看在线人数"]:
            user_count = len(user_contexts)
            return f"👥 当前有对话记录的用户数：{user_count}个\n用户ID列表：{', '.join(user_contexts.keys()) if user_contexts else '无'}"

        if msg.startswith("/设置最大上下文"):
            try:
                new_limit = int(msg.split("/设置最大上下文")[1].strip())
                if new_limit < 1 or new_limit > 50:
                    return "❌ 最大上下文范围需在1-50之间"
                max_context_length = new_limit
                logger.info(f"🔧 管理员{user_id}将最大上下文设置为{new_limit}轮", extra={"user_id": user_id})
                return f"✅ 已将单用户最大上下文设置为：{new_limit}轮"
            except Exception:
                return "❌ 格式错误，请使用：/设置最大上下文 数字（1-50）"

    keyword_reply = check_keyword(msg)
    if keyword_reply:
        return keyword_reply

    if re.fullmatch(r'(<faceType[^>]+>)+', msg.strip()):
        return "😊 收到你的表情啦～有什么问题可以直接对我说哦"
    if re.fullmatch(r'[0-9a-zA-Z\s]+', msg.strip()) and len(msg.strip()) < 3:
        return "请输入完整的问题，我会尽力为你解答~"

    if user_id not in user_contexts:
        user_contexts[user_id] = [{"role": "system", "content": config["GLOBAL_SYSTEM_PROMPT"]}]
    user_contexts[user_id].append({"role": "user", "content": msg})

    if len(user_contexts[user_id]) > max_context_length * 2 + 1:
        system_msg = user_contexts[user_id][0]
        rest_msgs = user_contexts[user_id][1:]
        rest_msgs = rest_msgs[-max_context_length * 2:]
        user_contexts[user_id] = [system_msg] + rest_msgs
        logger.info(f"🔄 用户{user_id}上下文超出限制，已截断至{max_context_length}轮", extra={"user_id": user_id})

    start_time = time.time()
    try:
        payload = {"model": current_model, "messages": user_contexts[user_id], "stream": False}
        req_body_str = json.dumps(payload, ensure_ascii=False)
        async with session.post(
            config["OLLAMA_URL"],
            json=payload,
            timeout=config["OLLAMA_TIMEOUT"]
        ) as res:
            cost_ms = (time.time() - start_time) * 1000
            resp_text = await res.text()
            await report_http_request(
                session, "ollama_chat", "POST", config["OLLAMA_URL"],
                req_body=req_body_str,
                resp_status=res.status,
                resp_body=resp_text,
                cost_ms=cost_ms,
                user_id=user_id
            )
            if res.status != 200:
                logger.error(f"【Ollama请求失败】状态码:{res.status} | 响应:{resp_text[:200]}",
                             extra={"user_id": user_id})
            res.raise_for_status()
            result = await res.json()
            if "message" not in result or "content" not in result["message"]:
                logger.error(f"【Ollama返回格式错误】返回字段缺失 | 原始数据:{str(result)[:200]}",
                             extra={"user_id": user_id})
                raise Exception("Ollama返回格式错误，缺少message/content字段")
            raw_reply = result["message"]["content"]
            reply = remove_thinking(raw_reply)
            if not reply or len(reply.strip()) == 0:
                reply = raw_reply.strip()
            if not reply or len(reply.strip()) == 0:
                reply = "我收到了你的消息，但暂时无法给出有效回复，请换个问题试试~"
            user_contexts[user_id].append({"role": "assistant", "content": reply})
            contexts_dirty = True
            return reply
    except ClientResponseError as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "ollama_chat", "POST", config["OLLAMA_URL"],
            resp_status=e.status,
            resp_body=str(e),
            cost_ms=cost_ms,
            user_id=user_id
        )
        if user_contexts.get(user_id) and user_contexts[user_id][-1]["role"] == "user":
            user_contexts[user_id].pop()
        logger.error(f"【Ollama HTTP错误】状态码:{e.status} | 用户消息:{msg[:50]} | 详情:{str(e)}",
                     exc_info=True, extra={"user_id": user_id})
        raise e
    except ClientError as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "ollama_chat", "POST", config["OLLAMA_URL"],
            resp_status=None,
            resp_body=str(e),
            cost_ms=cost_ms,
            user_id=user_id
        )
        if user_contexts.get(user_id) and user_contexts[user_id][-1]["role"] == "user":
            user_contexts[user_id].pop()
        logger.error(f"【Ollama连接错误】无法连接本地服务 | 详情:{str(e)}",
                     exc_info=True, extra={"user_id": user_id})
        raise e
    except Exception as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "ollama_chat", "POST", config["OLLAMA_URL"],
            resp_status=None,
            resp_body=str(e),
            cost_ms=cost_ms,
            user_id=user_id
        )
        if user_contexts.get(user_id) and user_contexts[user_id][-1]["role"] == "user":
            user_contexts[user_id].pop()
        logger.error(f"【Ollama回复异常】错误类型:{type(e).__name__} | 详情:{str(e)}",
                     exc_info=True, extra={"user_id": user_id})
        raise e


# ===================== 后台管理网站系统 =====================
@web.middleware
async def error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPNotFound:
        return web.json_response(
            {"success": False, "msg": "URL拼写可能存在错误，请检查"},
            status=404
        )
    except Exception as e:
        logger.error(f"后台接口异常：{type(e).__name__}: {e}", exc_info=True, extra={"user_id": "web_admin"})
        return web.json_response(
            {"success": False, "msg": f"服务器内部错误：{str(e)}"},
            status=500
        )


def is_config_hot_updateable(key: str) -> bool:
    hot_keys = {
        "MAX_CONTEXT_LENGTH", "RETRY_COUNT", "OLLAMA_TIMEOUT",
        "GLOBAL_SYSTEM_PROMPT", "ENABLE_GROUP_AT_FILTER",
        "MESSAGE_MAX_LENGTH", "HEARTBEAT_RATIO", "ADMIN_IDS",
        "KEYWORD_EXACT", "KEYWORD_FUZZY", "SENSITIVE_WORDS",
        "RATE_LIMIT_SECONDS", "RATE_LIMIT_MAX_COUNT", "ADMIN_BYPASS_LIMIT",
        "ENABLE_REPORT", "REPORT_BACKEND_URL", "REPORT_TIMEOUT", "REPORT_BODY_MAX_LENGTH",
        "WEB_ADMIN_USERNAME", "WEB_ADMIN_PASSWORD", "DEFAULT_MODEL",
        "ENABLE_CONTEXT_PERSISTENCE", "CONTEXT_SAVE_INTERVAL"
    }
    return key in hot_keys


def apply_hot_config():
    global current_model, max_context_length
    current_model = config["DEFAULT_MODEL"]
    max_context_length = config["MAX_CONTEXT_LENGTH"]
    for uid in user_contexts:
        if user_contexts[uid] and user_contexts[uid][0]["role"] == "system":
            user_contexts[uid][0]["content"] = config["GLOBAL_SYSTEM_PROMPT"]


def check_auth(request: web.Request) -> bool:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
        return (username == config["WEB_ADMIN_USERNAME"] and
                password == config["WEB_ADMIN_PASSWORD"])
    except Exception:
        return False


def auth_required(handler):
    @wraps(handler)
    async def wrapper(request):
        if not check_auth(request):
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": 'Basic realm="QQBot Admin"'},
                text="需要登录"
            )
        return await handler(request)
    return wrapper


# API接口
@auth_required
async def api_get_config(request: web.Request):
    safe_config = config.copy()
    _MASKED_SECRET = "__MASKED_APPSECRET__"
    _MASKED_PASSWORD = "__MASKED_PASSWORD__"
    safe_config["APPSECRET"] = _MASKED_SECRET if safe_config["APPSECRET"] != "你的APPSECRET" else safe_config["APPSECRET"]
    safe_config["WEB_ADMIN_PASSWORD"] = _MASKED_PASSWORD
    return web.json_response(safe_config)


@auth_required
async def api_update_config(request: web.Request):
    try:
        new_config = await request.json()
    except Exception:
        return web.json_response({"success": False, "msg": "请求格式错误"}, status=400)
    if not isinstance(new_config, dict):
        return web.json_response({"success": False, "msg": "配置格式错误"}, status=400)

    _MASKED_SECRET = "__MASKED_APPSECRET__"
    _MASKED_PASSWORD = "__MASKED_PASSWORD__"
    if new_config.get("APPSECRET") == _MASKED_SECRET:
        new_config["APPSECRET"] = config["APPSECRET"]
    if new_config.get("WEB_ADMIN_PASSWORD") == _MASKED_PASSWORD:
        new_config["WEB_ADMIN_PASSWORD"] = config["WEB_ADMIN_PASSWORD"]

    hot_updated = []
    need_restart = []
    for key, value in new_config.items():
        if key in config:
            old_value = config[key]
            config[key] = value
            if old_value != value:
                if is_config_hot_updateable(key):
                    hot_updated.append(key)
                else:
                    need_restart.append(key)
    if hot_updated:
        apply_hot_config()
    try:
        with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
    except Exception as e:
        return web.json_response({"success": False, "msg": f"保存文件失败：{str(e)}"}, status=500)
    return web.json_response({
        "success": True,
        "msg": "配置已保存",
        "hot_updated": hot_updated,
        "need_restart": need_restart
    })


@auth_required
async def api_get_status(request: web.Request):
    return web.json_response({
        "bot_online": bot_online_status,
        "bot_id": bot_id or "未连接",
        "current_model": current_model or "未加载",
        "max_context": max_context_length,
        "user_count": len(user_contexts),
        "processed_msg_count": len(processed_messages),
        "ollama_url": config["OLLAMA_URL"],
        "group_at_filter": config["ENABLE_GROUP_AT_FILTER"],
        "report_enabled": config["ENABLE_REPORT"]
    })


@auth_required
async def api_clear_all_context(request: web.Request):
    global contexts_dirty
    user_contexts.clear()
    contexts_dirty = True
    logger.info("【后台管理】已清空所有用户上下文", extra={"user_id": "web_admin"})
    return web.json_response({"success": True, "msg": "所有上下文已清空"})


# 管理页面
async def admin_page(request: web.Request):
    if not check_auth(request):
        return web.Response(
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="QQBot Admin"'},
            text="需要登录"
        )
    auth_header = request.headers.get("Authorization", "")
    html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QQ机器人 后台管理</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #f5f7fa; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px 30px; border-radius: 10px 10px 0 0; }
        .header h1 { font-size: 22px; margin-bottom: 5px; }
        .header p { opacity: 0.9; font-size: 14px; }
        .status-bar { background: white; padding: 15px 30px; border-bottom: 1px solid #eee; display: flex; gap: 30px; flex-wrap: wrap; }
        .status-item { display: flex; align-items: center; gap: 8px; font-size: 14px; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; background: #ccc; }
        .status-dot.online { background: #52c41a; box-shadow: 0 0 8px #52c41a; }
        .main { display: grid; grid-template-columns: 200px 1fr; gap: 0; background: white; border-radius: 0 0 10px 10px; min-height: 600px; }
        .sidebar { border-right: 1px solid #eee; padding: 20px 0; }
        .sidebar-item { padding: 12px 24px; cursor: pointer; font-size: 14px; border-left: 3px solid transparent; }
        .sidebar-item:hover { background: #f5f7fa; }
        .sidebar-item.active { background: #e6f7ff; border-left-color: #1890ff; color: #1890ff; font-weight: 500; }
        .content { padding: 30px; }
        .panel { display: none; }
        .panel.active { display: block; }
        .panel h2 { font-size: 18px; margin-bottom: 20px; color: #333; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; font-weight: 500; color: #333; font-size: 14px; }
        .form-group .desc { font-size: 12px; color: #999; margin-bottom: 8px; }
        .form-group input[type="text"],
        .form-group input[type="number"],
        .form-group textarea,
        .form-group select {
            width: 100%; padding: 10px 12px; border: 1px solid #d9d9d9; border-radius: 6px;
            font-size: 14px; font-family: inherit; transition: border-color 0.3s;
        }
        .form-group input:focus, .form-group textarea:focus { outline: none; border-color: #1890ff; }
        .form-group textarea { min-height: 100px; resize: vertical; }
        .checkbox-group { display: flex; align-items: center; gap: 8px; }
        .checkbox-group input { width: 16px; height: 16px; }
        .json-editor { width: 100%; min-height: 300px; font-family: Consolas, monospace; font-size: 13px; }
        .btn {
            padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer;
            font-size: 14px; transition: all 0.3s;
        }
        .btn-primary { background: #1890ff; color: white; }
        .btn-primary:hover { background: #40a9ff; }
        .btn-danger { background: #ff4d4f; color: white; }
        .btn-danger:hover { background: #ff7875; }
        .btn-group { display: flex; gap: 12px; margin-top: 20px; }
        .hint { padding: 12px 16px; background: #fffbe6; border: 1px solid #ffe58f; border-radius: 6px; margin-bottom: 20px; font-size: 13px; color: #d48806; }
        .success-msg { padding: 12px 16px; background: #f6ffed; border: 1px solid #b7eb8f; border-radius: 6px; margin-bottom: 20px; color: #52c41a; display: none; }
        .section-title { font-size: 16px; font-weight: 600; margin: 25px 0 15px 0; padding-bottom: 8px; border-bottom: 1px solid #f0f0f0; color: #333; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 QQ机器人 后台管理系统</h1>
            <p>本地Ollama驱动 · 可视化配置管理</p>
        </div>
        <div class="status-bar" id="statusBar">
            <div class="status-item">
                <span class="status-dot" id="statusDot"></span>
                <span>机器人状态：<span id="botStatus">加载中...</span></span>
            </div>
            <div class="status-item">当前模型：<span id="currentModel">-</span></div>
            <div class="status-item">在线用户：<span id="userCount">-</span></div>
        </div>
        <div class="main">
            <div class="sidebar">
                <div class="sidebar-item active" onclick="switchPanel('basic', this)">基础配置</div>
                <div class="sidebar-item" onclick="switchPanel('advanced', this)">高级配置</div>
                <div class="sidebar-item" onclick="switchPanel('keyword', this)">关键词回复</div>
                <div class="sidebar-item" onclick="switchPanel('security', this)">安全防护</div>
                <div class="sidebar-item" onclick="switchPanel('admin', this)">管理员设置</div>
                <div class="sidebar-item" onclick="switchPanel('raw', this)">原始JSON编辑</div>
            </div>
            <div class="content">
                <div class="success-msg" id="successMsg"></div>
                <div class="panel active" id="panel-basic">
                    <h2>基础配置</h2>
                    <div class="hint">修改 APPID / APPSECRET / Ollama地址 后需要重启程序生效</div>
                    <div class="form-group">
                        <label>APPID</label>
                        <div class="desc">QQ开放平台申请的机器人AppID</div>
                        <input type="text" id="cfg-APPID">
                    </div>
                    <div class="form-group">
                        <label>APPSECRET</label>
                        <div class="desc">QQ开放平台申请的机器人密钥</div>
                        <input type="text" id="cfg-APPSECRET">
                    </div>
                    <div class="form-group">
                        <label>Ollama服务地址</label>
                        <div class="desc">本地Ollama的chat接口地址</div>
                        <input type="text" id="cfg-OLLAMA_URL">
                    </div>
                    <div class="form-group">
                        <label>默认模型</label>
                        <div class="desc">必须是Ollama已下载的模型，修改后立即生效</div>
                        <input type="text" id="cfg-DEFAULT_MODEL">
                    </div>
                    <div class="form-group">
                        <label>全局系统提示词</label>
                        <div class="desc">AI的人设和回答规则，所有对话生效，修改后立即生效</div>
                        <textarea id="cfg-GLOBAL_SYSTEM_PROMPT"></textarea>
                    </div>
                    <div class="form-group">
                        <label>最大上下文轮数</label>
                        <div class="desc">单个用户最多保留的对话轮数，修改后立即生效</div>
                        <input type="number" id="cfg-MAX_CONTEXT_LENGTH" min="1" max="50">
                    </div>
                    <div class="form-group">
                        <label>Ollama推理超时时间（秒）</label>
                        <input type="number" id="cfg-OLLAMA_TIMEOUT" min="10">
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-primary" onclick="saveBasicConfig()">保存配置</button>
                    </div>
                </div>
                <div class="panel" id="panel-advanced">
                    <h2>高级运行配置</h2>
                    <div class="form-group">
                        <div class="checkbox-group">
                            <input type="checkbox" id="cfg-ENABLE_GROUP_AT_FILTER">
                            <label for="cfg-ENABLE_GROUP_AT_FILTER">群聊仅被@时回复（推荐开启）</label>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>单条消息最大字符数</label>
                        <div class="desc">超出后自动分段发送</div>
                        <input type="number" id="cfg-MESSAGE_MAX_LENGTH" min="100">
                    </div>
                    <div class="form-group">
                        <label>最大重连次数</label>
                        <input type="number" id="cfg-MAX_RECONNECT" min="1">
                    </div>
                    <div class="form-group">
                        <label>重连等待时间（秒）</label>
                        <input type="number" id="cfg-RECONNECT_DELAY" min="1">
                    </div>
                    <div class="form-group">
                        <label>心跳间隔系数</label>
                        <div class="desc">官方间隔 × 系数 = 实际发送间隔，建议0.8-0.9</div>
                        <input type="number" id="cfg-HEARTBEAT_RATIO" step="0.01" min="0.5" max="1">
                    </div>
                    <div class="form-group">
                        <label>网络请求重试次数</label>
                        <input type="number" id="cfg-RETRY_COUNT" min="0" max="10">
                    </div>
                    <div class="section-title">后台上报配置</div>
                    <div class="form-group">
                        <div class="checkbox-group">
                            <input type="checkbox" id="cfg-ENABLE_REPORT">
                            <label for="cfg-ENABLE_REPORT">开启HTTP请求后台上报</label>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>上报接口地址</label>
                        <input type="text" id="cfg-REPORT_BACKEND_URL">
                    </div>
                    <div class="form-group">
                        <label>上报超时时间（秒）</label>
                        <input type="number" id="cfg-REPORT_TIMEOUT" min="1">
                    </div>
                    <div class="form-group">
                        <label>上报内容最大长度</label>
                        <input type="number" id="cfg-REPORT_BODY_MAX_LENGTH" min="100">
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-primary" onclick="saveAdvancedConfig()">保存配置</button>
                    </div>
                </div>
                <div class="panel" id="panel-keyword">
                    <h2>关键词回复配置</h2>
                    <div class="hint">修改后立即生效。精确匹配需完全一致，模糊匹配只要包含关键词即触发。</div>
                    <div class="section-title">精确匹配关键词</div>
                    <div class="form-group">
                        <textarea id="cfg-KEYWORD_EXACT" class="json-editor"></textarea>
                        <div class="desc">JSON格式，键为关键词，值为回复内容</div>
                    </div>
                    <div class="section-title">模糊匹配关键词</div>
                    <div class="form-group">
                        <textarea id="cfg-KEYWORD_FUZZY" class="json-editor"></textarea>
                        <div class="desc">JSON格式，键为关键词，值为回复内容</div>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-primary" onclick="saveKeywordConfig()">保存配置</button>
                    </div>
                </div>
                <div class="panel" id="panel-security">
                    <h2>安全防护配置</h2>
                    <div class="form-group">
                        <label>敏感词列表</label>
                        <div class="desc">每行一个词，命中后拒绝回复，修改后立即生效</div>
                        <textarea id="cfg-SENSITIVE_WORDS" style="min-height: 150px;"></textarea>
                    </div>
                    <div class="section-title">防刷屏限流</div>
                    <div class="form-group">
                        <label>限流时间窗口（秒）</label>
                        <input type="number" id="cfg-RATE_LIMIT_SECONDS" min="1">
                    </div>
                    <div class="form-group">
                        <label>窗口内最大消息数</label>
                        <input type="number" id="cfg-RATE_LIMIT_MAX_COUNT" min="1">
                    </div>
                    <div class="form-group">
                        <div class="checkbox-group">
                            <input type="checkbox" id="cfg-ADMIN_BYPASS_LIMIT">
                            <label for="cfg-ADMIN_BYPASS_LIMIT">管理员不受限流约束</label>
                        </div>
                    </div>
                    <div class="section-title">快捷操作</div>
                    <div class="btn-group">
                        <button class="btn btn-danger" onclick="clearAllContext()">清空所有用户上下文</button>
                    </div>
                    <div class="btn-group" style="margin-top: 20px;">
                        <button class="btn btn-primary" onclick="saveSecurityConfig()">保存配置</button>
                    </div>
                </div>
                <div class="panel" id="panel-admin">
                    <h2>管理员设置</h2>
                    <div class="form-group">
                        <label>后台管理用户名</label>
                        <input type="text" id="cfg-WEB_ADMIN_USERNAME">
                    </div>
                    <div class="form-group">
                        <label>后台管理密码</label>
                        <input type="text" id="cfg-WEB_ADMIN_PASSWORD">
                    </div>
                    <div class="form-group">
                        <label>QQ管理员OpenID列表</label>
                        <div class="desc">每行一个ID，这些用户可使用机器人的管理员指令</div>
                        <textarea id="cfg-ADMIN_IDS" style="min-height: 120px;"></textarea>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-primary" onclick="saveAdminConfig()">保存配置</button>
                    </div>
                </div>
                <div class="panel" id="panel-raw">
                    <h2>原始JSON编辑</h2>
                    <div class="hint">高级用户使用，直接编辑完整配置JSON。错误格式可能导致程序异常。</div>
                    <div class="form-group">
                        <textarea id="rawJson" class="json-editor" style="min-height: 500px;"></textarea>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-primary" onclick="saveRawConfig()">保存完整配置</button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
        let currentConfig = {};
        const _authHeader = "''' + auth_header + '''";
        function authFetch(url, options) {
            options = options || {};
            options.headers = options.headers || {};
            options.headers['Authorization'] = _authHeader;
            return fetch(url, options);
        }
        function switchPanel(name, el) {
            document.querySelectorAll('.sidebar-item').forEach(function(item) { item.classList.remove('active'); });
            document.querySelectorAll('.panel').forEach(function(item) { item.classList.remove('active'); });
            el.classList.add('active');
            document.getElementById('panel-' + name).classList.add('active');
        }
        async function loadConfig() {
            const res = await authFetch('/api/config');
            currentConfig = await res.json();
            renderConfig();
        }
        function renderConfig() {
            document.getElementById('cfg-APPID').value = currentConfig.APPID || '';
            document.getElementById('cfg-APPSECRET').value = currentConfig.APPSECRET || '';
            document.getElementById('cfg-OLLAMA_URL').value = currentConfig.OLLAMA_URL || '';
            document.getElementById('cfg-DEFAULT_MODEL').value = currentConfig.DEFAULT_MODEL || '';
            document.getElementById('cfg-GLOBAL_SYSTEM_PROMPT').value = currentConfig.GLOBAL_SYSTEM_PROMPT || '';
            document.getElementById('cfg-MAX_CONTEXT_LENGTH').value = currentConfig.MAX_CONTEXT_LENGTH;
            document.getElementById('cfg-OLLAMA_TIMEOUT').value = currentConfig.OLLAMA_TIMEOUT;
            document.getElementById('cfg-ENABLE_GROUP_AT_FILTER').checked = currentConfig.ENABLE_GROUP_AT_FILTER;
            document.getElementById('cfg-MESSAGE_MAX_LENGTH').value = currentConfig.MESSAGE_MAX_LENGTH;
            document.getElementById('cfg-MAX_RECONNECT').value = currentConfig.MAX_RECONNECT;
            document.getElementById('cfg-RECONNECT_DELAY').value = currentConfig.RECONNECT_DELAY;
            document.getElementById('cfg-HEARTBEAT_RATIO').value = currentConfig.HEARTBEAT_RATIO;
            document.getElementById('cfg-RETRY_COUNT').value = currentConfig.RETRY_COUNT;
            document.getElementById('cfg-ENABLE_REPORT').checked = currentConfig.ENABLE_REPORT;
            document.getElementById('cfg-REPORT_BACKEND_URL').value = currentConfig.REPORT_BACKEND_URL || '';
            document.getElementById('cfg-REPORT_TIMEOUT').value = currentConfig.REPORT_TIMEOUT;
            document.getElementById('cfg-REPORT_BODY_MAX_LENGTH').value = currentConfig.REPORT_BODY_MAX_LENGTH;
            document.getElementById('cfg-KEYWORD_EXACT').value = JSON.stringify(currentConfig.KEYWORD_EXACT, null, 2);
            document.getElementById('cfg-KEYWORD_FUZZY').value = JSON.stringify(currentConfig.KEYWORD_FUZZY, null, 2);
            document.getElementById('cfg-SENSITIVE_WORDS').value = (currentConfig.SENSITIVE_WORDS || []).join('\\n');
            document.getElementById('cfg-RATE_LIMIT_SECONDS').value = currentConfig.RATE_LIMIT_SECONDS;
            document.getElementById('cfg-RATE_LIMIT_MAX_COUNT').value = currentConfig.RATE_LIMIT_MAX_COUNT;
            document.getElementById('cfg-ADMIN_BYPASS_LIMIT').checked = currentConfig.ADMIN_BYPASS_LIMIT;
            document.getElementById('cfg-WEB_ADMIN_USERNAME').value = currentConfig.WEB_ADMIN_USERNAME || '';
            document.getElementById('cfg-WEB_ADMIN_PASSWORD').value = currentConfig.WEB_ADMIN_PASSWORD || '';
            document.getElementById('cfg-ADMIN_IDS').value = (currentConfig.ADMIN_IDS || []).join('\\n');
            document.getElementById('rawJson').value = JSON.stringify(currentConfig, null, 4);
        }
        function showSuccess(msg) {
            const el = document.getElementById('successMsg');
            el.textContent = msg;
            el.style.display = 'block';
            setTimeout(function() { el.style.display = 'none'; }, 3000);
        }
        async function saveBasicConfig() {
            const updates = {
                APPID: document.getElementById('cfg-APPID').value,
                APPSECRET: document.getElementById('cfg-APPSECRET').value,
                OLLAMA_URL: document.getElementById('cfg-OLLAMA_URL').value,
                DEFAULT_MODEL: document.getElementById('cfg-DEFAULT_MODEL').value,
                GLOBAL_SYSTEM_PROMPT: document.getElementById('cfg-GLOBAL_SYSTEM_PROMPT').value,
                MAX_CONTEXT_LENGTH: parseInt(document.getElementById('cfg-MAX_CONTEXT_LENGTH').value),
                OLLAMA_TIMEOUT: parseInt(document.getElementById('cfg-OLLAMA_TIMEOUT').value)
            };
            await submitConfig(updates);
        }
        async function saveAdvancedConfig() {
            const updates = {
                ENABLE_GROUP_AT_FILTER: document.getElementById('cfg-ENABLE_GROUP_AT_FILTER').checked,
                MESSAGE_MAX_LENGTH: parseInt(document.getElementById('cfg-MESSAGE_MAX_LENGTH').value),
                MAX_RECONNECT: parseInt(document.getElementById('cfg-MAX_RECONNECT').value),
                RECONNECT_DELAY: parseInt(document.getElementById('cfg-RECONNECT_DELAY').value),
                HEARTBEAT_RATIO: parseFloat(document.getElementById('cfg-HEARTBEAT_RATIO').value),
                RETRY_COUNT: parseInt(document.getElementById('cfg-RETRY_COUNT').value),
                ENABLE_REPORT: document.getElementById('cfg-ENABLE_REPORT').checked,
                REPORT_BACKEND_URL: document.getElementById('cfg-REPORT_BACKEND_URL').value,
                REPORT_TIMEOUT: parseInt(document.getElementById('cfg-REPORT_TIMEOUT').value),
                REPORT_BODY_MAX_LENGTH: parseInt(document.getElementById('cfg-REPORT_BODY_MAX_LENGTH').value)
            };
            await submitConfig(updates);
        }
        async function saveKeywordConfig() {
            try {
                const exact = JSON.parse(document.getElementById('cfg-KEYWORD_EXACT').value);
                const fuzzy = JSON.parse(document.getElementById('cfg-KEYWORD_FUZZY').value);
                await submitConfig({ KEYWORD_EXACT: exact, KEYWORD_FUZZY: fuzzy });
            } catch(e) {
                alert('JSON格式错误：' + e.message);
            }
        }
        async function saveSecurityConfig() {
            const words = document.getElementById('cfg-SENSITIVE_WORDS').value
                .split('\\n').map(function(s) { return s.trim(); }).filter(function(s) { return s; });
            const updates = {
                SENSITIVE_WORDS: words,
                RATE_LIMIT_SECONDS: parseInt(document.getElementById('cfg-RATE_LIMIT_SECONDS').value),
                RATE_LIMIT_MAX_COUNT: parseInt(document.getElementById('cfg-RATE_LIMIT_MAX_COUNT').value),
                ADMIN_BYPASS_LIMIT: document.getElementById('cfg-ADMIN_BYPASS_LIMIT').checked
            };
            await submitConfig(updates);
        }
        async function saveAdminConfig() {
            const ids = document.getElementById('cfg-ADMIN_IDS').value
                .split('\\n').map(function(s) { return s.trim(); }).filter(function(s) { return s; });
            const updates = {
                WEB_ADMIN_USERNAME: document.getElementById('cfg-WEB_ADMIN_USERNAME').value,
                WEB_ADMIN_PASSWORD: document.getElementById('cfg-WEB_ADMIN_PASSWORD').value,
                ADMIN_IDS: ids
            };
            await submitConfig(updates);
        }
        async function saveRawConfig() {
            try {
                const newConfig = JSON.parse(document.getElementById('rawJson').value);
                await submitConfig(newConfig);
            } catch(e) {
                alert('JSON格式错误：' + e.message);
            }
        }
        async function submitConfig(updates) {
            const res = await authFetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });
            const data = await res.json();
            if (data.success) {
                let msg = data.msg;
                if (data.need_restart && data.need_restart.length > 0) {
                    msg += '\\n⚠️ 以下配置需重启生效：' + data.need_restart.join(', ');
                }
                if (data.hot_updated && data.hot_updated.length > 0) {
                    msg += '\\n✅ 以下配置已热更新：' + data.hot_updated.join(', ');
                }
                showSuccess(msg);
                loadConfig();
                loadStatus();
            } else {
                alert('保存失败：' + data.msg);
            }
        }
        async function clearAllContext() {
            if (!confirm('确定要清空所有用户的对话上下文吗？')) return;
            const res = await authFetch('/api/clear-all-context', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                showSuccess(data.msg);
                loadStatus();
            } else {
                alert('操作失败：' + data.msg);
            }
        }
        async function loadStatus() {
            try {
                const res = await authFetch('/api/status');
                const data = await res.json();
                document.getElementById('botStatus').textContent = data.bot_online ? '在线' : '离线';
                document.getElementById('statusDot').className = 'status-dot ' + (data.bot_online ? 'online' : '');
                document.getElementById('currentModel').textContent = data.current_model;
                document.getElementById('userCount').textContent = data.user_count + ' 人';
            } catch(e) {}
        }
        window.onload = function() {
            loadConfig();
            loadStatus();
            setInterval(loadStatus, 5000);
        };
    </script>
</body>
</html>'''
    return web.Response(text=html, content_type="text/html", charset="utf-8")


async def start_web_admin():
    global web_admin_running
    if not config["ENABLE_WEB_ADMIN"]:
        logger.info("ℹ️ 后台管理网站已关闭", extra={"user_id": "system"})
        return
    app = web.Application(middlewares=[error_middleware])

    async def favicon_handler(request):
        return web.Response(status=204)
    app.router.add_get('/favicon.ico', favicon_handler)
    app.router.add_get('/', admin_page)
    app.router.add_get('/api/config', api_get_config)
    app.router.add_post('/api/config', api_update_config)
    app.router.add_get('/api/status', api_get_status)
    app.router.add_post('/api/clear-all-context', api_clear_all_context)

    host = "0.0.0.0" if config["WEB_ADMIN_ALLOW_REMOTE"] else "127.0.0.1"
    port = config["WEB_ADMIN_PORT"]
    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        web_admin_running = True
        logger.info(f"🌐 后台管理网站已启动：http://127.0.0.1:{port}", extra={"user_id": "system"})
        logger.info(f"🔐 默认账号：{config['WEB_ADMIN_USERNAME']} / {'*' * len(config['WEB_ADMIN_PASSWORD'])}", extra={"user_id": "system"})
    except Exception as e:
        logger.error(f"❌ 后台网站启动失败：{type(e).__name__}: {e}", exc_info=True, extra={"user_id": "system"})


# ===================== 启动配置校验 =====================
async def check_config(session: aiohttp.ClientSession) -> bool:
    logger.info("正在校验配置...", extra={"user_id": "system"})
    if not config["APPID"] or not config["APPSECRET"] or config["APPID"] == "你的APPID":
        logger.error("❌ 请在config.json中填写APPID和APPSECRET", extra={"user_id": "system"})
        return False
    try:
        models = await get_available_models(session)
        logger.info(f"✅ Ollama可用模型：{models}", extra={"user_id": "system"})
        if config["DEFAULT_MODEL"] not in models:
            logger.error(f"❌ 默认模型 {config['DEFAULT_MODEL']} 不存在，请先运行 ollama pull {config['DEFAULT_MODEL']}", extra={"user_id": "system"})
            return False
    except Exception as e:
        logger.error(f"❌ 无法连接Ollama服务：{e}", extra={"user_id": "system"})
        logger.error("请确保Ollama已启动并运行 ollama serve", extra={"user_id": "system"})
        return False
    logger.info(f"✅ 已加载 {len(config['ADMIN_IDS'])} 个管理员账号", extra={"user_id": "system"})
    logger.info(f"✅ 已加载 {len(config['KEYWORD_EXACT'])+len(config['KEYWORD_FUZZY'])} 个关键词回复", extra={"user_id": "system"})
    logger.info(f"✅ 敏感词库已加载 {len(config['SENSITIVE_WORDS'])} 条规则", extra={"user_id": "system"})
    logger.info(f"✅ 防刷屏限流：{'开启' if config['RATE_LIMIT_MAX_COUNT'] > 0 else '关闭'}，管理员豁免：{'是' if config['ADMIN_BYPASS_LIMIT'] else '否'}", extra={"user_id": "system"})
    logger.info(f"✅ 后台请求上报：{'开启' if config['ENABLE_REPORT'] else '关闭'}", extra={"user_id": "system"})
    logger.info("✅ 所有配置校验通过", extra={"user_id": "system"})
    return True

# ===================== 主运行循环（修复重连机制） =====================
async def run():
    global bot_id, processed_messages, user_contexts, current_model, max_context_length, bot_online_status
    async with aiohttp.ClientSession() as session:
        await start_web_admin()
        if not await check_config(session):
            return
        # 加载持久化的对话上下文
        load_contexts()
        # 启动定期刷盘任务
        flush_task = asyncio.create_task(periodic_flush_contexts())
        # 新增定时刷新token任务，每5分钟检测，剩余10分钟强制刷新
        async def token_refresh_task(session_inner):
            while True:
                try:
                    # 剩余600秒(10分钟)就刷新token，避免2小时过期4009
                    if time.time() > token_state["expire"] - 600:
                        new_token, new_expire = await get_token(session_inner)
                        token_state["value"] = new_token
                        token_state["expire"] = new_expire
                        logger.info("🔄 定时预刷新access_token，避免会话4009超时", extra={"user_id": "system"})
                except Exception as e:
                    logger.error(f"定时刷新token失败：{e}", extra={"user_id": "system"})
                await asyncio.sleep(300)

        token_refresh_task_obj = asyncio.create_task(token_refresh_task(session))

        reconnect_count = 0
        heartbeat_task = None  # 提前初始化，避免finally访问报错

        while True:
            # 达到最大重连次数则退出
            if reconnect_count >= config["MAX_RECONNECT"]:
                logger.error(f"❌ 已达到最大重连次数({config['MAX_RECONNECT']}次)，程序退出", extra={"user_id": "system"})
                break

            # 非首次运行则打印重连提示
            if reconnect_count > 0:
                logger.info(f"🔌 开始第 {reconnect_count} 次自动重连...", extra={"user_id": "system"})

            try:
                # 1. 获取/刷新令牌
                if time.time() > token_state["expire"]:
                    token, token_expire = await get_token(session)
                    token_state["value"] = token
                    token_state["expire"] = token_expire
                    logger.info("🔄 已刷新访问令牌", extra={"user_id": "system"})

                # 2. 获取网关地址
                try:
                    gateway = await get_gateway(session, token_state["value"])
                except TokenExpiredError:
                    logger.info("🔄 令牌过期，重新获取后再连接网关", extra={"user_id": "system"})
                    token, token_expire = await get_token(session)
                    token_state["value"] = token
                    token_state["expire"] = token_expire
                    gateway = await get_gateway(session, token_state["value"])
                logger.info("✅ 正在连接QQ网关...", extra={"user_id": "system"})    

                # 3. 建立WebSocket连接
                async with websockets.connect(
                    gateway,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10,
                    max_size=20*1024*1024,
                    compression=None
                ) as ws:
                    heartbeat_interval = None
                    last_heartbeat_ack = time.time()
                    last_seq = None

                    while True:
                        try:
                            raw = await ws.recv()
                            if not raw:
                                logger.warning("⚠️ 收到空消息，连接可能已断开", extra={"user_id": "system"})
                                break
                            data = json.loads(raw)
                            op = data.get("op")
                            event_type = data.get("t")

                            if "s" in data and data["s"] is not None:
                                if data["s"] != last_seq:
                                    last_seq = data["s"]

                            if op == 10:
                                heartbeat_interval = data["d"]["heartbeat_interval"] / 1000 * config["HEARTBEAT_RATIO"]
                                logger.info(f"💓 收到官方心跳间隔：{data['d']['heartbeat_interval']/1000:.1f}秒，调整为：{heartbeat_interval:.1f}秒", extra={"user_id": "system"})

                                async def heartbeat():
                                    nonlocal last_heartbeat_ack, last_seq
                                    while True:
                                        try:
                                            is_closed = ws.closed if hasattr(ws, 'closed') else ws.is_closed()
                                            if is_closed:
                                                break
                                            # 发送心跳包
                                            heartbeat_packet = {"op": 1, "d": last_seq}
                                            await ws.send(json.dumps(heartbeat_packet))
                                            # 半周期检测一次，提前发现超时
                                            await asyncio.sleep(heartbeat_interval * 0.5)
                                            if time.time() - last_heartbeat_ack > heartbeat_interval * 1.5:
                                                logger.warning("⚠️ 心跳确认超时，主动断开重连（避免4009会话超时）", extra={"user_id": "system"})
                                                await ws.close()
                                                break
                                            # 等待剩余半周期
                                            await asyncio.sleep(heartbeat_interval * 0.5)
                                            if time.time() - last_heartbeat_ack > heartbeat_interval * 1.5:
                                                logger.warning("⚠️ 心跳确认超时，主动断开重连（避免4009会话超时）", extra={"user_id": "system"})
                                                await ws.close()
                                                break
                                        except Exception:
                                            break

                                heartbeat_task = asyncio.create_task(heartbeat())
                                intents = 1 + 2 + 4096 + 33554432
                                auth = {
                                    "op": 2,
                                    "d": {
                                        "token": f"QQBot {token_state['value']}",
                                        "intents": intents,
                                        "shard": [0, 1],
                                        "properties": {
                                            "$os": "windows",
                                            "$browser": "my_bot",
                                            "$device": "my_bot"
                                        }
                                    }
                                }
                                await ws.send(json.dumps(auth))
                                logger.info("🔐 已发送认证请求", extra={"user_id": "system"})

                            elif op == 11:
                                last_heartbeat_ack = time.time()

                            elif event_type == "READY":
                                bot_id = data["d"]["user"]["id"]
                                bot_online_status = True
                                # 认证成功后再重置重连计数
                                reconnect_count = 0
                                logger.info(f"✅ 机器人已上线！ID：{bot_id}", extra={"user_id": "system"})
                                if config["ENABLE_GROUP_AT_FILTER"]:
                                    logger.info("✅ 群聊@过滤已开启，仅在被@时回复群消息", extra={"user_id": "system"})
                                logger.info(f"🤖 当前使用模型：{current_model}", extra={"user_id": "system"})

                            elif event_type in ("C2C_MESSAGE_CREATE", "AT_MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"):
                                msg_id = data["d"]["id"]
                                if msg_id in processed_messages:
                                    continue
                                processed_messages.add(msg_id)
                                if len(processed_messages) > 5000:
                                    processed_messages = set(list(processed_messages)[-2500:])
                                user_msg = data["d"]["content"].strip()

                                if event_type == "GROUP_AT_MESSAGE_CREATE":
                                    user_id = data["d"].get("author", {}).get("member_openid") or data["d"].get("author", {}).get("id", "unknown")
                                else:
                                    user_id = data["d"]["author"]["id"]
                                is_admin = user_id in config["ADMIN_IDS"]

                                if event_type == "C2C_MESSAGE_CREATE":
                                    openid = data["d"].get("user_openid") or data["d"]["author"]["id"]
                                else:
                                    openid = None
                                guild_id = data["d"].get("src_guild_id") if event_type == "DIRECT_MESSAGE_CREATE" else None
                                group_openid = data["d"].get("group_openid") if event_type == "GROUP_AT_MESSAGE_CREATE" else None
                                channel_id = data["d"].get("channel_id") if event_type not in ("C2C_MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE") else None

                                if event_type == "AT_MESSAGE_CREATE" and config["ENABLE_GROUP_AT_FILTER"]:
                                    mentions = data["d"].get("mentions", [])
                                    is_mentioned = any(mention["id"] == bot_id for mention in mentions)
                                    if not is_mentioned:
                                        continue
                                    user_msg = re.sub(r'<@!?{}>'.format(bot_id), '', user_msg).strip()
                                    if not user_msg:
                                        continue

                                if event_type == "GROUP_AT_MESSAGE_CREATE":
                                    user_msg = re.sub(r'<@!?{}>'.format(bot_id), '', user_msg).strip()
                                    if not user_msg:
                                        continue

                                logger.info(f"📩 收到消息：{user_msg[:100]}...", extra={"user_id": user_id})

                                async def handle_reply(session, token_st, event_type, openid, guild_id, channel_id, user_id, user_msg, is_admin, orig_msg_id, group_openid=None):
                                    try:
                                        reply = await ollama_reply(session, user_id, user_msg, is_admin)
                                        if not reply or len(reply.strip()) == 0:
                                            reply = "我收到了你的消息，但暂时无法回复"
                                        current_token = token_st["value"]
                                        send_success = await send_long_message(
                                            session, current_token, event_type,
                                            openid, guild_id, channel_id,
                                            reply, user_id, msg_id=orig_msg_id,
                                            group_openid=group_openid
                                        )
                                        if send_success:
                                            logger.info(f"📤 发送回复成功：{reply[:100]}...", extra={"user_id": user_id})
                                        else:
                                            logger.error(f"📤 发送回复最终失败（重试耗尽）", extra={"user_id": user_id})
                                            try:
                                                await send_msg(
                                                    session, token_st["value"], event_type,
                                                    openid, guild_id, channel_id,
                                                    "⚠️ 消息发送失败，请稍后重试", user_id,
                                                    group_openid=group_openid
                                                )
                                            except Exception:
                                                pass
                                    except Exception as e:
                                        error_msg = f"⚠️ AI服务暂时不可用：{str(e)[:50]}..."
                                        logger.error(f"生成回复最终失败 | 错误类型:{type(e).__name__} | 详情:{str(e)}",
                                                     exc_info=True, extra={"user_id": user_id})
                                        try:
                                            await send_msg(
                                                session, token_st["value"], event_type,
                                                openid, guild_id, channel_id,
                                                error_msg, user_id,
                                                group_openid=group_openid
                                            )
                                        except Exception:
                                            logger.warning("错误提示消息也发送失败", extra={"user_id": user_id})

                                asyncio.create_task(handle_reply(
                                    session, token_state, event_type,
                                    openid, guild_id, channel_id,
                                    user_id, user_msg, is_admin, msg_id,
                                    group_openid=group_openid
                                ))

                        except ConnectionClosedOK as e:
                            bot_online_status = False
                            logger.warning(f"❌ 连接正常关闭：{e.code} {e.reason}", extra={"user_id": "system"})
                            reconnect_count += 1
                            raise ConnectionClosedError(e.code, e.reason)
                        except ConnectionClosedError as e:
                            bot_online_status = False
                            logger.warning(f"❌ 连接异常断开：错误码{e.code} | 原因:{e.reason}", extra={"user_id": "system"})
                            reconnect_count += 1
                            raise e
                        except json.JSONDecodeError as e:
                            logger.error(f"❌ 消息解析失败：{e}", extra={"user_id": "system"})
                            continue
                        except Exception as e:
                            logger.error(f"❌ 消息处理异常：{type(e).__name__}: {e}", exc_info=True, extra={"user_id": "system"})
                            continue

            except TokenExpiredError:
                bot_online_status = False
                reconnect_count += 1
                logger.info("🔄 令牌已过期，刷新后重连", extra={"user_id": "system"})
            except Exception as e:
                bot_online_status = False
                reconnect_count += 1
                logger.error(f"❌ 连接/重连失败（第{reconnect_count}次）：{type(e).__name__}: {e}",
                             exc_info=True, extra={"user_id": "system"})
            finally:
                # 安全取消心跳任务
                try:
                    if heartbeat_task and not heartbeat_task.done():
                        heartbeat_task.cancel()
                        await heartbeat_task
                except Exception:
                    pass
                # 断开后立即保存上下文
                if config.get("ENABLE_CONTEXT_PERSISTENCE", False):
                    save_contexts()
                # 未达到最大次数则等待重连
                if reconnect_count < config["MAX_RECONNECT"]:
                    delay = config["RECONNECT_DELAY"]
                    logger.info(f"⏳ {delay}秒后进行下一次重连...", extra={"user_id": "system"})
                    await asyncio.sleep(delay)

                # 退出时取消刷盘任务并做最终保存
                try:
                    flush_task.cancel()
                    token_refresh_task_obj.cancel()
                    await flush_task
                    await token_refresh_task_obj
                except Exception:
                    pass

        if config.get("ENABLE_CONTEXT_PERSISTENCE", False):
            save_contexts()
            logger.info("💾 退出前已保存对话上下文", extra={"user_id": "system"})

async def main():
    load_config()
    try:
        await run()
    except KeyboardInterrupt:
        # 捕获同步层面的键盘中断
        logger.info("👋 收到退出信号，机器人正在安全退出...", extra={"user_id": "system"})
    except asyncio.CancelledError:
        # 捕获异步任务取消（Ctrl+C触发的协程取消）
        logger.info("👋 收到退出信号，机器人正在安全退出...", extra={"user_id": "system"})
    except Exception as e:
        logger.critical(f"程序异常终止：{type(e).__name__}: {e}", exc_info=True, extra={"user_id": "system"})
    finally:
        # 无论正常退出、异常退出还是Ctrl+C，都会执行保存逻辑
        if config.get("ENABLE_CONTEXT_PERSISTENCE", False):
            save_contexts()
            logger.info("💾 对话上下文已保存", extra={"user_id": "system"})
        logger.info("✅ 程序已完全退出", extra={"user_id": "system"})

if __name__ == "__main__":
    asyncio.run(main())