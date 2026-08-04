"""Perchance 生图浏览器代理核心。

复刻 perchance 嵌入页(image-generation.perchance.org/embed)的完整链路:
    1. verifyUser  -> 拿到 64 位 hex userKey(IP 可信时直接返回,否则需 Cloudflare Turnstile)
    2. generate    -> POST /api/generate,返回 JSON(含 imageId / imageDownloadUrl,非图片本身)
    3. download    -> fetch imageDownloadUrl,blob -> base64 -> 二进制 bytes

必须用真实浏览器:所有请求继承浏览器的 cf_clearance Cookie 与同源凭证,
纯 HTTP 客户端会被 Cloudflare 拦截。参考 backend/../perchance-生图接口分析.md。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from patchright.async_api import BrowserContext, Page

from app import config

logger = logging.getLogger("perchance.client")


# --- 自定义异常 ---


class PerchanceError(Exception):
    """所有 perchance 代理错误的基类。"""


class AuthenticationError(PerchanceError):
    """userKey 获取失败 / 过期 / 无效。"""


class RateLimitError(PerchanceError):
    """Cloudflare 或服务端限流(too_many_requests)。"""


class GenerationError(PerchanceError):
    """生图本身失败(gen_failure / invalid_ad_access_code / stale_request 等)。"""


class DownloadError(PerchanceError):
    """下载生成的图片失败。"""


class _WaitingError(PerchanceError):
    """generate 返回 waiting_for_prev_request_to_finish,需轮询等待。

    内部信号异常,不向外抛出。
    """

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__("waiting_for_prev_request_to_finish")
        self.data = data


@dataclass
class GenerationResult:
    """单次生图结果(原始 JSON + 下载 bytes)。"""

    image_bytes: bytes
    file_extension: str = "jpeg"
    seed: int = -1
    width: int = 512
    height: int = 512
    guidance_scale: float = 7.0
    prompt: str = ""
    negative_prompt: str = ""
    maybe_nsfw: bool = False


# --- userKey 提取 ---

# 响应文本里形如 "userKey":"<64位hex>" 的片段
_USER_KEY_RE = re.compile(r'"userKey"\s*:\s*"([a-f0-9]{64})"')


def _cache_bust() -> str:
    """随机 cacheBust 参数,避免命中缓存。"""
    return str(random.randint(10_000_000, 99_999_999))


def _request_id() -> str:
    """生成 requestId(perchance 用 "aiImageCompletion" + 随机数)。"""
    return f"aiImageCompletion{random.randint(10_000_000, 99_999_999)}"


def _strip_html(content: str) -> str:
    """把 HTML 标签去掉,便于在日志里打印 verifyUser 响应片段。

    verifyUser 返回的可能是纯 JSON(包在 <pre> 里),也可能是挑战页 HTML。
    """
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class PerchanceClient:
    """在给定 BrowserContext 内执行 perchance 生图全链路。

    设计要点:
    - 每次操作新建 page,用完即关;context 跨调用复用(由 browser_pool 管理)。
    - userKey 缓存在实例上,失效时自动重验证。
    - 所有 HTTP 通过 page.evaluate 执行 JS fetch,继承浏览器 Cookie / 同源凭证。
    - 生成串行(perchance maxThreadsPerUser=1),由 browser_pool 的 Lock 保证。
    """

    BASE_URL = config.IMAGE_GEN_BASE_URL

    def __init__(self, context: BrowserContext) -> None:
        self._context = context
        # 缓存的 userKey(64-hex)。为空表示需重新验证。
        self._user_key: Optional[str] = None

    @property
    def has_user_key(self) -> bool:
        """是否已有缓存的 userKey(说明曾验证通过)。"""
        return bool(self._user_key)

    @property
    def user_key(self) -> Optional[str]:
        """当前缓存的 userKey(只读,外部用于诊断)。"""
        return self._user_key

    # ---------- 1. verifyUser ----------

    async def _ensure_user_key(self, page: Page) -> str:
        """获取有效的 userKey,带缓存。失效时调用方应清空缓存后重试。"""
        if self._user_key:
            return self._user_key
        self._user_key = await self._verify_user(page)
        return self._user_key

    async def _verify_user(self, page: Page) -> str:
        """获取 userKey。IP 可信时直接返回 already_verified;
        否则 perchance 返回 token_required,需先解 Cloudflare Turnstile
        拿到 token,再带 token 重新请求 verifyUser。

        旧逻辑遇到 token_required 直接抛异常放弃;现改为主动尝试解 Turnstile。
        """
        token = ""  # 首次无 token 走无感验证路径
        for attempt in range(2):  # 最多两轮:无 token / 带 token
            url = self._verify_url(token=token)
            logger.info("verifyUser 请求(attempt=%d, token=%s): %s",
                        attempt, "有" if token else "无", url[:120])
            await page.goto(url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
            content = await page.content()
            snippet = _strip_html(content)

            # 限流
            if "too_many_requests" in content:
                logger.warning("verifyUser 命中 too_many_requests")
                raise RateLimitError("perchance verifyUser 返回 too_many_requests,已被限流")

            # Cloudflare 挑战页(cf_clearance 失效)
            low = content.lower()
            if "just a moment" in low or "challenge-platform" in low:
                logger.warning("verifyUser 命中 Cloudflare 挑战页,cf_clearance 可能已失效")
                raise RateLimitError("verifyUser 命中 Cloudflare 挑战页,cf_clearance 可能已失效")

            # 已有 userKey,直接返回(IP 可信路径)
            m = _USER_KEY_RE.search(content)
            if m:
                logger.info("verifyUser 成功获得 userKey(IP 可信/已验证)")
                return m.group(1)

            # 需要 Turnstile token
            needs_token = ("token_required" in content
                           or "failed_verification" in content
                           or "verification_required" in content)
            if needs_token and not token:
                logger.info("verifyUser 要求 Turnstile token(%s),开始主动求解...", snippet[:160])
                if not config.TURNSTILE_AUTO_SOLVE:
                    raise AuthenticationError(
                        "perchance 验证需要 Turnstile token(当前 IP 被标记),"
                        "且 TURNSTILE_AUTO_SOLVE=False 已关闭自动求解。"
                    )
                token = await self._solve_turnstile_token(page)
                if not token:
                    raise AuthenticationError(
                        f"未能获取 Turnstile token(自动求解失败)。响应片段: {snippet[:200]!r}"
                    )
                logger.info("已获取 Turnstile token(len=%d),带 token 重新请求 verifyUser", len(token))
                continue  # 带 token 进入下一轮

            # 既没 userKey 也没要求 token,或带 token 仍未拿到 userKey
            raise AuthenticationError(
                f"verifyUser 未返回 userKey。响应片段: {snippet[:300]!r}"
            )

        raise AuthenticationError("verifyUser 两轮后仍未拿到 userKey")

    async def _solve_turnstile_token(self, page: Page) -> str:
        """求解 Cloudflare Turnstile,返回 token。

        策略链(按优先级):
          1. FlareSolverr(如果配置了) — 外部服务求解,成功率高
          2. 浏览器内轮询 — 等 widget 自行通过(managed 模式)

        都失败时返回空字符串。
        """
        # 从页面提取 sitekey(perchance 有固定值,但动态提取更稳妥)
        sitekey = await self._extract_sitekey(page) or config.TURNSTILE_SITEKEY

        # ===== 第 1 步:FlareSolverr 外部求解(优先)=====
        from app.core.cf_solver import flaresolverr  # 延迟导入避免循环
        if flaresolverr.enabled:
            token = await flaresolverr.solve_turnstile(sitekey, config.EMBED_URL)
            if token and len(token) >= config.TURNSTILE_TOKEN_MIN_LEN:
                logger.info("FlareSolverr 求解成功,直接使用 token(len=%d)", len(token))
                return token
            logger.info("FlareSolverr 求解失败,回退到浏览器内轮询方案")

        # ===== 第 2 步:回退到浏览器内轮询 =====
        logger.info("导航到 embed 页触发 Turnstile: %s", config.EMBED_URL)
        try:
            await page.goto(config.EMBED_URL, wait_until="domcontentloaded",
                            timeout=config.NAV_TIMEOUT_MS)
        except Exception as e:
            logger.warning("导航 embed 页失败: %s", e)
            return ""

        # 预热:让 Turnstile iframe 初始化(对齐 getTurnstileToken 的 human_sleep(2))
        await asyncio.sleep(2)

        for i in range(config.TURNSTILE_POLL_MAX_ATTEMPTS):
            try:
                token = await page.evaluate(
                    """
                    () => {
                        try {
                            const el = document.querySelector('input[name="cf-turnstile-response"]');
                            const byInput = String((el && el.value) || '').trim();
                            if (byInput) return byInput;
                            if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                                return String(window.turnstile.getResponse() || '').trim();
                            }
                            return '';
                        } catch (e) { return ''; }
                    }
                    """
                )
                token = str(token or "").strip()
                if len(token) >= config.TURNSTILE_TOKEN_MIN_LEN:
                    logger.info("Turnstile token 已获取(第 %d 轮,len=%d)", i + 1, len(token))
                    return token
            except Exception as e:
                logger.debug("读 token 第 %d 轮异常: %s", i + 1, e)

            # 兜底:尝试触发页面上 turnstile 容器,促使挑战开始
            if i == 0:
                await self._nudge_turnstile_widget(page)
            await asyncio.sleep(config.TURNSTILE_POLL_INTERVAL_S)

        logger.warning("Turnstile 轮询 %d 次仍未拿到 token", config.TURNSTILE_POLL_MAX_ATTEMPTS)
        return ""

    async def _nudge_turnstile_widget(self, page: Page) -> None:
        """尝试触发 Turnstile widget 开始挑战(轻点容器 / reset)。

        Turnstile iframe 跨域(challenges.cloudflare.com),父页面 JS 无法直接
        操作其内部复选框;这里只做尽力而为的容器点击 + reset,
        managed 模式下多数情况 widget 会自行判定,无需交互。
        """
        try:
            await page.evaluate(
                """
                () => {
                    try { if (window.turnstile && typeof window.turnstile.reset === 'function') window.turnstile.reset(); } catch(e) {}
                    try {
                        const nodes = Array.from(document.querySelectorAll('div,span,iframe'));
                        const hit = nodes.find(n => {
                            const t = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute && n.getAttribute('src') || '');
                            return String(t).toLowerCase().includes('turnstile');
                        });
                        if (hit && typeof hit.click === 'function') hit.click();
                    } catch(e) {}
                }
                """
            )
        except Exception as e:
            logger.debug("nudge turnstile 异常: %s", e)

    async def _extract_sitekey(self, page: Page) -> str:
        """从当前页面提取 Turnstile sitekey。

        优先从 cf-turnstile 元素的 data-sitekey 属性读,
        其次从 iframe src 的 sitekey 参数解析。
        提取失败返回空字符串。
        """
        try:
            return await page.evaluate(
                """
                () => {
                    try {
                        const el = document.querySelector('[data-sitekey]');
                        if (el && el.getAttribute('data-sitekey')) {
                            return el.getAttribute('data-sitekey').trim();
                        }
                    } catch(e) {}
                    try {
                        const iframes = document.querySelectorAll('iframe[src*="turnstile"]');
                        for (const f of iframes) {
                            const src = f.getAttribute('src') || '';
                            const m = src.match(/[?&]sitekey=([^&]+)/);
                            if (m && m[1]) return decodeURIComponent(m[1]).trim();
                        }
                    } catch(e) {}
                    return '';
                }
                """
            )
        except Exception:
            return ""

    def _verify_url(self, token: str = "") -> str:
        """构造 verifyUser URL。带 token 时走 token 验证路径。"""
        base = f"{self.BASE_URL}/verifyUser?thread=0&__cacheBust={_cache_bust()}"
        if token:
            return f"{self.BASE_URL}/verifyUser?token={token}&thread=0&__cacheBust={_cache_bust()}"
        return base

    def invalidate_user_key(self) -> None:
        """标记 userKey 失效,下次操作会重新验证。"""
        self._user_key = None

    async def _ensure_on_perchance_domain(self, page: Page) -> None:
        """确保 page 处于 image-generation.perchance.org 域。

        page.evaluate 里的 fetch 是同源请求,若 page 还在 about:blank
        (例如 userKey 命中缓存、_verify_user 未导航)则 fetch 会
        'Failed to fetch'。导航到 verifyUser 建立 perchance 同源上下文。
        """
        current = page.url
        if current.startswith("https://image-generation.perchance.org"):
            return
        url = self._verify_url()
        await page.goto(url, wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS)

    # ---------- 2. generate ----------

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        seed: int,
        resolution: str,
        guidance_scale: float,
        reference_image: Optional[str] = None,
    ) -> dict[str, Any]:
        """调用 /api/generate,返回原始 JSON 响应。

        prompt:            已拼接风格前缀的完整提示词
        negative_prompt:   已拼接风格负面前缀的完整负面提示词
        reference_image:   图生图参考图(base64 data URL 或 http URL);None 则文生图
        """
        async with await self._context.new_page() as page:
            page.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)

            user_key = await self._ensure_user_key(page)
            # 确保 page 处于 perchance 域(同源),fetch 才不会 Failed to fetch。
            # _ensure_user_key 命中缓存时不会导航,page 仍是 about:blank,需补导航。
            await self._ensure_on_perchance_domain(page)
            try:
                return await self._do_generate(
                    page, user_key, prompt, negative_prompt,
                    seed, resolution, guidance_scale, reference_image,
                )
            except GenerationError as e:
                # invalid_key -> userKey 过期,清空重验后重试一次
                if "invalid_key" in str(e):
                    self.invalidate_user_key()
                    user_key = await self._ensure_user_key(page)
                    return await self._do_generate(
                        page, user_key, prompt, negative_prompt,
                        seed, resolution, guidance_scale, reference_image,
                    )
                raise

    async def _do_generate(
        self, page: Page, user_key: str,
        prompt: str, negative_prompt: str, seed: int,
        resolution: str, guidance_scale: float,
        reference_image: Optional[str],
    ) -> dict[str, Any]:
        """实际发起 generate 请求并处理状态。"""
        request_id = _request_id()
        url = (
            f"{self.BASE_URL}/generate"
            f"?userKey={user_key}&requestId={request_id}&__cacheBust={_cache_bust()}"
        )

        body: dict[str, Any] = {
            "prompt": prompt,
            "negativePrompt": negative_prompt,
            "seed": seed,
            "resolution": resolution,
            "guidanceScale": guidance_scale,
            "channel": config.DEFAULT_CHANNEL,
            "subChannel": config.DEFAULT_SUB_CHANNEL,
            "userKey": user_key,
            "adAccessCode": "",
            "requestId": request_id,
        }
        if reference_image:
            body["referenceImage"] = {"url": reference_image, "blur": 0}

        # 在浏览器上下文内执行 fetch,继承 cf_clearance 与同源凭证
        resp = await page.evaluate(
            """
            async ([url, body]) => {
                const res = await fetch(url, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                    credentials: "include",
                });
                let text = "";
                try { text = await res.text(); } catch(e) {}
                return { status: res.status, text };
            }
            """,
            [url, body],
        )

        return self._interpret_generate_response(resp, request_id)

    def _interpret_generate_response(
        self, resp: dict[str, Any], request_id: str
    ) -> dict[str, Any]:
        """解析 generate 响应文本,处理各种 status。"""
        status_code = resp.get("status", 0)
        text = resp.get("text", "") or ""

        # HTTP 层限流
        if status_code == 429 or "too_many_requests" in text:
            raise RateLimitError("generate 被限流(too_many_requests / HTTP 429)")
        if status_code == 403 or "just a moment" in text.lower():
            raise RateLimitError("generate 命中 Cloudflare 挑战页")

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            raise GenerationError(f"generate 响应非 JSON: status={status_code}, 片段={text[:300]!r}")

        status = data.get("status", "")

        if status == "success":
            return data

        # 各类错误状态
        if status == "invalid_key":
            raise GenerationError("invalid_key: userKey 无效或过期")
        if status == "invalid_ad_access_code":
            raise GenerationError("invalid_ad_access_code")
        if status == "stale_request":
            raise GenerationError("stale_request: requestId 已过期")
        if status == "gen_failure":
            raise GenerationError(f"gen_failure: 服务端生成失败(type={data.get('type')})")
        if status == "fetch_failure":
            raise GenerationError("fetch_failure: 服务端网络错误")
        if status == "waiting_for_prev_request_to_finish":
            # 调用方应轮询等待,这里转成可识别异常由上层处理
            raise _WaitingError(data)

        # 未知状态
        raise GenerationError(f"未知 generate 状态: {status!r}, 完整: {text[:400]!r}")

    # ---------- 3. download ----------

    async def download_image(self, data: dict[str, Any]) -> GenerationResult:
        """下载 generate 返回的图片,组装 GenerationResult。

        优先用 imageDownloadUrl,其次用 imageId 拼 downloadTemporaryImage。
        """
        async with await self._context.new_page() as page:
            page.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
            # download 也需同源:先导航到 perchance 域
            await self._ensure_on_perchance_domain(page)

            download_url = self._resolve_download_url(data)
            if not download_url:
                raise DownloadError(f"响应中未找到可下载的图片地址: {data!r}")

            image_bytes, file_extension = await self._fetch_binary(page, download_url)

        return GenerationResult(
            image_bytes=image_bytes,
            file_extension=data.get("fileExtension", file_extension),
            seed=data.get("seed", -1),
            width=data.get("width", 512),
            height=data.get("height", 512),
            guidance_scale=data.get("guidanceScale", 7.0),
            prompt=data.get("prompt", ""),
            negative_prompt=data.get("negativePrompt", ""),
            maybe_nsfw=bool(data.get("maybeNsfw", False)),
        )

    @staticmethod
    def _resolve_download_url(data: dict[str, Any]) -> Optional[str]:
        """从 generate 响应里解析出下载 URL。"""
        # 优先:响应直接给的代理下载路径
        download_url = data.get("imageDownloadUrl")
        if download_url:
            if download_url.startswith("/"):
                # 相对路径,补全到 image-generation.perchance.org
                return "https://image-generation.perchance.org" + download_url
            if download_url.startswith("http"):
                return download_url

        # 其次:用 imageId 拼 downloadTemporaryImage
        image_id = data.get("imageId")
        if image_id:
            return f"https://image-generation.perchance.org/api/downloadTemporaryImage?imageId={image_id}"

        return None

    async def _fetch_binary(self, page: Page, url: str) -> tuple[bytes, str]:
        """在浏览器上下文内 fetch 二进制图片,blob -> base64 -> bytes。"""
        result = await page.evaluate(
            """
            async (url) => {
                const res = await fetch(url, { credentials: "include" });
                if (!res.ok) return { error: "HTTP " + res.status };
                const blob = await res.blob();
                // 从 Content-Type 推断扩展名
                const ct = res.headers.get("content-type") || "";
                const reader = new FileReader();
                const dataUrl = await new Promise((resolve, reject) => {
                    reader.onloadend = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
                return { dataUrl, contentType: ct };
            }
            """,
            url,
        )

        if not result or result.get("error"):
            raise DownloadError(f"下载图片失败: {result.get('error') if result else '无响应'}")

        data_url: str = result.get("dataUrl", "")
        if "," not in data_url:
            raise DownloadError("下载图片返回的 dataUrl 格式异常")

        header, b64 = data_url.split(",", 1)
        try:
            import base64

            image_bytes = base64.b64decode(b64)
        except Exception as e:
            raise DownloadError(f"base64 解码失败: {e}") from e

        # 推断扩展名
        ct = result.get("contentType", "").lower()
        if "png" in ct:
            ext = "png"
        elif "webp" in ct:
            ext = "webp"
        else:
            ext = "jpeg"

        return image_bytes, ext

    # ---------- 4. 完整链路 ----------

    async def generate_and_download(
        self,
        prompt: str,
        negative_prompt: str,
        seed: int,
        resolution: str,
        guidance_scale: float,
        reference_image: Optional[str] = None,
    ) -> GenerationResult:
        """完整生图链路:generate -> (必要时轮询) -> download。

        含对 waiting_for_prev_request_to_finish 的轮询,以及 gen_failure 的有限重试。
        """
        last_err: Optional[Exception] = None

        for attempt in range(config.MAX_RETRIES + 1):
            try:
                data = await self.generate(
                    prompt, negative_prompt, seed, resolution,
                    guidance_scale, reference_image,
                )
                return await self.download_image(data)
            except _WaitingError:
                # 上一请求未完成,轮询 awaitExistingGenerationRequest
                last_err = await self._await_existing(prompt, negative_prompt, seed, resolution,
                                                       guidance_scale, reference_image)
                if isinstance(last_err, GenerationResult):
                    return last_err
                # 轮询后重试 generate
                continue
            except GenerationError as e:
                last_err = e
                if "gen_failure" in str(e) and attempt < config.MAX_RETRIES:
                    await asyncio.sleep(2)
                    continue
                raise
            except RateLimitError:
                raise
            except Exception as e:
                last_err = e
                if attempt < config.MAX_RETRIES:
                    await asyncio.sleep(2)
                    continue
                raise

        # 重试耗尽
        raise GenerationError(f"生图重试耗尽: {last_err}") from last_err

    async def _await_existing(
        self, prompt: str, negative_prompt: str, seed: int, resolution: str,
        guidance_scale: float, reference_image: Optional[str],
    ) -> Any:
        """轮询 awaitExistingGenerationRequest,完成后重试 generate。"""
        for _ in range(config.POLL_MAX_ATTEMPTS):
            await asyncio.sleep(config.POLL_INTERVAL_S)
            try:
                async with await self._context.new_page() as page:
                    page.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
                    user_key = await self._ensure_user_key(page)
                    await self._ensure_on_perchance_domain(page)
                    url = (
                        f"{self.BASE_URL}/awaitExistingGenerationRequest"
                        f"?userKey={user_key}&__cacheBust={_cache_bust()}"
                    )
                    resp = await page.evaluate(
                        """
                        async (url) => {
                            const res = await fetch(url, { credentials: "include" });
                            let text = ""; try { text = await res.text(); } catch(e) {}
                            return { status: res.status, text };
                        }
                        """,
                        url,
                    )
                text = resp.get("text", "")
                if "success" in text:
                    # 上一请求完成,重试 generate
                    return None
            except Exception:
                continue
        raise GenerationError("等待上一请求完成超时")
