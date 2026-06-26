# ── Stage 1: 构建 React/Vite 前端静态资源 ────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app

# 先装依赖（利用 Docker 层缓存：package*.json 不变则不重装）
COPY package.json package-lock.json ./
RUN npm ci

# 构建。VITE_WS_URL 留空 → 前端运行时按 window.location.host 推导 ws://<host>/ws，
# 故同一镜像可部署到任意 IP，无需重新构建。
COPY . .
RUN npm run build

# ── Stage 2: Nginx 同时服务静态资源 + 反代后端（单一入口） ─────────────────────
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
