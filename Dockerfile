# ---------- 1. 前端构建(多阶段,产物给后端镜像打包)----------
FROM node:22-slim AS frontend-build

WORKDIR /build
# 先拷依赖清单,利用 Docker 层缓存
COPY frontend/package.json frontend/package-lock.json* ./
# 删除 Windows 生成的 lock 文件:它把 node_modules 结构(含 .bin 符号链接权限位)
# 冻结成 Windows 状态,在 Linux 容器里复用会导致 "vue-tsc/vite: Permission denied"。
# 删掉后 npm 会按 Linux 方式重新解析依赖、正确创建符号链接。
RUN rm -f package-lock.json && npm install
# 拷源码并构建
COPY frontend/ ./
# 直接用 node 调 vite 的 JS 入口,绕开 .bin/vite 符号链接(双保险):
# Windows lock 残留时 .bin/* 仍可能权限异常,而 node 调 .js 只需读权限,稳。
RUN node node_modules/vite/bin/vite.js build
# 产物在 /build/dist


# ---------- 2. 后端运行时 ----------
FROM python:3.12-slim

# 安装系统依赖:
# - Xvfb:虚拟显示器,让 patchright 有头模式在无物理屏服务器上跑
# - fonts:中文字体 + liberation,避免页面渲染方块
# - chromium 运行时依赖:nss/nspr/atk/gtk 等(patchright install-deps 在容器里可能装不全,这里显式装)
# - tini:轻量 init,正确转发信号(否则 uvicorn 退不出)
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    fonts-liberation fonts-liberation2 fonts-noto-cjk \
    fonts-wqy-microhei fonts-wqy-zenhei \
    fonts-dejavu-core fonts-dejavu-extra \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libxss1 \
    libasound2t64 libatspi2.0-0 libgtk-3-0 libpango-1.0-0 libcairo2 \
    tini ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装 Python 依赖(利用缓存)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 装 patchright 的 patched chromium(过 Cloudflare 的关键)。
# 容器内以 root 跑,直接 --with-deps 一起装系统库。
RUN python -m patchright install --with-deps chromium

# 拷后端代码
COPY backend/ ./

# 拷前端构建产物到后端镜像(main.py 会自动挂载 frontend/dist)
COPY --from=frontend-build /build/dist /app/frontend/dist

# 持久化目录(Docker volume 挂载点,存浏览器 storage_state)
RUN mkdir -p /app/browser_data

# 环境变量默认值(docker-compose.yml 可覆盖)
ENV HEADLESS=false \
    DISPLAY=:99 \
    PYTHONUNBUFFERED=1 \
    # 代理(留空=不走代理;上云后填住宅代理地址以提高过 CF 概率)
    HTTP_PROXY="" \
    HTTPS_PROXY="" \
    # 浏览器持久化数据目录(存 storage_state.json)
    BROWSER_DATA_DIR="/app/browser_data" \
    # 增强指纹伪造(默认开启)
    ENHANCED_FINGERPRINT="true"

# 启动脚本:先起 Xvfb,再起 uvicorn
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 2464

# tini 转发信号,entrypoint 起 Xvfb + uvicorn
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
