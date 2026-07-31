import logging
import os
import re
import base64
import secrets
from datetime import datetime
from config import LOG_DIR, PRIVACY_KEY_PATH, config


# ===================== 日志文件生成 =====================
def get_new_log_file() -> str:
    """自动创建logs目录，返回带启动时间的日志文件路径"""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
    time_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_name = f"{time_str}.txt"
    return os.path.join(LOG_DIR, log_name)


# ===================== 隐私加密管理器 =====================
class PrivacyManager:
    """隐私保护管理器"""

    def __init__(self):
        self._encryption_key = None
        self._salt = None
        self._load_or_generate_key()

    def _load_or_generate_key(self):
        """加载或生成加密密钥"""
        try:
            if os.path.exists(PRIVACY_KEY_PATH):
                with open(PRIVACY_KEY_PATH, 'r') as f:
                    key_data = json.load(f)
                    self._encryption_key = base64.b64decode(key_data['key'])
                    self._salt = base64.b64decode(key_data['salt'])
            else:
                self._encryption_key = secrets.token_bytes(32)
                self._salt = secrets.token_bytes(16)
                key_data = {
                    'key': base64.b64encode(self._encryption_key).decode(),
                    'salt': base64.b64encode(self._salt).decode()
                }
                with open(PRIVACY_KEY_PATH, 'w') as f:
                    json.dump(key_data, f)
                os.chmod(PRIVACY_KEY_PATH, 0o600)
        except Exception as e:
            logger.error(f"加密密钥初始化失败：{e}", extra={"user_id": "system"})
            self._encryption_key = secrets.token_bytes(32)
            self._salt = secrets.token_bytes(16)

    def encrypt(self, data: str) -> str:
        """加密数据"""
        if not config.get("PRIVACY_ENCRYPT_CONTEXT_STORAGE", True):
            return data
        try:
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=self._salt,
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(self._encryption_key))
            f = Fernet(key)
            encrypted = f.encrypt(data.encode())
            return base64.b64encode(encrypted).decode()
        except Exception:
            logger.warning("加密失败，使用明文存储", extra={"user_id": "system"})
            return data

    def decrypt(self, encrypted_data: str) -> str:
        """解密数据"""
        try:
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=self._salt,
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(self._encryption_key))
            f = Fernet(key)
            decrypted = f.decrypt(base64.b64decode(encrypted_data))
            return decrypted.decode()
        except Exception:
            logger.warning("解密失败，使用明文数据", extra={"user_id": "system"})
            return encrypted_data

    def mask_user_id(self, user_id: str) -> str:
        """掩码用户ID"""
        if not config.get("PRIVACY_MASK_USER_ID_IN_LOGS", False):
            return user_id
        if len(user_id) <= 6:
            return "***"
        return user_id[:3] + "***" + user_id[-3:]

    def mask_sensitive_content(self, content: str) -> str:
        """掩码敏感内容"""
        if not config.get("PRIVACY_MASK_SENSITIVE_CONTENT", True):
            return content

        patterns = config.get("PRIVACY_SENSITIVE_PATTERNS", [])
        for pattern in patterns:
            content = re.sub(pattern, lambda m: self._mask_match(m.group()), content)
        return content

    def _mask_match(self, text: str) -> str:
        """掩码匹配到的敏感信息"""
        if len(text) <= 4:
            return "***"
        if '@' in text:
            parts = text.split('@')
            return parts[0][:2] + "***@" + parts[1]
        if len(text) >= 18:
            return text[:3] + "***" + text[-4:]
        if len(text) == 11:
            return text[:3] + "***" + text[-4:]
        return text[:2] + "***" + text[-2:]

    def anonymize_message(self, message: str) -> str:
        """匿名化消息内容"""
        message = re.sub(r'[我你他她]的?[手机号电话微信QQ邮箱]', '[个人信息]', message)
        message = re.sub(r'\d{3,}', '[数字]', message)
        return message


# ===================== 日志过滤器 =====================
class UserIdLogFilter(logging.Filter):
    """给所有日志自动补充user_id字段，避免格式化时报错"""
    def filter(self, record):
        if not hasattr(record, "user_id"):
            record.user_id = "system"
        return True


class PrivacyLogFilter(logging.Filter):
    """日志隐私过滤器"""
    def filter(self, record):
        if hasattr(record, 'user_id') and record.user_id != "system":
            record.user_id = privacy_manager.mask_user_id(record.user_id)
        if hasattr(record, 'msg'):
            record.msg = privacy_manager.mask_sensitive_content(record.msg)
        return True


# ===================== 日志初始化 =====================
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
_privacy_filter = PrivacyLogFilter()
for _handler in logging.root.handlers:
    _handler.addFilter(_log_filter)
    _handler.addFilter(_privacy_filter)

logger = logging.getLogger(__name__)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

# 初始化隐私管理器（必须在logger之后）
import json
privacy_manager = PrivacyManager()

logger.info(f"📄 本次启动日志文件：{current_log_file}", extra={"user_id": "system"})


# ===================== 日志自动清理 =====================
def cleanup_old_logs():
    """清理旧的日志文件"""
    try:
        now = datetime.now()
        retention_days = config.get("PRIVACY_LOG_RETENTION_DAYS", 7)
        for filename in os.listdir(LOG_DIR):
            if filename.endswith('.txt'):
                filepath = os.path.join(LOG_DIR, filename)
                try:
                    date_str = filename.split('_')[0]
                    file_date = datetime.strptime(date_str, "%Y-%m-%d")
                    age = (now - file_date).days
                    if age > retention_days:
                        os.remove(filepath)
                        logger.info(f"已删除过期日志：{filename}", extra={"user_id": "system"})
                except (ValueError, IndexError):
                    mtime = os.path.getmtime(filepath)
                    mtime_date = datetime.fromtimestamp(mtime)
                    age = (now - mtime_date).days
                    if age > retention_days:
                        os.remove(filepath)
                        logger.info(f"已删除过期日志：{filename}", extra={"user_id": "system"})
    except Exception as e:
        logger.error(f"清理日志失败：{e}", extra={"user_id": "system"})


cleanup_old_logs()
