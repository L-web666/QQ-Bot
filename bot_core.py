import asyncio
import json
import sys
import time
import re
import websockets
import aiohttp
import context
import network
from config import config
from privacy_log import logger, privacy_manager
from web_admin import start_web_admin
from utils import TokenExpiredError
from ai import ollama_reply


async def run():
    # 所有跨模块全局变量通过模块名访问，避免局部变量遮蔽
    sys.stderr.write("=== ENTERING run() ===\n")
    sys.stderr.flush()

    try:
        async with aiohttp.ClientSession() as session:
            await start_web_admin()

            # 配置校验
            logger.info("正在校验配置...", extra={"user_id": "system"})
            if not config["APPID"] or not config["APPSECRET"] or config["APPID"] == "你的APPID":
                logger.error("❌ 请在config.json中填写APPID和APPSECRET", extra={"user_id": "system"})
                return
            try:
                models = await network.get_available_models(session)
                logger.info(f"✅ Ollama可用模型：{models}", extra={"user_id": "system"})
                if config["DEFAULT_MODEL"] not in models:
                    logger.error(f"❌ 默认模型 {config['DEFAULT_MODEL']} 不存在，请先运行 ollama pull {config['DEFAULT_MODEL']}",
                                 extra={"user_id": "system"})
                    return
            except Exception as e:
                logger.error(f"❌ 无法连接Ollama服务：{e}", extra={"user_id": "system"})
                logger.error("请确保Ollama已启动并运行 ollama serve", extra={"user_id": "system"})
                return
            logger.info(f"✅ 已加载 {len(config['ADMIN_IDS'])} 个管理员账号", extra={"user_id": "system"})
            logger.info("✅ 所有配置校验通过", extra={"user_id": "system"})

            context.load_contexts()
            flush_task = asyncio.create_task(context.periodic_flush_contexts())
            privacy_cleanup_task = asyncio.create_task(context.periodic_privacy_cleanup())

            reconnect_count = 0
            heartbeat_task = None

            # 外层主循环
            while context.main_loop_running and reconnect_count < config["MAX_RECONNECT"]:
                sys.stderr.write(f"🔁 外层循环开始: reconnect_count={reconnect_count}, main_loop_running={context.main_loop_running}\n")
                sys.stderr.flush()

                if reconnect_count > 0:
                    logger.info(f"🔌 开始第 {reconnect_count} 次自动重连...", extra={"user_id": "system"})

                try:
                    # 获取/刷新令牌
                    if time.time() > network.token_state["expire"]:
                        token, token_expire = await network.get_token(session)
                        network.token_state["value"] = token
                        network.token_state["expire"] = token_expire
                        logger.info("🔄 已刷新访问令牌", extra={"user_id": "system"})

                    # 获取网关地址
                    try:
                        gateway = await network.get_gateway(session, network.token_state["value"])
                    except TokenExpiredError:
                        logger.info("🔄 令牌过期，重新获取后再连接网关", extra={"user_id": "system"})
                        token, token_expire = await network.get_token(session)
                        network.token_state["value"] = token
                        network.token_state["expire"] = token_expire
                        gateway = await network.get_gateway(session, network.token_state["value"])

                    logger.info("✅ 正在连接QQ网关...", extra={"user_id": "system"})

                    # 建立WebSocket连接
                    async with websockets.connect(
                        gateway,
                        ping_interval=None,
                        ping_timeout=None,
                        close_timeout=10,
                        max_size=20*1024*1024,
                        compression=None
                    ) as ws:
                        network.ws_connection = ws
                        heartbeat_interval = None
                        last_heartbeat_ack = time.time()
                        last_seq = None
                        connection_error = None
                        token_refresh_task_obj = None

                        # Token刷新任务
                        async def token_refresh_and_reconnect():
                            nonlocal reconnect_count
                            while context.main_loop_running and not ws.closed:
                                try:
                                    # 检测是否提前需要刷新
                                    if time.time() > network.token_state["expire"] - 600:
                                        await asyncio.sleep(60)
                                    else:
                                        await asyncio.sleep(120)
                                    if ws.closed:
                                        break
                                    # Token过期前刷新
                                    if time.time() > network.token_state["expire"]:
                                        logger.info("🔄 Token即将过期，开始刷新...", extra={"user_id": "system"})
                                        try:
                                            new_token, new_expire = await network.get_token(session)
                                            network.token_state["value"] = new_token
                                            network.token_state["expire"] = new_expire
                                            logger.info("🔄 Token已刷新", extra={"user_id": "system"})
                                            # 删掉关闭ws的代码，保留长连接不断线
                                        except Exception as e:
                                            logger.error(f"刷新Token失败：{e}", extra={"user_id": "system"})
                                            await asyncio.sleep(30)
                                except Exception as e:
                                    logger.error(f"Token刷新任务异常：{e}", extra={"user_id": "system"})
                                    await asyncio.sleep(30)

                        token_refresh_task_obj = asyncio.create_task(token_refresh_and_reconnect())

                        # 内层消息循环
                        while context.main_loop_running and not ws.closed:
                            try:
                                try:
                                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                                except asyncio.TimeoutError:
                                    if ws.closed:
                                        logger.warning("⚠️ 连接已关闭", extra={"user_id": "system"})
                                        break
                                    if time.time() > network.token_state["expire"] - 600:
                                        logger.info("🔄 Token即将过期，主动刷新", extra={"user_id": "system"})
                                        if not ws.closed:
                                            await ws.close(code=1000, reason="Token refresh needed")
                                            break
                                    continue

                                if not raw:
                                    logger.warning("⚠️ 收到空消息，连接可能已断开", extra={"user_id": "system"})
                                    break

                                data = json.loads(raw)
                                op = data.get("op")
                                event_type = data.get("t")

                                if "s" in data and data["s"] is not None:
                                    if data["s"] != last_seq:
                                        last_seq = data["s"]

                                # 收到Hello包，启动心跳
                                if op == 10:
                                    heartbeat_interval = data["d"]["heartbeat_interval"] / 1000 * config["HEARTBEAT_RATIO"]
                                    logger.info(f"💓 收到官方心跳间隔：{data['d']['heartbeat_interval']/1000:.1f}秒，调整为：{heartbeat_interval:.1f}秒",
                                                extra={"user_id": "system"})

                                    async def heartbeat():
                                        nonlocal last_heartbeat_ack, last_seq
                                        while context.main_loop_running and not ws.closed:
                                            try:
                                                heartbeat_packet = {"op": 1, "d": last_seq}
                                                await ws.send(json.dumps(heartbeat_packet))
                                                await asyncio.sleep(heartbeat_interval * 0.5)
                                                if time.time() - last_heartbeat_ack > heartbeat_interval * 1.5:
                                                    logger.warning("⚠️ 心跳确认超时，主动断开重连", extra={"user_id": "system"})
                                                    await ws.close()
                                                    break
                                                await asyncio.sleep(heartbeat_interval * 0.5)
                                                if time.time() - last_heartbeat_ack > heartbeat_interval * 1.5:
                                                    logger.warning("⚠️ 心跳确认超时，主动断开重连", extra={"user_id": "system"})
                                                    await ws.close()
                                                    break
                                            except Exception as e:
                                                logger.error(f"心跳任务异常：{e}", extra={"user_id": "system"})
                                                break

                                    heartbeat_task = asyncio.create_task(heartbeat())

                                    # 发送认证
                                    intents = 1 + 2 + 4096 + 33554432
                                    auth = {
                                        "op": 2,
                                        "d": {
                                            "token": f"QQBot {network.token_state['value']}",
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
                                    network.bot_id = data["d"]["user"]["id"]
                                    network.bot_online_status = True
                                    reconnect_count = 0
                                    logger.info(f"✅ 机器人已上线！ID：{privacy_manager.mask_user_id(network.bot_id)}",
                                                extra={"user_id": "system"})
                                    logger.info(f"🤖 当前使用模型：{context.current_model}",
                                                extra={"user_id": "system"})

                                # 处理消息事件
                                elif event_type in ("C2C_MESSAGE_CREATE", "AT_MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"):
                                    msg_id = data["d"]["id"]
                                    if msg_id in context.processed_messages:
                                        continue

                                    # 队列维护顺序，超过上限淘汰最旧ID
                                    context.processed_messages.add(msg_id)
                                    context.msg_id_queue.append(msg_id)
                                    if len(context.msg_id_queue) > 5000:
                                        for _ in range(2500):
                                            oldest_id = context.msg_id_queue.popleft()
                                            context.processed_messages.discard(oldest_id)

                                    user_msg = data["d"]["content"].strip()

                                    # 提取用户ID
                                    if event_type == "GROUP_AT_MESSAGE_CREATE":
                                        user_id = data["d"].get("author", {}).get("member_openid") or data["d"].get("author", {}).get("id", "unknown")
                                    else:
                                        user_id = data["d"]["author"]["id"]
                                    is_admin = user_id in config["ADMIN_IDS"]

                                    # 提取场景参数
                                    openid = user_id if event_type == "C2C_MESSAGE_CREATE" else None
                                    guild_id = data["d"].get("src_guild_id") if event_type == "DIRECT_MESSAGE_CREATE" else None
                                    group_openid = data["d"].get("group_openid") if event_type == "GROUP_AT_MESSAGE_CREATE" else None
                                    channel_id = data["d"].get("channel_id") if event_type not in ("C2C_MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE") else None

                                    # 群聊@过滤
                                    if event_type == "AT_MESSAGE_CREATE" and config["ENABLE_GROUP_AT_FILTER"]:
                                        mentions = data["d"].get("mentions", [])
                                        is_mentioned = any(mention["id"] == network.bot_id for mention in mentions)
                                        if not is_mentioned:
                                            continue
                                        user_msg = re.sub(r'<@!?{}>'.format(network.bot_id), '', user_msg).strip()
                                        if not user_msg:
                                            continue

                                    if event_type == "GROUP_AT_MESSAGE_CREATE":
                                        user_msg = re.sub(r'<@!?{}>'.format(network.bot_id), '', user_msg).strip()
                                        if not user_msg:
                                            continue

                                    logger.info(f"📩 收到消息（已隐私保护）", extra={"user_id": user_id})

                                    # 异步处理回复
                                    async def handle_reply(
                                        session, token_st, event_type, openid, guild_id,
                                        channel_id, user_id, user_msg, is_admin, orig_msg_id, group_openid=None
                                    ):
                                        try:
                                            reply = await ollama_reply(session, user_id, user_msg, is_admin)
                                            if not reply or len(reply.strip()) == 0:
                                                reply = "我收到了你的消息，但暂时无法回复"
                                            current_token = token_st["value"]
                                            send_success = await network.send_long_message(
                                                session, current_token, event_type,
                                                openid, guild_id, channel_id,
                                                reply, user_id, msg_id=orig_msg_id,
                                                group_openid=group_openid
                                            )
                                            if send_success:
                                                logger.info(f"📤 发送回复成功", extra={"user_id": user_id})
                                            else:
                                                logger.error(f"📤 发送回复最终失败（重试耗尽）", extra={"user_id": user_id})
                                                try:
                                                    await network.send_msg(
                                                        session, token_st["value"], event_type,
                                                        openid, guild_id, channel_id,
                                                        "⚠️ 消息发送失败，请稍后重试", user_id,
                                                        group_openid=group_openid
                                                    )
                                                except Exception:
                                                    pass
                                        except Exception as e:
                                            error_msg = f"⚠️ AI服务暂时不可用：{str(e)[:50]}..."
                                            logger.error(f"生成回复最终失败 | 错误类型:{type(e).__name__}",
                                                         exc_info=True, extra={"user_id": user_id})
                                            try:
                                                await network.send_msg(
                                                    session, token_st["value"], event_type,
                                                    openid, guild_id, channel_id,
                                                    error_msg, user_id,
                                                    group_openid=group_openid
                                                )
                                            except Exception:
                                                pass

                                    asyncio.create_task(handle_reply(
                                        session, network.token_state, event_type,
                                        openid, guild_id, channel_id,
                                        user_id, user_msg, is_admin, msg_id,
                                        group_openid=group_openid
                                    ))

                            except websockets.exceptions.ConnectionClosedOK as e:
                                logger.warning(f"连接正常关闭：{e.code} {e.reason}", extra={"user_id": "system"})
                                if e.reason not in ("Token refreshed", "Token refresh needed"):
                                    reconnect_count += 1
                                connection_error = e
                                break

                            except websockets.exceptions.ConnectionClosedError as e:
                                logger.warning(f"连接异常断开：错误码{e.code} | 原因:{e.reason}", extra={"user_id": "system"})
                                if e.code == 4009:
                                    logger.info("🔄 检测到4009超时，尝试刷新token", extra={"user_id": "system"})
                                    try:
                                        new_token, new_expire = await network.get_token(session)
                                        network.token_state["value"] = new_token
                                        network.token_state["expire"] = new_expire
                                        logger.info("🔄 Token已刷新", extra={"user_id": "system"})
                                    except Exception as token_err:
                                        logger.error(f"刷新Token失败：{token_err}", extra={"user_id": "system"})
                                reconnect_count += 1
                                connection_error = e
                                break

                            except json.JSONDecodeError as e:
                                logger.error(f"消息解析失败：{e}", extra={"user_id": "system"})
                                continue

                            except Exception as e:
                                logger.error(f"消息处理异常：{type(e).__name__}: {e}", exc_info=True, extra={"user_id": "system"})
                                continue

                        # 内层循环结束清理
                        if token_refresh_task_obj and not token_refresh_task_obj.done():
                            token_refresh_task_obj.cancel()
                            try:
                                await token_refresh_task_obj
                            except:
                                pass

                        if connection_error and hasattr(connection_error, 'reason') and connection_error.reason == "Token refreshed":
                            logger.info("🔄 Token刷新完成，继续外层循环", extra={"user_id": "system"})
                            continue

                except TokenExpiredError:
                    network.bot_online_status = False
                    reconnect_count += 1
                    logger.info("🔄 令牌已过期，刷新后重连", extra={"user_id": "system"})

                except asyncio.CancelledError:
                    logger.warning("⛔ run() 收到取消信号", extra={"user_id": "system"})
                    raise

                except Exception as e:
                    network.bot_online_status = False
                    reconnect_count += 1
                    logger.error(f"连接/重连失败（第{reconnect_count}次）：{type(e).__name__}: {e}",
                                 exc_info=True, extra={"user_id": "system"})

                finally:
                    sys.stderr.write("=== ENTERING INNER FINALLY ===\n")
                    sys.stderr.flush()

                    try:
                        if heartbeat_task and not heartbeat_task.done():
                            heartbeat_task.cancel()
                            await heartbeat_task
                    except BaseException:
                        pass

                    try:
                        if config.get("ENABLE_CONTEXT_PERSISTENCE", False):
                            context.save_contexts()
                    except BaseException:
                        pass

                    try:
                        if context.main_loop_running:
                            delay = config["RECONNECT_DELAY"]
                            logger.info(f"⏳ 等待 {delay} 秒后尝试重连...", extra={"user_id": "system"})
                            await asyncio.sleep(delay)
                    except BaseException:
                        pass

            # 外层循环退出
            if config.get("ENABLE_CONTEXT_PERSISTENCE", False):
                context.save_contexts()
                logger.info("💾 退出前已保存对话上下文", extra={"user_id": "system"})

            context.main_loop_running = False
            try:
                flush_task.cancel()
                privacy_cleanup_task.cancel()
                await flush_task
                await privacy_cleanup_task
            except Exception:
                pass

    except Exception as e:
        sys.stderr.write(f"❌ run() 捕获到未处理的异常: {e}\n")
        sys.stderr.flush()
        logger.critical(f"run() 未处理异常: {e}", exc_info=True)
        raise
    finally:
        sys.stderr.write("=== FINAL FINALLY in run() ===\n")
        sys.stderr.flush()
        if config.get("ENABLE_CONTEXT_PERSISTENCE", False):
            context.save_contexts()
            logger.info("💾 最终保存上下文", extra={"user_id": "system"})
