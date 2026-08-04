# Perchance AI 生图接口完整分析报告

> 来源:`https://perchance.org/ai-character-generator`(基于 `text-to-image-plugin`)
> 分析方式:抓取页面源码 + 嵌入脚本 + 实测接口(curl 实际生成图片验证协议)+ 对照 GitHub 逆向工程库(eeemoon/perchance、aein00/perchance-image-generator)

---

## 一、核心结论(必读)

**Perchance 没有公开的、基于 Token 的干净 REST API。** 整个生图系统受 **Cloudflare Turnstile + Bot Management** 保护。纯 HTTP 客户端(curl/requests)只能短暂工作,几次请求后该 IP 就会被 Cloudflare 拦截,返回 403 挑战页,要求真实的浏览器 `cf_clearance` Cookie。

因此,所有可用的第三方接入方案,**都必须使用真实浏览器(Playwright/Patchright 无头浏览器)**,在已通过 Cloudflare 验证的浏览器上下文里用 `page.evaluate(fetch(...))` 发起请求。

---

## 二、三层架构

| 层 | 地址 | 作用 |
|---|---|---|
| 1. 生成器插件 | `https://perchance.org/text-to-image-plugin` | 用户侧 UI。**不直接调用 HTTP**,而是构建一个带 JSON 的 `<iframe>` |
| 2. 嵌入页面 | `https://image-generation.perchance.org/embed#<urlencoded JSON>` | 嵌入 iframe 中的实际应用。读 URL hash → 跑 Cloudflare Turnstile 拿 `userKey` → 调用生成 API → 通过 `postMessage` 把图片回传给父页面 |
| 3. 生成 API | `https://image-generation.perchance.org/api/...` | 真正干活的 HTTP JSON 端点 |

> Turnstile 站点密钥(硬编码在嵌入脚本):`0x4AAAAAAAA8g8NphwaSOT59`

---

## 三、所有 HTTP 端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/verifyUser?thread={n}&__cacheBust={rand}` | GET | 获取/检查 `userKey`(IP 可信时无需 Turnstile Token) |
| `/api/verifyUser?token={turnstileToken}&thread={n}&__cacheBust={rand}` | GET | 需要 Turnstile 时用 Token 验证 |
| `/api/checkUserVerificationStatus?userKey={key}&cacheKey={n}` | GET | 检查 `userKey` 是否仍有效 |
| `/api/generate` | **POST** | 主生图调用 |
| `/api/downloadTemporaryImage?imageId={id}` | GET | 下载生成的图(Cloudflare 保护) |
| `/api/downloadTemporaryImageViaProxy?t={token}` | GET | generate 响应里返回的备用下载 URL |
| `/api/getUserQueuePosition?userKey={key}&requestId={id}` | GET | 轮询队列位置 |
| `/api/awaitExistingGenerationRequest?userKey={key}` | GET | 等待进行中的请求 |
| `/api/flagImage?channel=...&subChannel=...&fileExtension=...` | GET | 标记不当内容 |

---

## 四、生图接口 `/api/generate` 完整契约

### 请求

**URL:** `POST https://image-generation.perchance.org/api/generate?userKey={64位hex}&requestId={任意字符串}&__cacheBust={随机数}`

**Query 参数:**
| 参数 | 必需 | 说明 |
|---|---|---|
| `userKey` | 是 | 64 位十六进制字符串,来自 verifyUser |
| `requestId` | 是 | 任意追踪字符串,如 `"aiImageCompletion"+随机数` |
| `adAccessCode` | 否 | 广告支持的访问码 |
| `__cacheBust` | 否 | 缓存清除随机数 |

**请求体(JSON, `Content-Type: application/json`):**
```json
{
  "prompt": "a cute orange cat",
  "negativePrompt": "",
  "seed": -1,
  "resolution": "512x512",
  "guidanceScale": 7,
  "channel": "ai-text-to-image-generator",
  "subChannel": "public",
  "userKey": "<64-hex>",
  "adAccessCode": "",
  "requestId": "aiImageCompletion123",
  "referenceImage": { "url": "...", "blur": 0 }
}
```

### 参数详情

| 参数 | 类型 | 默认值 | 取值范围/备注 |
|---|---|---|---|
| `prompt` | string | (必需) | 图像提示词。支持内联覆盖:`(seed:::123) (resolution:::512x768) (guidanceScale:::10) (negativePrompt:::text)`,发送前会被解析出来 |
| `negativePrompt` | string | `""` | 不希望出现的内容 |
| `seed` | number | `-1` | `-1`=随机。返回的整数是实际使用的 seed |
| `resolution` | string | `"512x512"` | 有效:`512x512`、`768x768`、`768x512`(横向)、`512x768`(纵向) |
| `guidanceScale` | number | `7` | 浮点数,范围约 `1-20`(部分库称 1-30) |
| `channel` | string | - | 生成器名,如 `"ai-text-to-image-generator"`,设为 `window.generatorName` |
| `subChannel` | string | `"public"` | 图库子频道(小写字母/数字/连字符) |
| `userKey` | string | - | 必需,来自 verifyUser 的 64 位 hex |
| `adAccessCode` | string | `""` | 可选 |
| `requestId` | string | - | 必需的追踪字符串 |
| `referenceImage` | object | - | 可选,图生图引用。`{ url, blur }`,blur 必须 >1 |

> **注意:`numInferenceSteps`、`brightness`、`sharpness`、`colorBlend`、`imageFormat`、`images` 这些参数在 `/api/generate` 中均不存在**,推理步数等是服务端固定值,不可调。

> 网页 UI 上的 **Shape(形状)** 选项映射到 resolution:`Portrait→512x768`、`Square→768x768`、`Landscape→768x512`

### 响应(JSON,非图片直返)

成功:
```json
{
  "status": "success",
  "imageId": "60daa36f9913203bd74be8620ffb09a96264c85e47b119dfcaaaae4f4852d134",
  "fileExtension": "jpeg",
  "seed": 46590389,
  "prompt": "a cute orange cat sitting on a windowsill",
  "width": 512,
  "height": 512,
  "guidanceScale": 7,
  "negativePrompt": "",
  "maybeNsfw": false,
  "imageDownloadUrl": "/api/downloadTemporaryImageViaProxy?t=v1.joIJ-..."
}
```

**返回 JSON,不是图片本身。** 图片通过 `imageId`(`/api/downloadTemporaryImage?imageId=`)或 `imageDownloadUrl` 下载,`imageDownloadUrl` 优先。`fileExtension` 通常是 `jpeg` 或 `webp`。

其他 `status` 值:
| status | 含义 | 处理 |
|---|---|---|
| `invalid_key` | userKey 错误/过期 | 重新验证 |
| `invalid_ad_access_code` | 访问码无效 | 重新获取 adAccessCode |
| `waiting_for_prev_request_to_finish` | 该线程忙 | 等待/轮询 |
| `gen_failure` (type:1) | 生成失败 | 重试 |
| `stale_request` | requestId 过期 | 重新请求 |
| `fetch_failure` | 网络错误 | 重试 |

---

## 五、认证 / 会话密钥机制

**没有传统的 API Token、账号或 Cookie 会话。** 机制如下:

1. **`userKey`(会话密钥):** 64 位十六进制字符串,通过 `/api/verifyUser` 获取,特定于**线程**(0 到 `maxThreadsPerUser-1`)。目前 `maxThreadsPerUser=1`,所以 `thread=0` 是唯一选择。
2. **验证流程(verifyUser()):**
   - 先试**无 Token 验证**:`GET /api/verifyUser?thread=0`。IP 可信时返回 `{"status":"already_verified","userKey":"<64-hex>"}`
   - 失败则加载 **Cloudflare Turnstile** 小组件(站点密钥 `0x4AAAAAAAA8g8NphwaSOT59`),解决挑战后提交 `GET /api/verifyUser?token=<turnstileToken>&thread=0`,返回 `{"status":"success","userKey":"<64-hex>"}`
3. **`adAccessCode`(可选):** 通过父页面 `postMessage` 协议获取——嵌入页向 perchance.org 发 `{type:"plsGibAccessCodeForAdPoweredStuff"}`,接收 `{type:"okayYouMayHaveCodeForAdPoweredStuff♡", code}`。不设置则为空字符串。
4. **Cloudflare `cf_clearance`:** 真正的网络层拦截。验证/生成端点在严格 Bot Management 下;下载端点保护更严。纯 curl 会收到 `403 Just a moment...` 挑战页。

---

## 六、速率限制

- **Cloudflare 按 IP 限流**:约 6-7 次快速请求后开始返回 403 挑战页(最硬性限制)
- **`waiting_for_prev_request_to_finish`**:服务端强制每个线程串行,`maxThreadsPerUser=1` → 每个 userKey 同时只能生成一张
- **`too_many_requests`**:服务端会返回此状态
- 嵌入脚本通过 `localStorage` 在同域并发嵌入页间协调(同时只有一个进行验证)

---

## 七、外部调用方式(接入你的网站)

### 必须用真实浏览器

你需要一个**服务端代理**,运行无头浏览器(Playwright Chromium/Firefox 或 Patchright):

1. 启动无头浏览器
2. 导航到 `https://image-generation.perchance.org/api/verifyUser?thread=0&__cacheBust=<rand>`,解析返回中的 `"userKey":"<64-hex>"`
3. 在浏览器上下文内,用 `fetch(url, {method:"POST", headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})` 调用 `/api/generate?userKey=...&requestId=...&__cacheBust=...`
4. 在浏览器上下文内 `fetch` `imageDownloadUrl`(或 `/api/downloadTemporaryImage?imageId=...`),读 Blob → base64 → 二进制

> 之所以必须在浏览器内 fetch:fetch 继承了浏览器的 `cf_clearance` Cookie 和同源凭证。**CORS 只允许 `perchance.org` / `image-generation.perchance.org` 源**,所以**前端浏览器 JS 直接调用不可行**,必须服务端代理。

### 推荐参考库

| 库 | 说明 |
|---|---|
| **`eeemoon/perchance`** (PyPI `pip install perchance`,65⭐) | 最成熟。基于 Playwright,用现代 POST `/api/generate`。API:`ImageGenerator.image(prompt, negative_prompt=, seed=, shape='portrait'\|'square'\|'landscape', guidance_scale=)`。**首选参考** |
| `aein00/perchance-image-generator` (14⭐) | CLI。用 Playwright 抓网络流量提取 userKey(正则 `userKey=([a-f\d]{64})`),用旧的 GET `/api/generate` 形式 + requests |
| `YaguriDev/node-perchance-unofficial-api` | Node 实现(Patchright),仅有 README |

---

## 八、Python 服务端代理示例(基于 eeemoon/perchance 模式)

```bash
pip install perchance playwright
playwright install chromium
```

```python
from perchance import ImageGenerator

gen = ImageGenerator()  # 内部启动无头浏览器,自动处理 verifyUser + cf_clearance

img_bytes = gen.image(
    prompt="a cute orange cat",
    negative_prompt="blurry, low quality",
    seed=-1,                       # -1 = 随机
    shape="portrait",              # portrait(512x768) / square(768x768) / landscape(768x512)
    guidance_scale=7,
)
# img_bytes 是图片二进制,保存或返回给前端
with open("out.jpg", "wb") as f:
    f.write(img_bytes)
```

包装成你自己的 HTTP 服务:

```python
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
from perchance import ImageGenerator

app = FastAPI()
gen = ImageGenerator()  # 复用实例,避免反复启动浏览器

class GenReq(BaseModel):
    prompt: str
    negative_prompt: str = ""
    seed: int = -1
    shape: str = "square"          # portrait / square / landscape
    guidance_scale: float = 7

@app.post("/api/generate")
def generate(req: GenReq):
    img = gen.image(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        seed=req.seed,
        shape=req.shape,
        guidance_scale=req.guidance_scale,
    )
    return Response(content=img, media_type="image/jpeg")
```

前端直接调你自己的 `/api/generate`,完全不碰 perchance 的 CORS/Cloudflare 问题。

---

## 九、关键风险与合规提示

1. **Perchance 服务条款**限制仅允许在 perchance.org 上嵌入使用。大规模商用接入有合规风险,建议仅个人/测试用途。
2. **IP 易被限流**:Cloudflare 约 6-7 次快速请求即触发挑战。生产环境需做请求间隔 + 多 IP/userKey 轮换 + 重试。
3. **userKey 会过期**,需定期通过 verifyUser 刷新。
4. **`maybeNsfw`** 字段需在前端做内容过滤判断。
5. `maxThreadsPerUser=1`,并发需多实例(多浏览器上下文 + 多 IP)。
6. 接口为非官方逆向,**随时可能变更**,需持续维护。

---

## 十、UI 选项与 API 参数对应关系

网页 `ai-character-generator` 上的控件如何映射到 API:

| 网页 UI | API 参数 |
|---|---|
| Description 输入框 | `prompt`(支持 `{a\|b\|c}` 随机选词、`(text)` 加权) |
| Art Style 下拉(80+ 风格) | **拼接到 prompt 前缀**(由插件模板处理,非独立 API 参数) |
| Shape: Portrait/Square/Landscape | `resolution` = `512x768` / `768x768` / `768x512` |
| 🎲 骰子 | 随机化提示词模板 |
| 🧠 按钮 | 调用 `ai-text-plugin` 扩写 prompt |
| seed(无 UI,默认 -1) | `seed` |

> 80+ 种 Art Style(Painted Anime、Cinematic、Pixel Art、Studio Ghibli、3D Disney、MTG Card 等)**不是 API 参数**,而是 perchance 插件在客户端把风格描述词拼进 prompt 的预设模板。要复刻这些风格,你需要自己收集每个风格对应的 prompt 前缀词(可从插件源码 `<script id="preloaded-generator-data">` 提取)。
