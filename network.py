import time
import json
import aiohttp
from typing import List, Tuple, Optional
from config import config
from privacy_log import logger, privacy_manager
from utils import retry, TokenExpiredError

# ===================== 全局状态 =====================
token_state: dict = {"value": "", "expire": 0}
ws_connection = None
bot_id: str = ""
bot_online_status = False


# ===================== HTTP请求上报 =====================
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
        if config.get("PRIVACY_ENABLE_MODE", True):
            if req_body:
                req_body = privacy_manager.mask_sensitive_content(req_body)
                req_body = privacy_manager.anonymize_message(req_body)
            if resp_body:
                resp_body = privacy_manager.mask_sensitive_content(resp_body)
                resp_body = privacy_manager.anonymize_message(resp_body)
            if user_id != "system":
                user_id = privacy_manager.mask_user_id(user_id)

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
                async with session.post(config["REPORT_BACKEND_URL"], json=payload, timeout=timeout) as res:
                    logger.debug(f"【后台上报】场景:{scene} 状态:{res.status}", extra={"user_id": "system"})
            except Exception:
                pass
        asyncio.create_task(_do_report())
    except Exception:
        pass


import asyncio


# ===================== Token管理 =====================
@retry(max_retries=2, delay=1)
async def refresh_token_if_needed(session: aiohttp.ClientSession):
    """检查全局token是否即将过期，提前300秒刷新"""
    if time.time() < token_state["expire"] - 300:
        return
    new_token, new_expire = await get_token(session)
    token_state["value"] = new_token
    token_state["expire"] = new_expire
    logger.info("🔄 预刷新访问令牌，避免会话超时", extra={"user_id": "system"})


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
    except Exception as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_token", "POST", url,
            req_body=req_body_str,
            resp_status=getattr(e, 'status', None),
            resp_body=str(e),
            cost_ms=cost_ms
        )
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
    except Exception as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_gateway", "GET", url,
            resp_status=getattr(e, 'status', None),
            resp_body=str(e),
            cost_ms=cost_ms
        )
        raise e


# ===================== 模型列表 =====================
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
    except Exception as e:
        cost_ms = (time.time() - start_time) * 1000
        await report_http_request(
            session, "get_models", "GET", url,
            resp_status=getattr(e, 'status', None),
            resp_body=str(e),
            cost_ms=cost_ms
        )
        raise e


# ===================== 消息发送 =====================
@retry(max_retries=3, delay=1)
async def send_msg(
    session: aiohttp.ClientSession, token: str, event_type: str,
    openid: str = None, guild_id: str = None, channel_id: str = None,
    content: str = None, user_id: str = "unknown",
    msg_id: str = None, group_openid: str = None
) -> bool:
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
    url = ""
    start_time = time.time()

    def build_payload(use_msg_id: bool) -> dict:
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
                err_code = None
                err_msg = ""
                try:
                    resp_json = json.loads(resp_text)
                    err_code = resp_json.get("code") or resp_json.get("err_code")
                    err_msg = resp_json.get("message", "")
                except Exception:
                    err_msg = resp_text[:200]
                return False, err_code, err_msg
        except Exception as e:
            raise e

    # 构造目标地址
    try:
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            if not group_openid:
                raise ValueError("群消息缺少group_openid参数")
            url = f"https://api.sgroup.qq.com/v2/groups/{group_openid}/messages"
        elif event_type == "C2C_MESSAGE_CREATE":
            if not openid:
                raise ValueError("C2C消息缺少openid参数")
            url = f"https://api.sgroup.qq.com/v2/users/{openid}/messages"
        elif event_type == "DIRECT_MESSAGE_CREATE":
            if not guild_id:
                raise ValueError("私信消息缺少guild_id参数")
            url = f"https://api.sgroup.qq.com/v2/dms/{guild_id}/messages"
        else:
            if not channel_id:
                raise ValueError("群消息缺少channel_id参数")
            url = f"https://api.sgroup.qq.com/v2/channels/{channel_id}/messages"
    except ValueError as e:
        logger.error(f"【消息参数错误】 | {e}", extra={"user_id": user_id})
        return False

    # 带msg_id发送
    success, err_code, err_msg = await _do_send(build_payload(use_msg_id=True))
    if success:
        return True

    # 去重错误，移除msg_id重试
    if err_code == 40054005:
        logger.warning(f"【消息触发去重】，自动移除msg_id重试", extra={"user_id": user_id})
        success, err_code, err_msg = await _do_send(build_payload(use_msg_id=False))
        if success:
            return True

    # 不可重试错误
    unrecoverable_codes = {40034105, 40054005, 40013002, 401, 403}
    if err_code in unrecoverable_codes:
        logger.error(f"【消息发送失败】错误码:{err_code} | 原因:{err_msg}", extra={"user_id": user_id})
        return False

    raise Exception(f"发送失败，错误码:{err_code}，原因:{err_msg}")


async def send_long_message(
    session: aiohttp.ClientSession, token: str, event_type: str,
    openid: str = None, guild_id: str = None, channel_id: str = None,
    content: str = None, user_id: str = "unknown",
    msg_id: str = None, group_openid: str = None
) -> bool:
    if len(content) <= config["MESSAGE_MAX_LENGTH"]:
        return await send_msg(
            session, token, event_type,
            openid, guild_id, channel_id, content, user_id,
            msg_id=msg_id, group_openid=group_openid
        )

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
        current_msg_id = msg_id if i == 0 else None
        if await send_msg(
            session, token, event_type,
            openid, guild_id, channel_id, chunk_content, user_id,
            msg_id=current_msg_id, group_openid=group_openid
        ):
            success_count += 1
            await asyncio.sleep(0.5)
    return success_count == len(chunks)


# ===================== Ollama对话接口 =====================
async def ollama_chat_request(session: aiohttp.ClientSession, messages: list, user_id: str):
    """调用Ollama对话接口，返回原始回复（全局串行，同一时间仅1个请求）"""
    # 获取全局串行锁，同一时间只有一个请求能进入下面的推理逻辑
    async with context.ollama_semaphore:
        start_time = time.time()
        payload = {"model": context.current_model, "messages": messages, "stream": False}
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
            res.raise_for_status()
            result = await res.json()
            if "message" not in result or "content" not in result["message"]:
                raise Exception("Ollama返回格式错误，缺少message/content字段")
            return result["message"]["content"]


import context
