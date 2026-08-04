"""Cloudflare Turnstile 求解器封装。

目前支持 FlareSolverr(自托管,免费)。
设计为可扩展:后续可追加 Capsolver / YesCaptcha 等第三方服务,
只需在 solve_turnstile 里加优先级调用链即可。

所有方法都不抛异常:失败返回 None,由调用方决定回退策略。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app import config

logger = logging.getLogger("perchance.cf_solver")


class FlareSolverrClient:
    """FlareSolverr API 客户端。

    FlareSolverr 是一个自托管的 Cloudflare 求解服务,
    内部用 undetected-chromedriver 处理挑战。

    API 文档: https://github.com/FlareSolverr/FlareSolverr
    """

    def __init__(self, base_url: str = "", timeout: int = 60) -> None:
        self.base_url = (base_url or config.FLARESOLVERR_URL).rstrip("/")
        self.timeout = timeout or config.FLARESOLVERR_TIMEOUT
        self._session: Optional[httpx.AsyncClient] = None

    @property
    def enabled(self) -> bool:
        """是否配置了 FlareSolverr 服务。"""
        return bool(self.base_url)

    async def _get_session(self) -> httpx.AsyncClient:
        """懒创建 httpx 客户端。"""
        if self._session is None or self._session.is_closed:
            self._session = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._session

    async def test_connection(self) -> bool:
        """测试 FlareSolverr 服务是否可达(用于健康检查)。"""
        if not self.enabled:
            return False
        try:
            sess = await self._get_session()
            resp = await sess.get("/v1", params={"cmd": "health"})
            # 健康检查可能返回 200 或其他,只要能连通就算可用
            return resp.status_code < 500
        except Exception as e:
            logger.warning("FlareSolverr 连接测试失败: %s", e)
            return False

    async def solve_turnstile(self, sitekey: str, page_url: str) -> Optional[str]:
        """求解 Cloudflare Turnstile,返回 token 字符串,失败返回 None。

        Args:
            sitekey: Turnstile sitekey(perchance 固定值见 config.TURNSTILE_SITEKEY)
            page_url: 触发 Turnstile 的页面 URL

        Returns:
            Turnstile token 字符串,失败返回 None
        """
        if not self.enabled:
            logger.debug("FlareSolverr 未配置,跳过")
            return None

        t0 = time.time()
        try:
            sess = await self._get_session()
            payload = {
                "cmd": "turnstile.solve",
                "siteKey": sitekey,
                "url": page_url,
            }
            logger.info("调用 FlareSolverr 求解 Turnstile(sitekey=%s, url=%s)",
                        sitekey[:16], page_url[:80])
            resp = await sess.post("/v1", json=payload)

            if resp.status_code != 200:
                logger.warning("FlareSolverr HTTP %d: %s", resp.status_code, resp.text[:200])
                return None

            try:
                data = resp.json()
            except Exception:
                logger.warning("FlareSolverr 响应非 JSON: %s", resp.text[:200])
                return None

            status = data.get("status")
            if status != "ok":
                msg = data.get("message", "")
                logger.warning("FlareSolverr Turnstile 求解失败(status=%s): %s", status, msg[:200])
                return None

            solution = data.get("solution", {})
            token = str(solution.get("token", "")).strip()
            if not token or len(token) < config.TURNSTILE_TOKEN_MIN_LEN:
                logger.warning("FlareSolverr 返回 token 无效(len=%d)", len(token))
                return None

            elapsed = time.time() - t0
            logger.info("FlareSolverr Turnstile 求解成功(token len=%d, 耗时=%.1fs)", len(token), elapsed)
            return token

        except httpx.TimeoutException:
            logger.warning("FlareSolverr Turnstile 求解超时(>%ds)", self.timeout)
            return None
        except Exception as e:
            logger.warning("FlareSolverr Turnstile 求解异常: %s", e)
            return None

    async def request_get(self, url: str) -> Optional[dict]:
        """用 FlareSolverr 访问一个 URL,过 Cloudflare 后返回响应数据。

        用于处理"第 1 层"Cloudflare 挑战页(Just a moment):
        FlareSolverr 内部浏览器加载页面 → 过 CF 挑战 → 返回 cookies + 页面内容。
        我们拿到 cf_clearance cookie 后注入到 patchright 浏览器,即可继续访问。

        返回 dict 结构(FlareSolverr solution):
            {
                "url": "...",
                "status": 200,
                "cookies": [{"name": "cf_clearance", "value": "...", ...}, ...],
                "response": "<html>...</html>",
                "userAgent": "...",
                ...
            }

        失败返回 None。
        """
        if not self.enabled:
            return None

        t0 = time.time()
        try:
            sess = await self._get_session()
            payload = {
                "cmd": "request.get",
                "url": url,
                "maxTimeout": self.timeout * 1000,
            }
            logger.info("调用 FlareSolverr request.get 过 CF 挑战: %s", url[:100])
            resp = await sess.post("/v1", json=payload)

            if resp.status_code != 200:
                logger.warning("FlareSolverr request.get HTTP %d: %s", resp.status_code, resp.text[:200])
                return None

            try:
                data = resp.json()
            except Exception:
                logger.warning("FlareSolverr request.get 响应非 JSON: %s", resp.text[:200])
                return None

            status = data.get("status")
            if status != "ok":
                msg = data.get("message", "")
                logger.warning("FlareSolverr request.get 失败(status=%s): %s", status, msg[:200])
                return None

            solution = data.get("solution", {})
            cookies = solution.get("cookies", [])
            elapsed = time.time() - t0

            # 统计 cf_clearance 是否拿到
            cf_cookies = [c for c in cookies if c.get("name") == "cf_clearance"]
            if cf_cookies:
                logger.info("FlareSolverr 过 CF 成功!拿到 cf_clearance(耗时=%.1fs, cookies=%d)",
                            elapsed, len(cookies))
            else:
                logger.warning("FlareSolverr 响应成功但没找到 cf_clearance cookie"
                               "(可能页面本身没 CF 挑战,或挑战未通过)。cookies=%d, 耗时=%.1fs",
                               len(cookies), elapsed)

            return solution

        except httpx.TimeoutException:
            logger.warning("FlareSolverr request.get 超时(>%ds)", self.timeout)
            return None
        except Exception as e:
            logger.warning("FlareSolverr request.get 异常: %s", e)
            return None

    async def close(self) -> None:
        """关闭底层 httpx 客户端。"""
        if self._session and not self._session.is_closed:
            await self._session.aclose()


# 全局单例
flaresolverr = FlareSolverrClient()
