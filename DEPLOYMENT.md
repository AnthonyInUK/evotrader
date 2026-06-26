# EvoTraders 部署指南

单条 `docker compose up` 起全栈：Nginx(统一入口) → 前端 + REST API + WebSocket 网关 + Postgres。

## 架构

```
                         :80 (WEB_PORT)
                            │
                    ┌───────▼────────┐
   浏览器 ──────────▶│  frontend      │  Nginx：静态前端 + 反向代理
                    │  (nginx)       │
                    └───┬────────┬───┘
              /api/*    │        │   /ws
                 ┌──────▼──┐  ┌──▼─────────┐
                 │  api    │  │  gateway   │
                 │ :8000   │  │  :8765     │
                 │ FastAPI │  │ WebSocket  │
                 └────┬────┘  └──────┬─────┘
                      │              │
                   ┌──▼──────────────▼──┐
                   │      db (Postgres)  │
                   └─────────────────────┘
```

- **frontend**（Nginx）：唯一对外端口。服务前端静态资源；`/api/*` 反代到 FastAPI，`/ws` 反代到 WebSocket 网关（含 Upgrade 头）。用 Docker 内嵌 DNS + 变量 proxy_pass，上游解析推迟到请求时，避免启动时序问题。
- **api**（FastAPI :8000）：研究/信号/策略 REST 接口。
- **gateway**（WebSocket :8765）：前端实时数据来源，跑交易 pipeline。**默认 mock 模式**——用模拟行情，不需真实 API key、不动真钱，开箱即用。
- **db**（Postgres）：持久化，仅内部访问。

前端 WebSocket 地址在运行时按 `window.location.host` 推导（`ws://<host>/ws`），故同一镜像可部署到任意 IP/域名，无需重新构建。

## 快速开始（本地或云服务器 IP）

```bash
cd evotraders
cp env.template .env          # 按需填 key；mock 模式可全空
docker compose up -d --build  # 首次构建后端镜像较久(agentscope/akshare)
```

打开 `http://<服务器IP>/`（本地为 `http://localhost/`）。

改对外端口：`WEB_PORT=8080 docker compose up -d`。

## 切换到真实数据

默认 mock 不需 key。要跑真实行情/回测：

1. `.env` 填入对应 key（至少 `FINANCIAL_DATASETS_API_KEY` 或 `FINNHUB_API_KEY`；A股需 `TUSHARE_API_TOKEN`；记忆模块需 `DASHSCOPE_API_KEY`；LLM 按 provider 填 `DEEPSEEK_API_KEY`/`OPENAI_API_KEY`）。
2. 改 `docker-compose.yml` 中 gateway 的 command：去掉 `--mock`，按需 `--mode backtest --start-date ... --end-date ...` 或 `--mode live`。

## HTTPS（可选，无域名）

无域名只能自签证书（浏览器会提示不受信，但链路加密）：在 frontend 的 nginx 加 `listen 443 ssl` + 自签证书挂载；或在 compose 前置一层 Caddy 用内部 CA。生产建议买域名后用 Let's Encrypt 自动签发。

## 常用命令

```bash
docker compose logs -f gateway    # 看网关日志
docker compose ps                 # 服务状态
docker compose down               # 停止
docker compose down -v            # 停止并清库
```
