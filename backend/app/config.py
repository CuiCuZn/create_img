"""应用配置。"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录(backend/):本地为 .../create-img/backend,Docker 里为 /app
# __file__ = <BASE_DIR>/app/config.py,parent.parent 即 BASE_DIR
BASE_DIR = Path(__file__).resolve().parent.parent

# 前端构建产物目录(生产模式下后端托管前端)。
# 优先用环境变量 FRONTEND_DIST 覆盖(Docker / 裸跑均可显式指定,最稳);
# 否则按目录层级自动探测:容器里是 /app/frontend/dist,本地是 .../backend/../frontend/dist。
# 两种结构都覆盖,避免因 backend/ 是否多嵌套一层而找不到 dist。
_FRONTEND_DIST_ENV = os.getenv("FRONTEND_DIST", "").strip()
if _FRONTEND_DIST_ENV:
    FRONTEND_DIST = Path(_FRONTEND_DIST_ENV)
elif (BASE_DIR / "frontend" / "dist").exists():
    # Docker 结构:/app/frontend/dist(BASE_DIR 直接是项目根)
    FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
else:
    # 本地结构:create-img/backend/app/config.py,dist 在 create-img/frontend/dist
    FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

# 风格预设数据文件
ART_STYLES_FILE = BASE_DIR / "app" / "data" / "art_styles.json"

# --- 浏览器代理配置 ---
# 是否使用无头模式。开发调试时可设为 False 观察浏览器行为。
# 注意:headless 下 Turnstile 更易被识别,本地调试过 Turnstile 时建议 False。
# 重要:patchright 在 headless 下过不了 Cloudflare JS 挑战(实测 403),
# 有头(headed)模式才能过。上云时配 Xvfb 虚拟显示器跑有头模式。
# 支持环境变量 HEADLESS 覆盖(上云时按需调整)。
HEADLESS = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")

# 浏览器导航 / evaluate 操作超时(毫秒)。
# verifyUser 与 generate 都可能较慢,留足余量。
NAV_TIMEOUT_MS = 60_000

# 生图整体超时(秒)。perchance 生图通常 10-30 秒,极端情况更久。
GENERATE_TIMEOUT_S = 180

# 生成失败时的最大重试次数(gen_failure / 网络错误)。
MAX_RETRIES = 2

# 等待上一请求完成时的轮询间隔(秒)。
# perchance maxThreadsPerUser=1,同一 userKey 串行,需轮询 waiting_for_prev_request_to_finish。
POLL_INTERVAL_S = 3

# 轮询队列位置的最大等待次数。
POLL_MAX_ATTEMPTS = 20

# --- Turnstile / 反检测配置 ---
# embed 页:perchance 在此加载 Cloudflare Turnstile widget。
# verifyUser 返回 token_required 时,导航到这里触发挑战、取 token。
EMBED_URL = "https://image-generation.perchance.org/embed"

# perchance 的 Turnstile sitekey(固定值,从 embed 页提取)。
# 供 FlareSolverr 等外部求解器使用。
TURNSTILE_SITEKEY = "0x4AAAAAAAadcOX5cAZG6Tf9"

# 判定 Turnstile 已通过的经验阈值:token 写入隐藏 input 后长度通常 ≥80。
TURNSTILE_TOKEN_MIN_LEN = 80

# 轮询 Turnstile token 的最大轮次,每轮间隔 1 秒(对齐 getTurnstileToken 的 20 次)。
TURNSTILE_POLL_MAX_ATTEMPTS = 20

# Turnstile 轮询间隔(秒)。
TURNSTILE_POLL_INTERVAL_S = 1

# 是否在 verifyUser 命中 Cloudflare 挑战页/要求 token 时,主动尝试解 Turnstile。
# 关闭后行为退化为直接抛 AuthenticationError(改前的旧逻辑)。
TURNSTILE_AUTO_SOLVE = True

# 是否应用启动时即预热浏览器(而非首次生图才懒启动)。
# True: 启动即开浏览器窗口(有头模式下启动后窗口持续挂着,避免首次生图才弹的延迟感)。
# False: 首次生图请求时才启动浏览器(懒启动)。
WARMUP_ON_STARTUP = os.getenv("WARMUP_ON_STARTUP", "true").lower() in ("1", "true", "yes")

# --- FlareSolverr 配置 ---
# FlareSolverr 服务地址(留空=不启用)。
# 启用后,遇到 Turnstile 挑战时优先调用 FlareSolverr 求解,失败再回退到浏览器内方案。
# 示例: "http://flaresolverr:8191" (Docker Compose 服务名)
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "").strip()

# FlareSolverr 求解超时(秒)。Turnstile 求解通常 10-60 秒。
FLARESOLVERR_TIMEOUT = int(os.getenv("FLARESOLVERR_TIMEOUT", "60"))

# --- 浏览器持久化配置 ---
# 浏览器持久化数据目录(存 storage_state.json,含 cookies / localStorage)。
# 留空则不持久化(每次重启重新验证)。
# Docker 部署建议挂载 volume 到这个目录,重启后 cf_clearance 自动恢复。
BROWSER_DATA_DIR = os.getenv("BROWSER_DATA_DIR", "").strip()

# --- 指纹增强配置 ---
# 是否启用增强指纹伪造(WebGL / Canvas / Audio / navigator 等)。
# 开启后更接近真实桌面浏览器,提高 Turnstile 无感通过率;
# 如遇兼容性问题可关闭回退到基础反检测。
ENHANCED_FINGERPRINT = os.getenv("ENHANCED_FINGERPRINT", "true").lower() in ("1", "true", "yes")

# 生成 API 基础 URL
IMAGE_GEN_BASE_URL = "https://image-generation.perchance.org/api"

# 默认 channel / subChannel(对应 perchance 公开生成器)
DEFAULT_CHANNEL = "ai-text-to-image-generator"
DEFAULT_SUB_CHANNEL = "public"

# shape -> resolution 映射(perchance 固定分辨率)
SHAPE_RESOLUTION = {
    "portrait": "512x768",
    "square": "768x768",
    "landscape": "768x512",
}
