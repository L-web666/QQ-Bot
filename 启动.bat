@echo off
title QQ机器人一键启动
mode con cols=60 lines=12

echo ============================================
echo           QQ机器人 一键启动脚本
echo ============================================
echo.
echo  即将依次启动3个程序（独立窗口运行）：
echo   1. Ollama 模型服务
echo   2. 后台接收服务
echo   3. QQ机器人主程序
echo.
echo  每个程序启动间自动等待，避免启动冲突
echo.
pause

cd /d "%~dp0"

:: 1. 启动 Ollama 模型服务
echo [1/3] 正在启动 Ollama 模型服务...
start "Ollama模型服务" cmd /k "ollama serve"
timeout /t 6 /nobreak >nul
echo       Ollama 服务窗口已启动

:: 2. 启动后台接收服务
echo [2/3] 正在启动 后台接收服务...
start "后台接收服务:54188" cmd /k "python backend.py"
timeout /t 2 /nobreak >nul
echo       后台服务窗口已启动

:: 3. 启动 QQ 机器人主程序
echo [3/3] 正在启动 QQ机器人主程序...
start "QQ机器人主程序" cmd /k "python main.py"
timeout /t 2 /nobreak >nul
echo       机器人窗口已启动

echo.
echo ============================================
echo  所有程序均已启动完成
echo  各程序在独立窗口运行，关闭对应窗口即可停止
echo.
pause
exit
