import os
import sys
import json
# ===================== 基础路径定义（适配exe打包） =====================
def get_run_base_dir():
    # 判断是否是PyInstaller打包后的exe环境
    if hasattr(sys, "_MEIPASS"):
        # exe运行时，程序根目录为exe所在文件夹
        return os.path.dirname(sys.executable)
    else:
        # 源码直接运行，使用py文件所在目录
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_run_base_dir()
LOG_DIR = os.path.join(BASE_DIR, "logs")
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_README_PATH = os.path.join(BASE_DIR, "config配置说明.txt")
DEFAULT_CONTEXTS_PATH = os.path.join(BASE_DIR, "contexts.json")
PRIVACY_KEY_PATH = os.path.join(BASE_DIR, ".privacy_key")
WEB_ADMIN_HTML_PATH = os.path.join(BASE_DIR, "web_admin.html")

# 自动创建日志文件夹，防止首次运行无目录报错
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# ===================== 默认内置配置（含隐私保护） =====================
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
    "CONTEXT_SAVE_INTERVAL": 30,
    # ========== 隐私保护配置 ==========
    "PRIVACY_ENABLE_MODE": True,
    "PRIVACY_MASK_USER_ID_IN_LOGS": False,
    "PRIVACY_MASK_SENSITIVE_CONTENT": True,
    "PRIVACY_AUTO_CLEANUP_INTERVAL": 3600,
    "PRIVACY_MAX_CONTEXT_AGE": 86400,
    "PRIVACY_ENCRYPT_CONTEXT_STORAGE": True,
    "PRIVACY_LOG_RETENTION_DAYS": 7,
    "PRIVACY_ENABLE_ANONYMOUS_STATS": False,
    "PRIVACY_SENSITIVE_PATTERNS": [
        "\\d{17}[\\dXx]",                            # 身份证（优先匹配，避免被手机号规则覆盖）
        "\\d{11}",                                   # 手机号（精确11位）
        "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}",  # 邮箱
        "\\d{4}-\\d{2}-\\d{2}",                      # 日期
        "\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}" # IP地址
    ]
}

config = DEFAULT_CONFIG.copy()


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
九、隐私保护配置
--------------------------------------------------
PRIVACY_ENABLE_MODE
  说明：是否启用全局隐私保护模式
  取值：true/false
  默认：true
PRIVACY_MASK_USER_ID_IN_LOGS
  说明：日志中是否对用户ID进行掩码处理
  默认：false（建议生产环境设为true）
PRIVACY_MASK_SENSITIVE_CONTENT
  说明：是否对消息中的敏感信息进行自动掩码
  默认：true
PRIVACY_AUTO_CLEANUP_INTERVAL
  说明：自动清理过期上下文的间隔时间（秒），默认3600
PRIVACY_MAX_CONTEXT_AGE
  说明：单个用户上下文保留的最长时间（秒），默认86400
PRIVACY_ENCRYPT_CONTEXT_STORAGE
  说明：是否对保存到磁盘的上下文文件进行加密，默认true
PRIVACY_LOG_RETENTION_DAYS
  说明：日志文件保留天数，超过自动删除，默认7
PRIVACY_SENSITIVE_PATTERNS
  说明：敏感信息匹配正则表达式列表
========================================
修改注意事项
1. 所有字符串用英文双引号包裹
2. 列表最后一项后面不要加逗号
3. 布尔值使用JSON原生格式：true/false
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
    global config
    from context import current_model, max_context_length, apply_hot_config

    if not os.path.exists(config_path):
        print(f"[提示] 未找到配置文件，路径：{config_path}")
        print("[提示] 正在自动生成默认配置和说明文档...")
        gen_ok = generate_default_config(config_path)
        generate_config_readme()
        if not gen_ok:
            print("[警告] 配置文件生成失败，将使用内置默认配置启动")
            return False
        print("[提示] 配置文件已生成，请打开config.json填写APPID和APPSECRET")
        print("[提示] 配置项说明可查看同目录的 config配置说明.txt")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config.update(user_config)
        apply_hot_config()
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


def is_config_hot_updateable(key: str) -> bool:
    """判断配置项是否支持热更新"""
    hot_keys = {
        "MAX_CONTEXT_LENGTH", "RETRY_COUNT", "OLLAMA_TIMEOUT",
        "GLOBAL_SYSTEM_PROMPT", "ENABLE_GROUP_AT_FILTER",
        "MESSAGE_MAX_LENGTH", "HEARTBEAT_RATIO", "ADMIN_IDS",
        "KEYWORD_EXACT", "KEYWORD_FUZZY", "SENSITIVE_WORDS",
        "RATE_LIMIT_SECONDS", "RATE_LIMIT_MAX_COUNT", "ADMIN_BYPASS_LIMIT",
        "ENABLE_REPORT", "REPORT_BACKEND_URL", "REPORT_TIMEOUT", "REPORT_BODY_MAX_LENGTH",
        "WEB_ADMIN_USERNAME", "WEB_ADMIN_PASSWORD", "DEFAULT_MODEL",
        "ENABLE_CONTEXT_PERSISTENCE", "CONTEXT_SAVE_INTERVAL",
        "PRIVACY_ENABLE_MODE", "PRIVACY_MASK_USER_ID_IN_LOGS", "PRIVACY_MASK_SENSITIVE_CONTENT",
        "PRIVACY_AUTO_CLEANUP_INTERVAL", "PRIVACY_MAX_CONTEXT_AGE", "PRIVACY_ENCRYPT_CONTEXT_STORAGE",
        "PRIVACY_LOG_RETENTION_DAYS", "PRIVACY_ENABLE_ANONYMOUS_STATS"
    }
    return key in hot_keys
