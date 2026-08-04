#!/bin/bash
# 容器入口:启动 Xvfb 虚拟显示器,然后起 uvicorn。
# patchright 有头模式需要 DISPLAY,headless 不需要但实测过不了 Cloudflare,所以默认有头+Xvfb。
set -e

echo "[entrypoint] 启动 Xvfb 虚拟显示器(:99)..."
# 后台起 Xvfb,1280x800x24 色深足够 patchright 有头渲染
Xvfb :99 -screen 0 1280x800x24 -ac -noreset &
XVFB_PID=$!

# 给 Xvfb 一点启动时间
sleep 1
if kill -0 $XVFB_PID 2>/dev/null; then
    echo "[entrypoint] Xvfb 已启动 (pid=$XVFB_PID)"
else
    echo "[entrypoint] 警告: Xvfb 启动失败,将尝试无显示器模式(可能过不了 Cloudflare)"
fi

echo "[entrypoint] 启动 uvicorn (0.0.0.0:2464)..."
# exec 让 uvicorn 接管 PID 1(tini 已是 PID 1,这里 exec 替换当前 shell)
exec uvicorn app.main:app --host 0.0.0.0 --port 2464 --workers 1
