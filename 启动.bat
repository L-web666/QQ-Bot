@echo off
title QQ机器人守护进程
:loop
echo [%date% %time%] 启动QQ机器人...
python 1.py
echo [%date% %time%] 程序已退出，10秒后自动重启...
timeout /t 10 /nobreak >nul
goto loop