import os
import base64
import json
import aiohttp
from functools import wraps
from aiohttp import web
from config import config, DEFAULT_CONFIG_PATH, WEB_ADMIN_HTML_PATH, is_config_hot_updateable
from privacy_log import logger
import context
import network
from context import apply_hot_config, save_contexts

web_admin_running = False


# ===================== 中间件 =====================
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


# ===================== 认证 =====================
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


# ===================== API接口 =====================
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
        "bot_online": network.bot_online_status,
        "bot_id": network.bot_id or "未连接",
        "current_model": context.current_model or "未加载",
        "max_context": context.max_context_length,
        "user_count": len(context.user_contexts),
        "processed_msg_count": len(context.processed_messages),
        "ollama_url": config["OLLAMA_URL"],
        "group_at_filter": config["ENABLE_GROUP_AT_FILTER"],
        "report_enabled": config["ENABLE_REPORT"],
        "privacy_enabled": config.get("PRIVACY_ENABLE_MODE", True)
    })


@auth_required
async def api_clear_all_context(request: web.Request):
    context.user_contexts.clear()
    context.user_contexts_timestamp.clear()
    context.contexts_dirty = True
    save_contexts()
    logger.info("【后台管理】已清空所有用户上下文", extra={"user_id": "web_admin"})
    return web.json_response({"success": True, "msg": "所有上下文已清空"})


@auth_required
async def api_get_models(request: web.Request):
    """获取Ollama所有可用模型列表"""
    try:
        async with aiohttp.ClientSession() as session:
            from network import get_available_models
            models = await get_available_models(session)
            return web.json_response({
                "success": True,
                "models": models,
                "current": context.current_model
            })
    except Exception as e:
        logger.error(f"获取模型列表失败：{e}", extra={"user_id": "web_admin"})
        return web.json_response({
            "success": False,
            "msg": f"获取模型失败：{str(e)}"
        }, status=500)


@auth_required
async def api_switch_model(request: web.Request):
    """在线切换运行模型，热更新生效，自动清空所有上下文"""
    try:
        body = await request.json()
        target_model = body.get("model", "").strip()
    except Exception:
        return web.json_response({"success": False, "msg": "请求格式错误"}, status=400)
    
    if not target_model:
        return web.json_response({"success": False, "msg": "模型名称不能为空"}, status=400)
    
    try:
        # 验证模型是否存在
        async with aiohttp.ClientSession() as session:
            from network import get_available_models
            models = await get_available_models(session)
            if target_model not in models:
                return web.json_response({
                    "success": False, 
                    "msg": f"模型 {target_model} 不存在，可用模型：{', '.join(models)}"
                }, status=400)
        
        # 真正修改全局模型变量，所有模块实时生效
        context.current_model = target_model
        # 清空所有用户上下文
        context.user_contexts.clear()
        context.user_contexts_timestamp.clear()
        context.contexts_dirty = True
        save_contexts()
        
        logger.info(f"【后台管理】已切换模型为：{target_model}", extra={"user_id": "web_admin"})
        return web.json_response({
            "success": True,
            "msg": f"已成功切换模型为：{target_model}，所有上下文已清空"
        })
    except Exception as e:
        logger.error(f"切换模型失败：{e}", extra={"user_id": "web_admin"})
        return web.json_response({
            "success": False,
            "msg": f"切换失败：{str(e)}"
        }, status=500)


# ===================== 页面 =====================
async def admin_page(request: web.Request):
    if not check_auth(request):
        return web.Response(
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="QQBot Admin"'},
            text="需要登录"
        )

    try:
        with open(WEB_ADMIN_HTML_PATH, 'r', encoding='utf-8') as f:
            html_content = f.read()
        auth_header = request.headers.get("Authorization", "")
        html_content = html_content.replace("{{AUTH_HEADER}}", auth_header)
        return web.Response(text=html_content, content_type="text/html", charset="utf-8")
    except FileNotFoundError:
        logger.error(f"后台管理页面文件不存在：{WEB_ADMIN_HTML_PATH}", extra={"user_id": "system"})
        return web.Response(
            text="<h1>错误</h1><p>后台管理页面文件不存在，请确保 web_admin.html 文件在程序目录下</p>",
            content_type="text/html",
            charset="utf-8", status=500
        )
    except Exception as e:
        logger.error(f"读取后台管理页面失败：{e}", extra={"user_id": "system"})
        return web.Response(
            text=f"<h1>错误</h1><p>读取页面文件失败：{str(e)}</p>",
            content_type="text/html",
            charset="utf-8", status=500
        )


# ===================== 启动 =====================
async def start_web_admin():
    global web_admin_running
    if not config["ENABLE_WEB_ADMIN"]:
        logger.info("ℹ️ 后台管理网站已关闭", extra={"user_id": "system"})
        return

    if not os.path.exists(WEB_ADMIN_HTML_PATH):
        logger.warning(f"⚠️ 后台管理页面文件不存在：{WEB_ADMIN_HTML_PATH}", extra={"user_id": "system"})

    app = web.Application(middlewares=[error_middleware])

    async def favicon_handler(request):
        return web.Response(status=204)

    app.router.add_get('/favicon.ico', favicon_handler)
    app.router.add_get('/', admin_page)
    app.router.add_get('/api/config', api_get_config)
    app.router.add_post('/api/config', api_update_config)
    app.router.add_get('/api/status', api_get_status)
    app.router.add_post('/api/clear-all-context', api_clear_all_context)
    app.router.add_get('/api/models', api_get_models)
    app.router.add_post('/api/switch-model', api_switch_model)

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
        logger.info(f"🔒 隐私保护模式：{'已启用' if config.get('PRIVACY_ENABLE_MODE', True) else '已禁用'}", extra={"user_id": "system"})
    except Exception as e:
        logger.error(f"❌ 后台网站启动失败：{type(e).__name__}: {e}", exc_info=True, extra={"user_id": "system"})
