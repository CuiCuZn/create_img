# 后端 - Perchance 生图代理

FastAPI + Playwright 浏览器代理,调用 perchance.org 生图 API。

## 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

## 启动

```bash
# 开发模式
uvicorn app.main:app --reload --port 8000

# 生产模式(需先构建前端:cd ../frontend && npm run build)
uvicorn app.main:app --port 8000
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/generate` | 生图(文生图/图生图) |
| GET | `/api/styles` | 风格预设列表(87 个) |
| GET | `/api/health` | 健康检查 |
| GET | `/` | 前端页面(生产模式) |
| GET | `/docs` | Swagger API 文档 |

详见上级目录 README.md。
