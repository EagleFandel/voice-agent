@echo off
rem 停止超脑语音助手 Demo 的所有组件
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8899 " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7860 " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq VoiceAgent-Worker*" /F >nul 2>&1
docker stop livekit-server >nul 2>&1
echo 已全部停止。
pause
