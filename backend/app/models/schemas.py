"""请求 / 响应数据模型。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# 允许的形状
Shape = Literal["portrait", "square", "landscape"]


class GenerateRequest(BaseModel):
    """生图请求(文生图 / 图生图统一入口)。"""

    prompt: str = Field(..., min_length=1, description="图像提示词")
    negative_prompt: str = Field("", description="负面提示词")
    seed: int = Field(-1, description="随机种子,-1 表示随机")
    shape: Shape = Field("square", description="图片形状")
    guidance_scale: float = Field(7.0, ge=1.0, le=30.0, description="引导系数")
    style: str = Field("none", description="风格预设名称,对应 art_styles.json 中的 name")
    reference_image: Optional[str] = Field(
        None,
        description="图生图参考图(base64 data URL 或 http URL)。为空则文生图。",
    )
    count: int = Field(1, ge=1, le=4, description="生成图片数量,1-4 张")


class ImageItem(BaseModel):
    """单张生成图片。"""

    image: str = Field(..., description="base64 编码的图片(不含 data: 前缀)")
    file_extension: str = Field("jpeg", description="图片格式")
    seed: int = Field(..., description="实际使用的种子")
    width: int = Field(..., description="图片宽度")
    height: int = Field(..., description="图片高度")
    maybe_nsfw: bool = Field(False, description="是否可能为 NSFW 内容")


class GenerateResponse(BaseModel):
    """生图响应。返回 base64 图片 + 元数据。"""

    images: list[ImageItem] = Field(..., description="生成的图片列表")
    prompt: str = Field(..., description="实际发送的完整提示词(含风格前缀)")
    negative_prompt: str = Field(..., description="实际发送的完整负面提示词")
    style: str = Field(..., description="使用的风格名称")


class ArtStyle(BaseModel):
    """风格预设。"""

    name: str = Field(..., description="风格名称(前端下拉显示)")
    positive_prefix: str = Field("", description="拼到用户 prompt 前的正向描述词")
    negative_prefix: str = Field("", description="拼到用户负面 prompt 前的描述词")


class HealthResponse(BaseModel):
    """健康检查响应。

    除了基础的进程/浏览器就绪状态,还包含 Cloudflare 验证状态,
    便于运维监控判断服务是否真正可用。
    """

    status: str = "ok"
    browser_ready: bool = False
    # Cloudflare 验证状态: unknown / checking / verified / failed
    cf_status: str = "unknown"
    # 上次检查 CF 状态的时间(ISO 格式),None 表示从未检查过
    cf_last_checked_at: Optional[str] = None
    # 上次 CF 验证失败的错误信息(可选)
    cf_last_error: Optional[str] = None
    # 是否已缓存 userKey(有缓存说明曾验证通过过)
    user_key_cached: bool = False
    # 使用的求解器类型: none / browser / flaresolverr / capsolver
    solver_type: str = "none"
    # 求解器是否可用(FlareSolverr 能否连通等)
    solver_available: bool = False
