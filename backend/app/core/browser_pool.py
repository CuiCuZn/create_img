"""浏览器实例池:单例 Patchright + BrowserContext 复用 + 串行控制。

用 patchright(playwright 的反检测 fork)而非普通 playwright:
patchright 修补了 TLS/JA3 等深层指纹,能通过 Cloudflare 对
image-generation.perchance.org/api 的 JS 挑战(普通 playwright 会被挡在
403 "Just a moment")。实测 headless 仍过不了,有头(headed)才能过,
上云时配 Xvfb 虚拟显示器跑有头模式。

容器内(Docker)注意事项:
- 无 GPU 时 WebGL 指纹异常会被 CF 识别,故加 --use-gl=swiftshader 让 WebGL
  至少能软件渲染(非完美,但比完全没有强)。
- 数据中心 IP 易被判 bot,可通过 HTTP_PROXY/HTTPS_PROXY 环境变量配住宅代理。

perchance 的 maxThreadsPerUser=1,同一 userKey 同时只能生成一张,
并发请求无意义反而触发限流。因此用一个 asyncio.Lock 串行化所有生图请求。
浏览器启动开销大(数秒),复用单个 context 跨请求共享,避免反复冷启动。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from patchright.async_api import Browser, BrowserContext, Playwright, async_playwright

from app import config
from app.core.perchance_client import PerchanceClient

logger = logging.getLogger("perchance.browser_pool")


def _resolve_proxy() -> Optional[dict]:
    """从环境变量解析代理配置,供 patchright new_context(proxy=) 使用。

    优先 HTTPS_PROXY(生图走 https),其次 HTTP_PROXY。
    留空则返回 None(直连)。格式:http://user:pass@host:port
    """
    proxy_url = (os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
                 or os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or "").strip()
    if not proxy_url:
        return None
    logger.info("使用代理: %s", proxy_url)
    return {"server": proxy_url}


# 反检测初始化脚本:在每个页面的所有脚本之前执行(add_init_script 等价于
# Chrome 扩展的 run_at: document_start)。移植自 turnstilePatch/content.js。
# 目的:把无头 Chromium 的自动化痕迹在 Turnstile 检测脚本读到之前洗干净。
# 关键是 webdriver=false —— 这是 Turnstile 最看重的单一信号。
_STEALTH_INIT_SCRIPT = """
(function () {
    "use strict";
    // 1. 隐藏 navigator.webdriver(最关键)
    try {
        Object.defineProperty(navigator, "webdriver", {
            get: function () { return false; }, configurable: true,
        });
    } catch (e) {}
    // 2. 移除 Chrome 自动化 Runtime 通道
    try {
        if (window.chrome && window.chrome.runtime) {
            delete window.chrome.runtime.onConnect;
            delete window.chrome.runtime.onMessage;
        }
    } catch (e) {}
    // 3. 覆盖 permissions.query,隐藏 notifications 权限异常
    try {
        var origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = function (params) {
            if (params.name === "notifications") {
                return Promise.resolve({ state: Notification.permission });
            }
            return origQuery(params);
        };
    } catch (e) {}
    // 4. 伪造 plugins 数量
    try {
        Object.defineProperty(navigator, "plugins", {
            get: function () { return [1, 2, 3, 4, 5]; }, configurable: true,
        });
    } catch (e) {}
    // 5. 伪造 languages
    try {
        Object.defineProperty(navigator, "languages", {
            get: function () { return ["en-US", "en"]; }, configurable: true,
        });
    } catch (e) {}
})();
"""

# 增强指纹伪造脚本(可选,由 ENHANCED_FINGERPRINT 控制是否注入)。
# 针对容器内 swiftshader / 虚拟声卡 / 字体少等问题,伪造更接近真实桌面浏览器的指纹。
# 策略:贴近"最常见的 Windows + Chrome + Intel/NVIDIA GPU"配置,而非随机化。
# 注意:指纹伪造有两面性,伪造不当反而更易被识别;因此默认启用但可关。
_ENHANCED_FINGERPRINT_SCRIPT = """
(function () {
    "use strict";

    // ===== 1. WebGL 指纹伪造 =====
    // 容器内 swiftshader 的 Vendor 是 "Google Inc.",Renderer 是 "SwiftShader",
    // 这在真实桌面浏览器里几乎不存在,极易被识别。
    // 伪造为常见的 Intel UHD 集显(Windows 笔记本最常见配置)。
    try {
        var fakeVendor = "Intel Inc.";
        var fakeRenderer = "Intel(R) UHD Graphics 620";
        var realGetParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (param) {
            // UNMASKED_VENDOR_WEBGL = 0x9245
            if (param === 0x9245) return fakeVendor;
            // UNMASKED_RENDERER_WEBGL = 0x9246
            if (param === 0x9246) return fakeRenderer;
            // VENDOR = 0x1F00
            if (param === 0x1F00) return fakeVendor;
            // RENDERER = 0x1F01
            if (param === 0x1F01) return fakeRenderer;
            // VERSION = 0x1F02
            if (param === 0x1F02) return "WebGL 1.0 (OpenGL ES 2.0 Chromium)";
            return realGetParameter.call(this, param);
        };
        // 也覆盖 WebGL2
        if (window.WebGL2RenderingContext) {
            var realGetParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function (param) {
                if (param === 0x9245) return fakeVendor;
                if (param === 0x9246) return fakeRenderer;
                if (param === 0x1F00) return fakeVendor;
                if (param === 0x1F01) return fakeRenderer;
                return realGetParameter2.call(this, param);
            };
        }
        // 覆盖 getSupportedExtensions,伪造常见扩展集(去掉 swiftshader 特有标记)
        var realGetExt = WebGLRenderingContext.prototype.getSupportedExtensions;
        WebGLRenderingContext.prototype.getSupportedExtensions = function () {
            var exts = realGetExt.call(this) || [];
            // 去掉 swiftshader 特征扩展,增加常见桌面扩展
            var filtered = exts.filter(function (e) {
                return e.indexOf("WEBGL_debug_renderer_info") === -1
                    && e.indexOf("EXT_texture_filter_anisotropic") !== -1;
            });
            return filtered;
        };
        // debug-renderer-info 扩展返回值伪造
        var realExt = WebGLRenderingContext.prototype.getExtension;
        WebGLRenderingContext.prototype.getExtension = function (name) {
            if (name === "WEBGL_debug_renderer_info") return null;
            return realExt.call(this, name);
        };
    } catch (e) {}

    // ===== 2. navigator 属性补充 =====
    // 容器内 hardwareConcurrency / deviceMemory / maxTouchPoints 等可能异常
    try {
        Object.defineProperty(navigator, "hardwareConcurrency", {
            get: function () { return 8; }, configurable: true,
        });
    } catch (e) {}
    try {
        Object.defineProperty(navigator, "deviceMemory", {
            get: function () { return 8; }, configurable: true,
        });
    } catch (e) {}
    try {
        Object.defineProperty(navigator, "maxTouchPoints", {
            get: function () { return 0; }, configurable: true,
        });
    } catch (e) {}
    try {
        Object.defineProperty(navigator, "platform", {
            get: function () { return "Win32"; }, configurable: true,
        });
    } catch (e) {}

    // ===== 3. permissions.query 扩展 =====
    // 补充 midi / camera / microphone 等权限的返回值
    try {
        var _origQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = function (params) {
            var name = params && params.name;
            if (name === "midi" || name === "camera" || name === "microphone"
                || name === "geolocation" || name === "clipboard-read"
                || name === "clipboard-write" || name === "payment-handler") {
                return Promise.resolve({ state: "prompt" });
            }
            if (name === "notifications") {
                return Promise.resolve({ state: Notification.permission });
            }
            return _origQuery(params);
        };
    } catch (e) {}

    // ===== 4. chrome 对象完善 =====
    // 真实 Chrome 有 chrome.loadTimes / chrome.csi 等,headless 可能缺失或异常
    try {
        if (!window.chrome) {
            window.chrome = {};
        }
        if (typeof window.chrome.loadTimes !== "function") {
            window.chrome.loadTimes = function () {
                var now = performance.now();
                return {
                    requestTime: now / 1000 - 0.5,
                    startLoadTime: now / 1000 - 0.4,
                    commitLoadTime: now / 1000 - 0.3,
                    finishDocumentLoadTime: now / 1000 - 0.1,
                    finishLoadTime: now / 1000,
                    firstPaintTime: now / 1000 + 0.1,
                    firstPaintAfterLoadTime: 0,
                    wasFetchedViaSpdy: false,
                    wasNpnNegotiated: false,
                    npnNegotiatedProtocol: "http/1.1",
                    connectionInfo: "http/1.1",
                    alternateProtocolAvailable: true,
                    wasAlternateProtocolAvailable: false,
                    isExternalRequest: false,
                };
            };
        }
        if (typeof window.chrome.csi !== "function") {
            window.chrome.csi = function () {
                var now = performance.now();
                return {
                    startEager: now - 500,
                    start: now - 400,
                    committed: now - 200,
                    load: now,
                    readyState: 4,
                    onloadTime: now,
                    tran: 1,
                };
            };
        }
        if (typeof window.chrome.app !== "object") {
            window.chrome.app = {
                isInstalled: false,
                getIsInstalled: function () { return false; },
                installState: function () { return "not_installed"; },
                getDetails: function () { return {}; },
            };
        }
    } catch (e) {}

    // ===== 5. Canvas 指纹轻微扰动 =====
    // 添加亚像素级别的随机偏移,避免与其他 headless 浏览器指纹完全一致。
    // 扰动极小(< 0.2px),肉眼不可见,但足以改变 hash。
    try {
        var _fillText = CanvasRenderingContext2D.prototype.fillText;
        var _strokeText = CanvasRenderingContext2D.prototype.strokeText;
        var seed = (Date.now() % 10000) / 10000;
        var jitterX = (seed - 0.5) * 0.3;  // ±0.15 px
        var jitterY = ((seed * 1.7) % 1 - 0.5) * 0.3;
        CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
            return _fillText.call(this, text, x + jitterX, y + jitterY, maxWidth);
        };
        CanvasRenderingContext2D.prototype.strokeText = function (text, x, y, maxWidth) {
            return _strokeText.call(this, text, x + jitterX, y + jitterY, maxWidth);
        };
    } catch (e) {}

    // ===== 6. AudioContext 指纹轻微扰动 =====
    // 虚拟声卡的音频指纹(如 getByteFrequencyData 噪声底)特征明显,
    // 添加轻微随机噪声,让每次指纹不完全一致。
    try {
        var _createAnalyser = AudioContext.prototype.createAnalyser;
        AudioContext.prototype.createAnalyser = function () {
            var analyser = _createAnalyser.call(this);
            var _getByteFrequencyData = analyser.getByteFrequencyData.bind(analyser);
            analyser.getByteFrequencyData = function (array) {
                _getByteFrequencyData(array);
                // 给每个 bin 添加 ±1 的随机扰动,不影响整体频谱形状
                for (var i = 0; i < array.length; i++) {
                    var noise = Math.floor(Math.random() * 3) - 1;
                    var v = array[i] + noise;
                    if (v < 0) v = 0;
                    if (v > 255) v = 255;
                    array[i] = v;
                }
            };
            return analyser;
        };
    } catch (e) {}

    // ===== 7. 字体数量伪造(简单版) =====
    // 真实桌面通常有几百种字体,容器里可能只有几十种。
    // 纯 JS 无法新增字体,但可以在一些常见字体探测 API 上做手脚。
    // 这里不做 aggressive 伪造(容易被反向检测),仅确保基础字体存在。
    // 实际增加字体靠 Dockerfile 里装字体包。

    // ===== 8. Object.getOwnPropertyDescriptor 防护 =====
    // 有些检测脚本会用 getOwnPropertyDescriptor 探测被覆盖的属性,
    // 确保 webdriver / plugins 等属性的 descriptor 看起来自然。
    try {
        var _origGetOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
        Object.getOwnPropertyDescriptor = function (obj, prop) {
            var desc = _origGetOwnPropertyDescriptor.call(this, obj, prop);
            // 如果是 navigator 的 webdriver 属性,返回看起来自然的 descriptor
            if (obj === Navigator.prototype && prop === "webdriver") {
                return {
                    value: false,
                    writable: true,
                    enumerable: true,
                    configurable: true,
                };
            }
            return desc;
        };
    } catch (e) {}
})();
"""


class BrowserPool:
    """全局唯一的浏览器实例管理器。

    生命周期:
    - ensure_started():懒启动 Playwright + Chromium + context(幂等)
    - get_client():返回绑定到当前 context 的 PerchanceClient
    - generate():串行执行生图(持锁),内部管理 client
    - close():应用关闭时清理资源
    """

    def __init__(self) -> None:
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._client: Optional[PerchanceClient] = None
        # 串行锁:perchance maxThreadsPerUser=1,同时只允许一个生图
        self._gen_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        # 持久化锁:防止并发写 storage_state 文件
        self._persist_lock = asyncio.Lock()
        # CF 验证状态追踪
        self._cf_status: str = "unknown"  # unknown / checking / verified / failed
        self._cf_last_checked_at: Optional[float] = None  # unix 时间戳
        self._cf_last_error: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        """context 是否真正可用(非空且未被关闭)。"""
        return self._context is not None and self._client is not None

    @property
    def cf_status(self) -> str:
        """Cloudflare 验证状态:unknown / checking / verified / failed。"""
        return self._cf_status

    @property
    def cf_last_error(self) -> Optional[str]:
        """上次 CF 验证失败的错误信息。"""
        return self._cf_last_error

    @property
    def cf_last_checked_at_iso(self) -> Optional[str]:
        """上次检查 CF 状态的 ISO 格式时间字符串。"""
        if self._cf_last_checked_at is None:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self._cf_last_checked_at, tz=timezone.utc).isoformat()

    @property
    def has_user_key(self) -> bool:
        """是否已有缓存的 userKey。"""
        return self._client is not None and self._client.has_user_key

    # ---------- 持久化(storage_state)----------

    def _storage_state_path(self) -> Optional[Path]:
        """返回 storage_state.json 路径,未配置 BROWSER_DATA_DIR 则返回 None。"""
        if not config.BROWSER_DATA_DIR:
            return None
        return Path(config.BROWSER_DATA_DIR) / "storage_state.json"

    def _load_storage_state(self) -> Optional[dict[str, Any]]:
        """从持久化文件加载 storage_state(同步,启动时调用)。"""
        path = self._storage_state_path()
        if not path or not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            n_cookies = len(state.get("cookies", []))
            n_origins = len(state.get("origins", []))
            logger.info("已加载持久化 storage_state(cookies=%d, origins=%d): %s",
                        n_cookies, n_origins, path)
            return state
        except Exception as e:
            logger.warning("加载 storage_state 失败,将忽略: %s", e)
            return None

    async def save_storage_state(self) -> None:
        """保存当前 context 的 storage_state 到持久化文件。

        外部(如 router)也可调用以主动保存。带锁防并发写。
        """
        path = self._storage_state_path()
        if not path or self._context is None:
            return
        async with self._persist_lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                state = await self._context.storage_state()
                # 原子写入:先写临时文件再 rename,避免崩溃导致文件损坏
                tmp_path = path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(state), encoding="utf-8")
                tmp_path.replace(path)
                n_cookies = len(state.get("cookies", []))
                logger.debug("已保存 storage_state(cookies=%d) 到 %s", n_cookies, path)
            except Exception as e:
                logger.warning("保存 storage_state 失败: %s", e)

    async def _context_alive(self) -> bool:
        """探测 context 是否仍然存活(浏览器未被关闭/崩溃)。

        用一个无害的 page 生命周期探活:能成功 new_page+close 即视为存活。
        """
        if self._context is None:
            return False
        try:
            pg = await self._context.new_page()
            await pg.close()
            return True
        except Exception:
            return False

    async def _reset(self) -> None:
        """重置内部状态,丢弃已失效的浏览器引用(不主动关闭,可能已断)。"""
        self._client = None
        self._context = None
        self._browser = None
        self._pw = None

    async def ensure_started(self) -> None:
        """确保浏览器可用(幂等,并发安全)。context 失效时自动重建。"""
        # 快路径:已有 context 且存活
        if self._context is not None and await self._context_alive():
            return
        async with self._start_lock:
            # 双检:拿到锁后再次确认(可能已被其他协程重建)
            if self._context is not None and await self._context_alive():
                return
            if self._context is not None:
                logger.warning("浏览器 context 已失效,重建中...")
                await self._reset()
            logger.info("启动 Patchright + Chromium(headless=%s)...", config.HEADLESS)
            self._pw = await async_playwright().start()
            # patchright 用打了补丁的 chromium,修补 TLS/JA3 等指纹以过 Cloudflare。
            # 有头模式(headless=False)实测能过 Cloudflare JS 挑战;headless 过不了。
            #
            # 启动参数(容器友好):
            # - --no-sandbox:容器内以 root 跑需要(非 root 可去)
            # - --disable-dev-shm-usage:避免 /dev/shm 太小崩溃(也由 compose shm_size 兜底)
            # - --use-gl=swiftshader:容器无 GPU 时让 WebGL 软件渲染,
            #   否则 WebGL 指纹异常会被 CF 识别(非完美解,但容器内最优)
            launch_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--use-gl=swiftshader",
                "--enable-webgl",
                "--ignore-gpu-blocklist",
            ]
            self._browser = await self._pw.chromium.launch(
                headless=config.HEADLESS,
                args=launch_args,
            )
            # 代理:从 HTTP_PROXY/HTTPS_PROXY 环境变量读取(住宅代理过 CF 关键)
            proxy = _resolve_proxy()
            context_kwargs: dict[str, Any] = dict(
                # 用一个常见桌面 UA,降低被 Cloudflare 标记概率
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )
            if proxy:
                context_kwargs["proxy"] = proxy
            # 持久化:加载 storage_state(cookies + localStorage),
            # 重启后 cf_clearance 仍在的话直接跳过验证
            storage_state = self._load_storage_state()
            if storage_state:
                context_kwargs["storage_state"] = storage_state
            self._context = await self._browser.new_context(**context_kwargs)
            # 反检测脚本:在每个页面脚本之前执行,等价于扩展的 document_start 注入。
            # 隐藏 webdriver 等自动化痕迹(patchright 已做深层指纹修补,这里作补充)。
            await self._context.add_init_script(_STEALTH_INIT_SCRIPT)
            # 增强指纹伪造:WebGL / Canvas / Audio / navigator 等,
            # 容器内 swiftshader 等指纹异常时开启,提高 Turnstile 无感通过率。
            if config.ENHANCED_FINGERPRINT:
                await self._context.add_init_script(_ENHANCED_FINGERPRINT_SCRIPT)
                logger.info("已注入增强指纹伪造脚本(WebGL/Canvas/Audio 等)。")
            self._client = PerchanceClient(self._context)
            logger.info("浏览器就绪(已注入反检测脚本)。")

    async def get_client(self) -> PerchanceClient:
        """获取绑定的 client(确保浏览器已启动且可用)。"""
        await self.ensure_started()
        assert self._client is not None
        return self._client

    async def warmup(self) -> None:
        """应用启动时预热浏览器(可选)。让有头窗口在启动后即出现,
        避免首次生图请求时才弹窗的延迟感。
        """
        try:
            await self.ensure_started()
        except Exception as e:
            logger.warning("预热浏览器失败(将退化为懒启动): %s", e)

    async def check_cf_status(self, force: bool = False) -> str:
        """主动检查 Cloudflare 验证状态(不触发 Turnstile 求解)。

        访问 verifyUser 看能否直接拿到 userKey,
        只检查是否已有有效 cf_clearance / IP 是否可信,
        遇到 token_required 或挑战页视为未验证(failed),不会尝试求解。

        Args:
            force:  True 表示跳过缓存,强制重新检查

        Returns:
            "verified" / "failed" / "unknown"
        """
        # 非强制时,5 分钟内的检查结果直接复用
        if (not force and self._cf_last_checked_at is not None
                and self._cf_status in ("verified", "failed")
                and self._cf_last_checked_at > 0):
            # 5 分钟内复用结果
            import time
            if time.time() - self._cf_last_checked_at < 300:
                return self._cf_status

        self._cf_status = "checking"
        await self.ensure_started()
        assert self._context is not None

        import time
        try:
            async with await self._context.new_page() as page:
                page.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
                # 构造 verifyUser URL(不带 token,走无感验证路径)
                url = (
                    f"{config.IMAGE_GEN_BASE_URL}/verifyUser"
                    f"?thread=0&__cacheBust={int(time.time() * 1000)}"
                )
                await page.goto(url, wait_until="domcontentloaded")
                content = await page.content()

                # 有 userKey → 已验证
                import re
                if re.search(r'"userKey"\s*:\s*"[a-f0-9]{64}"', content):
                    self._cf_status = "verified"
                    self._cf_last_error = None
                    # 同时刷新 client 里的 userKey 缓存
                    if self._client is not None:
                        m = re.search(r'"userKey"\s*:\s*"([a-f0-9]{64})"', content)
                        if m and not self._client.has_user_key:
                            # 注入到 client 实例(私有属性,这里直接设置)
                            self._client._user_key = m.group(1)
                elif ("token_required" in content or "failed_verification" in content
                      or "verification_required" in content):
                    self._cf_status = "failed"
                    self._cf_last_error = "需要 Turnstile 验证(token_required)"
                elif "just a moment" in content.lower() or "challenge-platform" in content.lower():
                    self._cf_status = "failed"
                    self._cf_last_error = "命中 Cloudflare 挑战页"
                elif "too_many_requests" in content:
                    self._cf_status = "failed"
                    self._cf_last_error = "请求被限流(too_many_requests)"
                else:
                    self._cf_status = "failed"
                    self._cf_last_error = f"未知响应状态"
        except Exception as e:
            self._cf_status = "failed"
            self._cf_last_error = f"检查异常: {e}"
            logger.warning("检查 CF 状态失败: %s", e)

        self._cf_last_checked_at = time.time()
        return self._cf_status

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        seed: int,
        resolution: str,
        guidance_scale: float,
        reference_image: Optional[str] = None,
    ):
        """串行执行生图全链路。

        浏览器实例单例复用:首次生图(或 context 失效重建后)才开窗口,
        后续生图复用同一浏览器,不反复弹窗。context 异常时自动重建并重试一次。

        返回 GenerationResult。调用方负责异常捕获与 HTTP 错误映射。
        """
        async with self._gen_lock:
            client = await self.get_client()
            try:
                result = await client.generate_and_download(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    seed=seed,
                    resolution=resolution,
                    guidance_scale=guidance_scale,
                    reference_image=reference_image,
                )
                # 生图成功 → 持久化 storage_state(cf_clearance 等 cookie 更新了)
                await self.save_storage_state()
                return result
            except Exception as e:
                # context 可能已失效,重建后重试一次
                logger.warning("生图异常(%s),重建浏览器后重试...", type(e).__name__)
                await self._reset()
                client = await self.get_client()
                result = await client.generate_and_download(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    seed=seed,
                    resolution=resolution,
                    guidance_scale=guidance_scale,
                    reference_image=reference_image,
                )
                # 重试成功也保存
                await self.save_storage_state()
                return result

    async def close(self) -> None:
        """清理浏览器资源。应用关闭时调用。"""
        logger.info("关闭浏览器...")
        # 关闭前先保存 storage_state
        if self._context is not None:
            await self.save_storage_state()
        # 按相反顺序关闭
        if self._context is not None:
            try:
                await self._context.close()
            except Exception as e:
                logger.warning("关闭 context 失败: %s", e)
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning("关闭 browser 失败: %s", e)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as e:
                logger.warning("停止 playwright 失败: %s", e)
        self._client = None
        self._context = None
        self._browser = None
        self._pw = None
        logger.info("浏览器已关闭。")


# 全局单例
browser_pool = BrowserPool()
