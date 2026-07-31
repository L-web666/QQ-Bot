import time
import json
import os
import asyncio
from typing import Dict, List, Set, Tuple
from collections import deque
from config import config, DEFAULT_CONTEXTS_PATH
from privacy_log import logger, privacy_manager

# ===================== 全局运行时变量 =====================
user_contexts: Dict[str, List[Dict]] = {}
user_contexts_timestamp: Dict[str, float] = {}
processed_messages: Set[str] = set()
msg_id_queue = deque()  # 维护消息ID插入顺序，修复去重BUG
user_rate_limit: Dict[str, Tuple[float, int]] = {}

current_model: str = config["DEFAULT_MODEL"]
max_context_length: int = config["MAX_CONTEXT_LENGTH"]
contexts_dirty = False
ollama_semaphore = asyncio.Semaphore(1)
main_loop_running = True


# ===================== 热更新配置同步 =====================
def apply_hot_config():
    """配置热更新后同步到全局变量"""
    global current_model, max_context_length
    current_model = config["DEFAULT_MODEL"]
    max_context_length = config["MAX_CONTEXT_LENGTH"]
    for uid in user_contexts:
        if user_contexts[uid] and user_contexts[uid][0]["role"] == "system":
            user_contexts[uid][0]["content"] = config["GLOBAL_SYSTEM_PROMPT"]


# ===================== 限流检测 =====================
def check_rate_limit(user_id: str, is_admin: bool) -> str:
    if is_admin and config["ADMIN_BYPASS_LIMIT"]:
        return ""
    global user_rate_limit
    now = time.time()

    if len(user_rate_limit) > 100:
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


# ===================== 自动清理过期上下文 =====================
def auto_cleanup_contexts():
    """自动清理过期的上下文数据"""
    if not config.get("PRIVACY_ENABLE_MODE", True):
        return

    now = time.time()
    expired_users = []
    max_age = config.get("PRIVACY_MAX_CONTEXT_AGE", 86400)

    for user_id, timestamp in user_contexts_timestamp.items():
        if now - timestamp > max_age:
            expired_users.append(user_id)

    for user_id in expired_users:
        if user_id in user_contexts:
            system_msg = None
            if user_contexts[user_id] and user_contexts[user_id][0]["role"] == "system":
                system_msg = user_contexts[user_id][0]
            user_contexts[user_id] = [system_msg] if system_msg else []
            logger.info(f"自动清理用户 {privacy_manager.mask_user_id(user_id)} 的过期上下文",
                       extra={"user_id": "system"})

    if expired_users:
        global contexts_dirty
        contexts_dirty = True


async def periodic_privacy_cleanup():
    """定期执行隐私清理任务"""
    while main_loop_running:
        try:
            interval = config.get("PRIVACY_AUTO_CLEANUP_INTERVAL", 3600)
            await asyncio.sleep(interval)
            auto_cleanup_contexts()
            now = time.time()
            expired_rate_limits = [
                uid for uid, (t, _) in user_rate_limit.items()
                if now - t > config["RATE_LIMIT_SECONDS"] * 2
            ]
            for uid in expired_rate_limits:
                del user_rate_limit[uid]
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"定期隐私清理异常：{e}", extra={"user_id": "system"})


import asyncio


# ===================== 上下文持久化 =====================
def save_contexts(filepath: str = DEFAULT_CONTEXTS_PATH) -> bool:
    """将当前所有用户对话上下文保存到JSON文件（加密）"""
    global contexts_dirty
    if not config.get("ENABLE_CONTEXT_PERSISTENCE", False):
        return False

    try:
        contexts_to_save = {}
        encrypt = config.get("PRIVACY_ENCRYPT_CONTEXT_STORAGE", True)
        for uid, msgs in user_contexts.items():
            if msgs:
                encrypted_msgs = []
                for msg in msgs:
                    msg_copy = msg.copy()
                    if encrypt:
                        msg_copy["content"] = privacy_manager.encrypt(msg_copy["content"])
                    encrypted_msgs.append(msg_copy)
                contexts_to_save[uid] = encrypted_msgs

        data = {
            "save_time": int(time.time()),
            "model": current_model,
            "encrypted": encrypt,
            "contexts": contexts_to_save
        }

        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
        contexts_dirty = False
        logger.debug(f"💾 已保存 {len(user_contexts)} 个用户的对话上下文（加密：{encrypt}）",
                    extra={"user_id": "system"})
        return True
    except Exception as e:
        logger.error(f"保存对话上下文失败：{e}", extra={"user_id": "system"})
        return False


def load_contexts(filepath: str = DEFAULT_CONTEXTS_PATH) -> bool:
    """从JSON文件加载用户对话上下文（修复解密逻辑BUG）"""
    global user_contexts, contexts_dirty, user_contexts_timestamp
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
        is_encrypted = data.get("encrypted", False)

        for uid, msgs in saved_contexts.items():
            if isinstance(msgs, list) and len(msgs) > 0:
                # 修复：只要文件是加密存储的，无论当前开关状态都解密
                decrypted_msgs = []
                for msg in msgs:
                    msg_copy = msg.copy()
                    if is_encrypted:
                        try:
                            msg_copy["content"] = privacy_manager.decrypt(msg_copy["content"])
                        except Exception:
                            logger.warning(f"用户 {uid} 上下文解密失败，保留原内容", extra={"user_id": "system"})
                    decrypted_msgs.append(msg_copy)
                user_contexts[uid] = decrypted_msgs
                user_contexts_timestamp[uid] = time.time()
                loaded_count += 1

        contexts_dirty = False
        save_time = data.get("save_time", 0)
        save_age = int(time.time() - save_time) if save_time else 0
        logger.info(f"💾 已加载 {loaded_count} 个用户的对话上下文（上次保存于 {save_age} 秒前，加密：{is_encrypted}）",
                   extra={"user_id": "system"})
        return True
    except json.JSONDecodeError as e:
        logger.error(f"对话上下文文件格式错误：{e}", extra={"user_id": "system"})
        return False
    except Exception as e:
        logger.error(f"加载对话上下文失败：{e}", extra={"user_id": "system"})
        return False


async def periodic_flush_contexts():
    """定期将对话上下文刷盘，仅在内容变化时写入"""
    while main_loop_running:
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
