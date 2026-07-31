import asyncio
from config import load_config
from bot_core import run
from privacy_log import logger
from context import main_loop_running, save_contexts


async def main():
    global main_loop_running
    load_config()
    try:
        await run()
    except KeyboardInterrupt:
        logger.info("👋 收到退出信号，机器人正在安全退出...", extra={"user_id": "system"})
        main_loop_running = False
    except asyncio.CancelledError:
        logger.info("👋 收到退出信号，机器人正在安全退出...", extra={"user_id": "system"})
        main_loop_running = False
    except Exception as e:
        logger.critical(f"程序异常终止：{type(e).__name__}: {e}", exc_info=True, extra={"user_id": "system"})
        main_loop_running = False
    finally:
        main_loop_running = False
        if __import__("config").config.get("ENABLE_CONTEXT_PERSISTENCE", False):
            save_contexts()
            logger.info("💾 对话上下文已保存", extra={"user_id": "system"})
        logger.info("✅ 程序已完全退出", extra={"user_id": "system"})


if __name__ == "__main__":
    asyncio.run(main())
