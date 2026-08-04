# AI 角色生成器(基于 Perchance 生图代理)

基于 [perchance.org](https://perchance.org/ai-character-generator) 的 AI 文本生图 / 图生图网站。后端用 Playwright 浏览器代理绕过 Cloudflare 调用 perchance 生图 API,前端 Vue 3 提供完整的生图交互界面。

> ⚠️ **重要**:本项目的生图能力依赖 perchance.org 的非官方接口,受 Cloudflare Turnstile 保护。详见下方[验证环境要求](#验证环境要求)。

---

## 功能

- ✅ **文生图**:输入提示词 + 选择风格/形状,生成图片
- ✅ **图生图**:上传参考图,基于参考图生成(image-to-image)
- ✅ **87 种艺术风格**:从 perchance 原站完整提取(Painted Anime、Cinematic、Studio Ghibli、3D Disney、Pixel Art 等)
- ✅ **参数控制**:种子、引导系数(guidance scale)、形状(竖图/方图/横图)、负面提示词
- ✅ **NSFW 检测**:后端返回 `maybe_nsfw` 标记,前端遮罩提示
- ✅ **图片下载**:一键下载生成结果

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.13 + FastAPI + Playwright(自写浏览器代理) |
| 前端 | Vue 3 + Vite + TypeScript + Tailwind CSS |
| 部署 | 本地开发一体(后端托管前端构建产物) |

## 项目结构

```
create-img/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口,挂载路由 + 静态文件
│   │   ├── config.py            # 配置(端口、超时、形状映射等)
│   │   ├── routers/generate.py  # /api/generate、/api/styles、/api/health
│   │   ├── core/
│   │   │   ├── perchance_client.py  # 浏览器代理核心(verifyUser→generate→download)
│   │   │   └── browser_pool.py       # 浏览器实例池(单例复用 + 串行锁)
│   │   ├── models/schemas.py    # Pydantic 请求/响应模型
│   │   └── data/art_styles.json # 87 个风格预设(从原站提取)
│   ├── scripts/extract_styles.py # 一次性脚本:从原站抓取风格预设
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.vue / main.ts
│   │   ├── views/GeneratorView.vue      # 主页面
│   │   ├── components/
│   │   │   ├── PromptPanel.vue          # 提示词 + 风格/形状/参数面板
│   │   │   ├── ImageDisplay.vue         # 图片展示 + 下载 + NSFW 遮罩
│   │   │   └── RefImageUpload.vue       # 图生图参考图上传
│   │   ├── api/index.ts                 # axios 封装
│   │   └── types.ts                     # 类型定义
│   └── vite.config.ts                   # dev 代理 /api → 后端
├── perchance-生图接口分析.md             # 接口逆向分析报告
└── README.md
```

## 快速开始

### 1. 安装依赖

**后端:**
```bash
cd backend
pip install -r requirements.txt
playwright install chromium   # 安装浏览器二进制(必须)
```

**前端:**
```bash
cd frontend
npm install
```

### 2. 开发模式(前后端分离)

打开两个终端:

```bash
# 终端 1:启动后端(API 在 http://localhost:8000)
cd backend
uvicorn app.main:app --reload --port 8000

# 终端 2:启动前端(dev 代理自动转发 /api 到后端)
cd frontend
npm run dev
```

访问 http://localhost:5173 即可使用。

### 3. 生产模式(后端一体托管)

```bash
# 构建前端
cd frontend
npm run build

# 启动后端(自动托管 frontend/dist)
cd ../backend
uvicorn app.main:app --port 8000
```

访问 http://localhost:8000 即可(前端 + API 同源)。

## API 接口

### `POST /api/generate` — 生图

**请求体(JSON):**
| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `prompt` | string | (必填) | 图像提示词,支持 perchance 语法 `{a\|b\|c}` 随机、`(text)` 加权 |
| `negative_prompt` | string | `""` | 负面提示词 |
| `seed` | int | `-1` | 随机种子,-1 表示随机 |
| `shape` | string | `"square"` | `portrait` / `square` / `landscape` |
| `guidance_scale` | float | `7` | 引导系数,1-30 |
| `style` | string | `"None"` | 风格名(见 `/api/styles`) |
| `reference_image` | string? | `null` | 图生图参考图(base64 data URL),为空则文生图 |

**响应(JSON):**
```json
{
  "image": "<base64>",
  "file_extension": "jpeg",
  "seed": 46590389,
  "width": 768,
  "height": 768,
  "maybe_nsfw": false,
  "prompt": "...",
  "negative_prompt": "...",
  "style": "Cinematic"
}
```

### `GET /api/styles` — 风格列表
返回 87 个风格预设,每个含 `name`、`positive_prefix`、`negative_prefix`。

### `GET /api/health` — 健康检查
返回 `{ "status": "ok", "browser_ready": false }`。

## 验证环境要求(重要)

perchance 的生图 API 受 **Cloudflare Turnstile** 保护。验证流程:

1. **IP 可信时**(多数家庭宽带/未滥用 IP):`verifyUser` 直接返回 `userKey`,**无需 Turnstile**,生图正常工作。
2. **IP 被标记时**(服务器 IP、被滥用 IP、频繁请求):perchance 返回 `token_required`,需要 Turnstile 交互式验证。此时自动化浏览器(headless/headed)**无法通过**,因为 Turnstile widget 在自动化环境里不渲染。

### 如果遇到 `token_required` 错误

后端会返回 HTTP 502,提示"无法通过 perchance 验证"。解决方法:

1. **更换网络环境**:换到可信 IP(如家庭宽带、手机热点),tokenless 验证会直接通过。
2. **使用代理**:在 `browser_pool.py` 的 `new_context` 中配置代理(`proxy={"server": "http://proxy:port"}`),用可信 IP 的代理。
3. **等待冷却**:Cloudflare 限流通常几小时后解除,但 IP 标记可能持续更久。

> 这是 perchance 反爬的根本限制,非代码问题。接口契约(参数、响应格式、调用链路)已全部实测验证正确。详见 `perchance-生图接口分析.md`。

## 风格预设维护

风格数据在 `backend/app/data/art_styles.json`。如需从原站重新抓取:

```bash
cd backend
python -m scripts.extract_styles        # 抓取并覆盖
python -m scripts.extract_styles --print # 仅预览不写文件
```

## 关键配置

`backend/app/config.py` 中可调:

| 配置 | 默认 | 说明 |
|---|---|---|
| `HEADLESS` | `True` | 浏览器无头模式,调试时设 `False` 观察 |
| `GENERATE_TIMEOUT_S` | `180` | 生图整体超时(秒) |
| `MAX_RETRIES` | `2` | 生成失败重试次数 |
| `SHAPE_RESOLUTION` | - | 形状到分辨率映射(portrait=512x768 等) |

## 已知限制

1. **Cloudflare 验证**:IP 被标记时无法自动通过 Turnstile(见上)。
2. **串行生成**:perchance `maxThreadsPerUser=1`,同一时刻只能生成一张,后端用锁串行化。
3. **生图耗时**:约 10-30 秒,前端已设 180s 超时 + loading 动画。
4. **接口非官方**:perchance 接口随时可能变更,client 层已隔离便于维护。
5. **合规**:perchance ToS 限制仅允许 perchance.org 嵌入使用,本项目仅供学习研究。
