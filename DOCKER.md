# Perchance 生图代理 - Docker 部署

## 架构

```
┌─────────────────────────────────────────────┐
│  Docker 容器 (perchance)                     │
│                                              │
│  ┌──────────┐   ┌──────────────────────┐    │
│  │  Xvfb    │   │  uvicorn :2464       │    │
│  │ (虚拟屏) │   │  ├─ FastAPI /api/*   │    │
│  └────┬─────┘   │  ├─ 前端 dist 静态   │    │
│       │         │  └─ patchright 浏览器 │    │
│       └─────────┼──────────────────────┘    │
│                 │                            │
│          DISPLAY=:99                         │
└─────────────────┼───────────────────────────┘
                  │ :2464
          ┌───────┴───────┐
          │ 外部网关/反代  │ (SSL 在这里处理)
          └───────┬───────┘
                  │
            二级域名访问
```

**单容器**:前端 dist + 后端 uvicorn + patchright 浏览器 + Xvfb 全打包在一起。
端口 2464 直连,SSL 由外部网关处理。

## 文件清单

```
.
├── Dockerfile              # 多阶段构建(前端 build + 后端运行时)
├── docker-compose.yml      # 编排:单服务,端口 2464
├── docker/
│   └── entrypoint.sh       # 启动 Xvfb + uvicorn
├── .dockerignore
├── backend/                # 后端代码
└── frontend/               # 前端源码(构建时用)
```

## 部署步骤

### 1. 服务器装 Docker

```bash
# Ubuntu/Debian 一键装
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER  # 当前用户加入 docker 组(免 sudo)
# 重新登录让组生效
exit  # 然后重新 ssh 登录
docker --version  # 确认
```

### 2. 传项目到服务器

把整个项目目录传上去(本地执行):

```bash
# 方式 A:rsync(推荐,增量快)
rsync -avz --exclude='node_modules' --exclude='__pycache__' \
    --exclude='.venv' --exclude='frontend/dist' \
    -e ssh ./ user@服务器IP:/opt/perchance-app/

# 方式 B:scp 打包
tar czf perchance.tar.gz --exclude='node_modules' --exclude='__pycache__' \
    --exclude='.venv' --exclude='frontend/dist' .
scp perchance.tar.gz user@服务器IP:/opt/
ssh user@服务器IP 'mkdir -p /opt/perchance-app && tar xzf /opt/perchance.tar.gz -C /opt/perchance-app'
```

### 3. 构建并启动

```bash
cd /opt/perchance-app
docker compose up -d --build
```

首次构建会下载基础镜像、装依赖、跑前端 build,**约 5-10 分钟**。
看到 `Container perchance  Started` 就成了。

### 4. 验证

```bash
# 容器状态
docker compose ps

# 健康检查(API 是否响应)
curl http://127.0.0.1:2464/api/health
# 期望: {"status":"ok",...}

# 看实时日志(关键:看浏览器启动 + verifyUser 是否成功)
docker compose logs -f
# 按 Ctrl+C 退出

# 生图测试(真实调用 perchance,会触发 Cloudflare 验证)
curl -X POST http://127.0.0.1:2464/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a cat","shape":"square","style":"None","guidance_scale":7,"seed":-1,"count":1}'
```

访问 `http://服务器IP:2464` 就是前端页面。

## 常用命令

```bash
docker compose up -d --build      # 重新构建并启动(改代码后)
docker compose up -d              # 不重建,用已有镜像启动
docker compose down               # 停止并删除容器
docker compose restart            # 重启
docker compose logs -f            # 实时日志
docker compose logs --tail 100    # 最近 100 行
docker compose exec perchance bash  # 进容器调试
```

## 关键问题:Cloudflare 过不过得去

⚠️ **这是最大的不确定性**。容器内 Xvfb + swiftshader 软件 WebGL,数据中心 IP,
三重不利因素叠加,Cloudflare 可能仍判 bot。

### 快速查看状态

启动后先看健康检查接口:

```bash
curl http://127.0.0.1:2464/api/health | python -m json.tool
```

重点看 `cf_status` 字段:
- `verified` — ✅ 已通过 Cloudflare 验证
- `failed` — ❌ 没过,需要看日志排查
- `unknown` — 浏览器还没启动或还没检查过

### 怎么判断

启动后看日志:

```bash
docker compose logs -f
```

| 日志关键词 | 含义 | 处理 |
|---|---|---|
| `verifyUser 成功获得 userKey` | ✅ 过了 CF | 没问题,直接用 |
| `FlareSolverr 求解成功` | ✅ FlareSolverr 解出来了 | 正常 |
| `verifyUser 要求 Turnstile token` | ⚠️ 被要求验证 | 看后续 FlareSolverr/浏览器能否解 |
| `未能获取 Turnstile token` | ❌ 过不了 | 启用 FlareSolverr 或配住宅代理 |
| `命中 Cloudflare 挑战页` | ❌ cf_clearance 失效 | 启用 FlareSolverr 或配住宅代理 |
| `生图异常...重建浏览器后重试` | 浏览器崩了 | 看崩溃原因 |

### 方案一:启用 FlareSolverr(免费,推荐先试)

FlareSolverr 是自托管的 Cloudflare 求解服务,用 undetected-chromedriver 处理挑战。
零成本,成功率约 60-80%(视 IP 质量)。

**启用步骤**:

1. 编辑 `docker-compose.yml`,取消 `flaresolverr` 服务的注释
2. 把 `perchance` 服务的 `FLARESOLVERR_URL` 改为 `"http://flaresolverr:8191"`
3. 重启:

```bash
docker compose up -d
```

启动后日志里会有 `FlareSolverr 求解成功/失败` 相关记录。
健康检查里 `solver_type` 会变成 `"flaresolverr"`。

> 💡 **提示**: FlareSolverr 额外占 ~200-300MB 内存,2G 服务器刚好够用。
> 如果 FlareSolverr 也过不了,配合下面的住宅代理效果更好。

### 方案二:配住宅代理(成功率最高的免费手段)

代理是过 CF 最有效的手段。买一个住宅代理(BrightData / Smartproxy / IPRoyal 等),
拿到 `http://user:pass@host:port` 格式地址,改 `docker-compose.yml`:

```yaml
    environment:
      HTTP_PROXY: "http://user:pass@proxy.host:port"
      HTTPS_PROXY: "http://user:pass@proxy.host:port"
```

然后重启:

```bash
docker compose up -d
```

代码已支持自动读取这两个环境变量(`browser_pool.py` 的 `_resolve_proxy()`),
不用改任何代码。日志里会打印 `使用代理: http://...`。

### 方案三:增强指纹伪造(默认已开启)

容器内 swiftshader 软件渲染的 WebGL 指纹很容易被识别。
代码默认启用了增强指纹伪造(`ENHANCED_FINGERPRINT=true`),伪造:
- WebGL vendor/renderer(假装是 Intel UHD 620)
- navigator 属性(CPU 核数、内存、平台等)
- Canvas 指纹扰动
- Audio 指纹扰动
- 更完整的 chrome 对象

如需关闭(如遇兼容性问题),在 docker-compose 里设:
```yaml
    environment:
      ENHANCED_FINGERPRINT: "false"
```

### Cookie 持久化(自动生效)

浏览器的 cookies / localStorage 会自动保存到 Docker volume(`perchance-browser-data`),
容器重启后 `cf_clearance` 自动恢复,不用重新验证。

验证:
```bash
# 查看持久化文件是否存在
docker compose exec perchance ls -la /app/browser_data/
# 应该有 storage_state.json
```

### 换有 GPU 的服务器(终极方案)

如果以上都搞不定,说明 Cloudflare 对软件渲染 GPU 指纹识别太严,
只能换带独立显卡的服务器(AWS g4dn / 阿里云 GPU 等),
把 `--use-gl=swiftshader` 去掉(改 `browser_pool.py` launch_args),用真 GPU 渲染。

## 端口 / 域名

- 容器监听 `0.0.0.0:2464`
- 二级域名指向服务器,外部网关/反代处理 SSL,转发到 2464
- 改端口:编辑 `docker-compose.yml` 的 `ports: ["2464:2464"]`,左边宿主机端口

## 故障排查

### 容器起不来

```bash
docker compose logs  # 看启动报错
```

常见:
- `Xvfb 启动失败` -> 罕见,检查 shm_size
- `patchright install` 失败 -> 网络问题,重试 `docker compose build --no-cache`

### 生图返回 502 / 超时

99% 是 Cloudflare 没过。看日志确认,然后配住宅代理。

### 浏览器崩溃

```bash
docker compose logs | grep -i "crash\|segfault\|killed"
```

可能 shm 不够,compose 里已设 `shm_size: "2g"`,应该够。

### 内存不够

```bash
docker stats  # 看容器内存占用
```

patchright chromium 吃内存,1G 服务器可能不够,建议 2G+。
