import re
import time
from config import config
from privacy_log import logger, privacy_manager
import context
from context import check_rate_limit
from utils import check_keyword, check_sensitive_word, remove_thinking, retry
from network import get_available_models, ollama_chat_request


@retry()
async def ollama_reply(session, user_id: str, msg: str, is_admin: bool) -> str:
    # 更新用户活跃时间
    context.user_contexts_timestamp[user_id] = time.time()

    # 限流检测
    limit_tip = check_rate_limit(user_id, is_admin)
    if limit_tip:
        logger.warning(f"用户触发限流", extra={"user_id": user_id})
        return limit_tip

    # 敏感词检测
    sensitive_tip = check_sensitive_word(msg, is_admin)
    if sensitive_tip:
        logger.warning(f"检测到敏感词", extra={"user_id": user_id})
        return sensitive_tip

    # 清空上下文指令
    clear_commands = ["/清空上下文", "/重置对话", "/clear", "/清除上下文"]
    if msg.strip() in clear_commands:
        if user_id in context.user_contexts:
            context.user_contexts[user_id] = [{"role": "system", "content": config["GLOBAL_SYSTEM_PROMPT"]}]
            context.contexts_dirty = True
            return "✅ 你的上下文已成功清空，可以开始新的对话了"
        else:
            return "ℹ️ 你还没有任何对话记录，无需清空"

    # 查看上下文指令
    context_commands = ["/查看上下文", "/上下文状态", "/context"]
    if msg.strip() in context_commands:
        if user_id in context.user_contexts and len(context.user_contexts[user_id]) > 1:
            rounds = (len(context.user_contexts[user_id]) - 1) // 2
            return f"📊 当前上下文状态：\n- 对话轮数：{rounds}轮\n- 最大保留：{context.max_context_length}轮\n- 当前总消息数：{len(context.user_contexts[user_id])-1}条"
        else:
            return "ℹ️ 你还没有任何对话记录"

    # 隐私设置指令
    privacy_commands = ["/隐私设置", "/隐私模式", "/查看隐私设置"]
    if msg.strip() in privacy_commands:
        status = "已启用" if config.get("PRIVACY_ENABLE_MODE", True) else "已禁用"
        return f"""🔒 隐私保护设置：
• 隐私保护模式：{status}
• 日志用户ID掩码：{'已启用' if config.get('PRIVACY_MASK_USER_ID_IN_LOGS', False) else '已禁用'}
• 敏感内容掩码：{'已启用' if config.get('PRIVACY_MASK_SENSITIVE_CONTENT', True) else '已禁用'}
• 上下文加密存储：{'已启用' if config.get('PRIVACY_ENCRYPT_CONTEXT_STORAGE', True) else '已禁用'}
• 上下文自动清理间隔：{config.get('PRIVACY_AUTO_CLEANUP_INTERVAL', 3600)//3600}小时
• 上下文保留时间：{config.get('PRIVACY_MAX_CONTEXT_AGE', 86400)//3600}小时
• 日志保留天数：{config.get('PRIVACY_LOG_RETENTION_DAYS', 7)}天
💡 你的对话数据受到加密保护，定期自动清理"""

    # ========== 管理员指令 ==========
    if is_admin:
        # 查看模型列表
        model_list_commands = ["/查看模型", "/模型列表", "/models"]
        if msg.strip() in model_list_commands:
            try:
                models = await get_available_models(session)
                model_list = "\n".join([f"- {model}" for model in models])
                return f"🤖 可用模型列表：\n{model_list}\n\n当前使用：{context.current_model}"
            except Exception as e:
                logger.error(f"获取模型列表失败：{e}", extra={"user_id": user_id})
                return "❌ 无法获取模型列表，请检查Ollama服务是否正常"

        # 切换模型
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
                # 真正修改context模块的全局变量，确保推理侧同步生效
                context.current_model = target_model
                for uid in list(context.user_contexts):
                    context.user_contexts[uid] = [{"role": "system", "content": config["GLOBAL_SYSTEM_PROMPT"]}]
                context.contexts_dirty = True
                logger.info(f"🔄 管理员已切换模型为：{context.current_model}", extra={"user_id": user_id})
                return f"✅ 已成功切换模型为：{context.current_model}\n所有用户上下文已自动清空"
            except Exception as e:
                logger.error(f"切换模型失败：{e}", extra={"user_id": user_id})
                return "❌ 切换模型失败，请检查Ollama服务是否正常"

        # 查看当前模型
        current_model_commands = ["/当前模型", "/current"]
        if msg.strip() in current_model_commands:
            return f"🤖 当前使用的模型：{context.current_model}\n📌 默认模型：{config['DEFAULT_MODEL']}"

        # 清空所有上下文
        clear_all_commands = ["/清空所有上下文", "/清除所有上下文", "/清空全部上下文", "/全局清空", "/clear all"]
        if msg.strip() in clear_all_commands:
            context.user_contexts.clear()
            context.user_contexts_timestamp.clear()
            context.contexts_dirty = True
            logger.info(f"🔧 管理员清空了所有用户上下文", extra={"user_id": user_id})
            return "✅ 管理员操作：所有用户的上下文已全部清空"

        # 清空指定用户上下文
        if msg.startswith(("/清空用户上下文", "/清除用户上下文")):
            try:
                if msg.startswith("/清空用户上下文"):
                    target_id = msg.split("/清空用户上下文")[1].strip()
                else:
                    target_id = msg.split("/清除用户上下文")[1].strip()
                if not target_id:
                    return "❌ 格式错误，请使用：/清空用户上下文 用户ID"
                if target_id in context.user_contexts:
                    context.user_contexts[target_id] = [{"role": "system", "content": config["GLOBAL_SYSTEM_PROMPT"]}]
                    context.user_contexts_timestamp[target_id] = time.time()
                    context.contexts_dirty = True
                    logger.info(f"🔧 管理员清空了用户 {privacy_manager.mask_user_id(target_id)} 的上下文",
                              extra={"user_id": user_id})
                    return f"✅ 管理员操作：用户 {privacy_manager.mask_user_id(target_id)} 的上下文已清空"
                else:
                    return f"ℹ️ 用户 {privacy_manager.mask_user_id(target_id)} 没有对话记录"
            except Exception:
                return "❌ 格式错误，请使用：/清空用户上下文 用户ID"

        # 查看在线用户数
        if msg.strip() in ["/查看在线用户", "/用户数", "/在线人数", "/查看在线人数"]:
            user_count = len(context.user_contexts)
            return f"👥 当前有对话记录的用户数：{user_count}个"

        # 设置最大上下文
        if msg.startswith("/设置最大上下文"):
            try:
                new_limit = int(msg.split("/设置最大上下文")[1].strip())
                if new_limit < 1 or new_limit > 50:
                    return "❌ 最大上下文范围需在1-50之间"
                context.max_context_length = new_limit
                logger.info(f"🔧 管理员将最大上下文设置为{new_limit}轮", extra={"user_id": user_id})
                return f"✅ 已将单用户最大上下文设置为：{new_limit}轮"
            except Exception:
                return "❌ 格式错误，请使用：/设置最大上下文 数字（1-50）"

    # 关键词回复
    keyword_reply = check_keyword(msg)
    if keyword_reply:
        return keyword_reply

    # 表情/短内容过滤
    if re.fullmatch(r'(<faceType[^>]+>)+', msg.strip()):
        return "😊 收到你的表情啦～有什么问题可以直接对我说哦"
    if re.fullmatch(r'[0-9a-zA-Z\s]+', msg.strip()) and len(msg.strip()) < 3:
        return "请输入完整的问题，我会尽力为你解答~"

    # 初始化用户上下文
    if user_id not in context.user_contexts:
        context.user_contexts[user_id] = [{"role": "system", "content": config["GLOBAL_SYSTEM_PROMPT"]}]
    context.user_contexts[user_id].append({"role": "user", "content": msg})

    # 上下文截断
    if len(context.user_contexts[user_id]) > context.max_context_length * 2 + 1:
        system_msg = context.user_contexts[user_id][0]
        rest_msgs = context.user_contexts[user_id][1:]
        if len(rest_msgs) > context.max_context_length * 2:
            rest_msgs = rest_msgs[-context.max_context_length * 2:]
        context.user_contexts[user_id] = [system_msg] + rest_msgs
        logger.info(f"🔄 用户 {privacy_manager.mask_user_id(user_id)} 上下文超出限制，已截断至{context.max_context_length}轮",
                   extra={"user_id": user_id})

    # 调用AI生成回复
    try:
        logger.debug(f"发送请求到Ollama，用户：{privacy_manager.mask_user_id(user_id)}",
                    extra={"user_id": "system"})
        raw_reply = await ollama_chat_request(session, context.user_contexts[user_id], user_id)
        reply = remove_thinking(raw_reply)
        if not reply or len(reply.strip()) == 0:
            reply = raw_reply.strip()
        if not reply or len(reply.strip()) == 0:
            reply = "我收到了你的消息，但暂时无法给出有效回复，请换个问题试试~"
        context.user_contexts[user_id].append({"role": "assistant", "content": reply})
        context.contexts_dirty = True
        return reply
    except Exception as e:
        if context.user_contexts.get(user_id) and context.user_contexts[user_id][-1]["role"] == "user":
            context.user_contexts[user_id].pop()
        logger.error(f"【Ollama回复异常】错误类型:{type(e).__name__} | 详情:{str(e)}",
                     exc_info=True, extra={"user_id": user_id})
        raise e
