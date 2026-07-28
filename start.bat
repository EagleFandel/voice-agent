@echo off
rem ============================================================
rem  超脑语音助手 Demo 一键启动（LiveKit 全链路）
rem  启动 4 个组件：Docker LiveKit Server / Agent Worker /
rem                Token+静态页服务器 / FastRTC 桥接前端
rem ============================================================
setlocal
set ROOT=%~dp0

rem 清掉本机失效的 Clash 代理环境变量（会导致 WS/HTTP 连接失败）
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=

echo [1/4] 启动 Docker LiveKit Server (:7880) ...
docker start livekit-server >nul 2>&1
if errorlevel 1 (
    echo     容器不存在，创建新容器 ...
    docker run -d --name livekit-server -p 7880:7880 -p 7881:7881 -p 50000-50060:50000-50060/udp livekit/livekit-server --dev --node-ip 127.0.0.1 >nul
)

echo [2/4] 启动 Agent Worker (livekit-agents + 百炼 realtime) ...
rem 注意：子窗口继承本窗口已清空的代理变量，无需再 set（set 会吞掉 && 后面的命令）
start "VoiceAgent-Worker" /min cmd /k "cd /d %ROOT%livekit-demo && venv\Scripts\python.exe minimal_agent.py dev"

echo [3/4] 启动 Token Server + 脉冲球前端 (:8899) ...
start "VoiceAgent-Token" /min cmd /k "cd /d %ROOT%livekit-demo && venv\Scripts\python.exe token_server.py"

echo [4/4] 启动 FastRTC 电话式前端 + 桥接 (:7860) ...
start "VoiceAgent-Bridge" /min cmd /k "cd /d %ROOT%fastrtc-demo && venv\Scripts\python.exe livekit_bridge_chat.py"

echo.
echo ============================================================
echo  启动完成，等约 10 秒后访问：
echo.
echo    架构图 + 能力分析：  http://localhost:8899/architecture.html
echo    Demo A 脉冲球前端：  http://localhost:8899
echo    Demo B 电话式前端：  http://localhost:7860
echo.
echo  停止所有服务：双击 stop.bat
echo ============================================================
pause
