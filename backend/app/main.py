"""FastAPI 应用入口。

启动:
    cd backend
    uvicorn app.main:app --reload --port 8000

生产模式下(前端已构建),后端同时托管前端静态文件:
    访问 http://localhost:8000/ 即是前端页面,/api/* 是后端接口。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import config
from app.core.browser_pool import browser_pool
from app.routers import generate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("perchance.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:按配置预热浏览器,关闭时清理。

    WARMUP_ON_STARTUP=True 时启动即开浏览器(有头模式下窗口启动后即挂着,
    避免首次生图才弹窗的延迟感);False 时首次生图才懒启动。
    """
    if config.WARMUP_ON_STARTUP:
        logger.info("应用启动,预热浏览器...")
        await browser_pool.warmup()
    else:
        logger.info("应用启动。浏览器将在首次生图时懒启动。")
    yield
    logger.info("应用关闭,清理浏览器...")
    await browser_pool.close()


app = FastAPI(
    title="Perchance 生图代理",
    description="基于 Playwright 浏览器代理的 perchance 生图服务,支持文生图与图生图。",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS:允许前端 dev 服务器(Vite 默认 5173)跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API 路由
app.include_router(generate.router)


# 生产模式:托管前端构建产物
if config.FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIST), html=True), name="frontend")
    logger.info("已挂载前端静态文件: %s", config.FRONTEND_DIST)
else:
    logger.info(
        "前端构建产物不存在(%s),仅提供 API。"
        "开发模式请单独运行前端 `npm run dev`。",
        config.FRONTEND_DIST,
    )
