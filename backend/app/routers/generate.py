"""生图相关路由:/api/generate、/api/styles、/api/health。"""
from __future__ import annotations

import base64
import json
import logging

from fastapi import APIRouter, HTTPException

from app import config
from app.core.browser_pool import browser_pool
from app.core.perchance_client import (
    AuthenticationError,
    GenerationError,
    PerchanceError,
    RateLimitError,
)
from app.models.schemas import ArtStyle, GenerateRequest, GenerateResponse, HealthResponse, ImageItem

logger = logging.getLogger("perchance.router")

router = APIRouter(prefix="/api", tags=["generate"])

# 风格缓存:启动时从 JSON 加载,避免每次请求读文件
_styles_cache: list[ArtStyle] | None = None
_styles_map: dict[str, ArtStyle] | None = None


def _load_styles() -> tuple[list[ArtStyle], dict[str, ArtStyle]]:
    """加载风格预设(带缓存)。"""
    global _styles_cache, _styles_map
    if _styles_cache is not None and _styles_map is not None:
        return _styles_cache, _styles_map

    try:
        raw = json.loads(config.ART_STYLES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("加载风格预设失败: %s", e)
        raw = [{"name": "None", "positive_prefix": "", "negative_prefix": ""}]

    _styles_cache = [ArtStyle(**s) for s in raw]
    _styles_map = {s.name: s for s in _styles_cache}
    return _styles_cache, _styles_map


def _build_prompt(req: GenerateRequest) -> tuple[str, str, str]:
    """拼接风格前缀,返回 (完整 prompt, 完整 negative_prompt, resolution)。"""
    _, styles_map = _load_styles()

    style = styles_map.get(req.style) or styles_map.get("None")
    if style is None:
        style = ArtStyle(name="None", positive_prefix="", negative_prefix="")

    # 正向:风格前缀 + 用户 prompt。风格前缀含 [input.description] 占位,
    # 在原站会被替换;这里我们简单拼接(前缀在前,用户描述在后)。
    if style.positive_prefix:
        full_prompt = f"{style.positive_prefix}, {req.prompt}"
    else:
        full_prompt = req.prompt

    # 负向:风格负面前缀 + 用户负面
    parts = [p for p in (style.negative_prefix, req.negative_prompt) if p]
    full_negative = ", ".join(parts)

    resolution = config.SHAPE_RESOLUTION[req.shape]
    return full_prompt, full_negative, resolution


@router.get("/styles", response_model=list[ArtStyle])
async def get_styles() -> list[ArtStyle]:
    """返回所有风格预设,供前端下拉渲染。"""
    styles, _ = _load_styles()
    return styles


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """健康检查。返回服务状态 + Cloudflare 验证状态 + 求解器信息。

    注意:浏览器未启动时 cf_status 为 unknown;浏览器启动后才会检查 CF 状态。
    要主动检查,调用 GET /api/health?check_cf=true。
    """
    # 求解器类型判断
    solver_type = "none"
    solver_available = False
    if config.FLARESOLVERR_URL:
        solver_type = "flaresolverr"
        # 不主动测连接(健康检查接口要快),交由 check_cf 或实际请求时验证
        solver_available = True  # 配置了就算"可用配置"
    elif config.TURNSTILE_AUTO_SOLVE:
        solver_type = "browser"
        solver_available = browser_pool.is_ready

    cf_status = browser_pool.cf_status if browser_pool.is_ready else "unknown"

    return HealthResponse(
        status="ok",
        browser_ready=browser_pool.is_ready,
        cf_status=cf_status,
        cf_last_checked_at=browser_pool.cf_last_checked_at_iso,
        cf_last_error=browser_pool.cf_last_error,
        user_key_cached=browser_pool.has_user_key,
        solver_type=solver_type,
        solver_available=solver_available,
    )


@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """生图(文生图 / 图生图统一入口)。

    支持 count 参数批量生成(1-4 张),后端串行调用 perchance。
    返回 base64 图片列表 + 元数据。生图耗时约每张 5-15 秒。
    """
    full_prompt, full_negative, resolution = _build_prompt(req)

    # 参考图:前端传 base64 data URL 或 http URL,直接透传给 perchance
    reference_image = req.reference_image or None

    logger.info(
        "生图请求: style=%s shape=%s seed=%s count=%s ref=%s prompt=%s",
        req.style, req.shape, req.seed, req.count, bool(reference_image), req.prompt[:60],
    )

    images: list[ImageItem] = []
    # 批量生成:count 张,串行调用(perchance maxThreadsPerUser=1)。
    # seed=-1 时每张随机;指定 seed 时每张用不同随机种子(避免完全相同)。
    for i in range(req.count):
        # 第一张用原始 seed;后续张:若 seed=-1 保持随机,否则随机化
        cur_seed = req.seed if (i == 0 or req.seed != -1) else -1
        # 指定 seed 时,后续张递增 seed 以产生差异
        if req.seed != -1 and i > 0:
            cur_seed = req.seed + i

        try:
            result = await browser_pool.generate(
                prompt=full_prompt,
                negative_prompt=full_negative,
                seed=cur_seed,
                resolution=resolution,
                guidance_scale=req.guidance_scale,
                reference_image=reference_image,
            )
        except RateLimitError as e:
            logger.warning("生图被限流: %s", e)
            raise HTTPException(
                status_code=429,
                detail="生成请求过于频繁,已被 perchance 限流。请稍后再试。",
            ) from e
        except AuthenticationError as e:
            logger.error("认证失败: %s", e)
            raise HTTPException(
                status_code=502,
                detail="无法通过 perchance 验证(Cloudflare 挑战)。请稍后重试。",
            ) from e
        except GenerationError as e:
            logger.error("生图失败: %s", e)
            raise HTTPException(status_code=502, detail=f"生图失败: {e}") from e
        except PerchanceError as e:
            logger.error("perchance 错误: %s", e)
            raise HTTPException(status_code=502, detail=f"生成错误: {e}") from e
        except Exception as e:
            logger.exception("生图未预期错误")
            raise HTTPException(status_code=500, detail=f"内部错误: {e}") from e

        image_b64 = base64.b64encode(result.image_bytes).decode("ascii")
        images.append(
            ImageItem(
                image=image_b64,
                file_extension=result.file_extension,
                seed=result.seed,
                width=result.width,
                height=result.height,
                maybe_nsfw=result.maybe_nsfw,
            )
        )
        logger.info("已完成 %d/%d 张", i + 1, req.count)

    return GenerateResponse(
        images=images,
        prompt=full_prompt,
        negative_prompt=full_negative,
        style=req.style,
    )
