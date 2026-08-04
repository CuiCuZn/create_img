"""Cloudflare Turnstile 求解器封装。

目前支持 FlareSolverr(自托管,免费)。
设计为可扩展:后续可追加 Capsolver / YesCaptcha 等第三方服务,
只需在 solve_turnstile 里加优先级调用链即可。

所有方法都不抛异常:失败返回 None,由调用方决定回退策略。
"""
from __future__ import annotations

import asyncio
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
        """懒创建 httpx 客户端。

        注意:必须设置 trust_env=False,否则 httpx 会读取 HTTP_PROXY 环境变量,
        导致连 flaresolverr:8191(内网地址)也走代理出去,连接失败。
        """
        if self._session is None or self._session.is_closed:
            self._session = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                trust_env=False,  # 关键:不读取系统代理环境变量
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

        FlareSolverr v3.x 没有独立的 turnstile.solve 命令。
        实现思路:
          1. 创建 FlareSolverr session(持久化浏览器上下文)
          2. 先访问 embed 页,让 Turnstile widget 加载并开始验证
          3. 在同一个 session 内轮询:每次请求同页 + 注入"读取 token 并写到 title"的脚本
          4. 从返回的 document.title 中提取 token
          5. 销毁 session

        为什么不直接用脚本等待?因为 FlareSolverr 的 request.get 在页面 load 后就返回了,
        不会等 setTimeout 的异步逻辑。所以用 session 内多次轮询的方式。

        Args:
            sitekey: Turnstile sitekey(页面上会自动使用,这里不需要传)
            page_url: 触发 Turnstile 的页面 URL(通常是 embed 页)

        Returns:
            Turnstile token 字符串,失败返回 None
        """
        if not self.enabled:
            logger.debug("FlareSolverr 未配置,跳过")
            return None

        t0 = time.time()
        session_id = f"turnstile_{int(time.time() * 1000)}"
        sess = await self._get_session()
        max_polls = 30  # 最多轮询 30 次,每次间隔约 2 秒,总共约 60 秒
        poll_interval = 2.0  # 秒

        # 每次轮询时注入的脚本:立即读取当前 Turnstile token 并写到 document.title
        # 这样 request.get 返回后,我们从 solution.title 就能读到 token
        read_token_script = r"""
() => {
  // 页面加载完成后立即读一次 token 并写到 title
  function readToken() {
    let token = '';
    try {
      const el = document.querySelector('input[name="cf-turnstile-response"]');
      if (el && el.value) token = el.value.trim();
      if (!token && window.turnstile && typeof window.turnstile.getResponse === 'function') {
        token = (window.turnstile.getResponse() || '').trim();
      }
    } catch(e) {}
    if (token && token.length > 50) {
      document.title = 'TS_TOKEN_' + token;
    } else {
      // 没有 token 时也标记一下,方便判断脚本是否执行了
      if (!document.title.startsWith('TS_')) {
        document.title = 'TS_CHECKING_' + Date.now();
      }
      // 尝试主动触发 widget
      try {
        if (window.turnstile && typeof window.turnstile.reset === 'function') {
          window.turnstile.reset();
        }
        const w = document.querySelector('.cf-turnstile') || document.querySelector('[data-sitekey]');
        if (w && w.click) w.click();
      } catch(e) {}
    }
  }
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    readToken();
  } else {
    window.addEventListener('DOMContentLoaded', readToken);
    window.addEventListener('load', readToken);
  }
}
""".strip()

        try:
            # 步骤 1:创建 session
            logger.debug("创建 FlareSolverr session: %s", session_id)
            resp = await sess.post("/v1", json={
                "cmd": "sessions.create",
                "session": session_id,
            })
            if resp.status_code != 200:
                logger.warning("FlareSolverr 创建 session 失败: HTTP %d", resp.status_code)
                return None

            # 步骤 2:轮询访问 embed 页,每次检查 token
            import re
            for i in range(max_polls):
                if i > 0:
                    await asyncio.sleep(poll_interval)

                logger.debug("FlareSolverr Turnstile 轮询第 %d/%d 次", i + 1, max_polls)
                resp = await sess.post("/v1", json={
                    "cmd": "request.get",
                    "url": page_url,
                    "session": session_id,
                    "maxTimeout": 15000,  # 单次 15s 超时
                    "script": read_token_script,
                })

                if resp.status_code != 200:
                    logger.debug("FlareSolverr 轮询请求 HTTP %d,继续", resp.status_code)
                    continue

                try:
                    data = resp.json()
                except Exception:
                    continue

                if data.get("status") != "ok":
                    continue

                solution = data.get("solution", {})
                title = solution.get("title", "") or ""

                # 成功:拿到 token
                m = re.match(r"^TS_TOKEN_(.+)$", title)
                if m:
                    token = m.group(1).strip()
                    if len(token) >= config.TURNSTILE_TOKEN_MIN_LEN:
                        elapsed = time.time() - t0
                        logger.info(
                            "FlareSolverr Turnstile 求解成功(token len=%d, 耗时=%.1fs, 轮询=%d次)",
                            len(token), elapsed, i + 1,
                        )
                        return token

                # title 还在 TS_CHECKING_ 说明脚本执行了但还没出 token,继续等
                # 空 title 可能是页面还没加载完,也继续等

            # 轮询耗尽
            elapsed = time.time() - t0
            logger.warning("FlareSolverr Turnstile 轮询 %d 次仍未拿到 token(耗时=%.1fs)",
                           max_polls, elapsed)
            return None

        except httpx.TimeoutException:
            logger.warning("FlareSolverr Turnstile 求解超时")
            return None
        except Exception as e:
            logger.warning("FlareSolverr Turnstile 求解异常: %s", e)
            return None
        finally:
            # 步骤 3:销毁 session
            try:
                await sess.post("/v1", json={
                    "cmd": "sessions.destroy",
                    "session": session_id,
                })
            except Exception:
                pass

    async def request_get(self, url: str, return_solution: bool = False) -> Optional[dict]:
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
            logger.info("调用 FlareSolverr request.get(url=%s)", url[:100])
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

            http_status = solution.get("status", 0)
            # 统计 cf_clearance 是否拿到
            cf_cookies = [c for c in cookies if c.get("name") == "cf_clearance"]
            if cf_cookies:
                logger.info("FlareSolverr 过 CF 成功!拿到 cf_clearance(耗时=%.1fs, cookies=%d, http=%d)",
                            elapsed, len(cookies), http_status)
            else:
                logger.info("FlareSolverr 请求完成(耗时=%.1fs, cookies=%d, http=%d)"
                            " — 未检测到 CF 挑战(IP 可信或 FlareSolverr 直接通过)",
                            elapsed, len(cookies), http_status)

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
