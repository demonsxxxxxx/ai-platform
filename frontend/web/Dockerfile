# syntax=docker/dockerfile:1.7

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown

FROM node:22-alpine AS build

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown

WORKDIR /workspace
RUN apk add --no-cache python3

ENV AI_PLATFORM_BUILD_COMMIT=${AI_PLATFORM_BUILD_COMMIT}
ENV AI_PLATFORM_BUILD_DIRTY=${AI_PLATFORM_BUILD_DIRTY}

COPY frontend/web/.npmrc frontend/web/package.json frontend/web/pnpm-lock.yaml frontend/web/pnpm-workspace.yaml ./frontend/web/
COPY frontend/web/eslint.config.js frontend/web/index.html ./frontend/web/
COPY frontend/web/postcss.config.js frontend/web/tailwind.config.js frontend/web/tsconfig.json frontend/web/vite.config.ts ./frontend/web/
COPY frontend/web/public ./frontend/web/public
COPY frontend/web/scripts ./frontend/web/scripts
COPY frontend/web/src ./frontend/web/src
COPY tools ./tools
COPY .github/workflows/ai-platform-frontend.yml ./.github/workflows/ai-platform-frontend.yml

WORKDIR /workspace/frontend/web
RUN corepack enable && corepack pnpm install --frozen-lockfile && corepack pnpm run ci:verify

FROM nginx:1.27-alpine

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown

LABEL org.opencontainers.image.title=ai-platform-frontend
LABEL org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT

ENV AI_PLATFORM_API_UPSTREAM=http://api:8020
ENV AI_PLATFORM_FRONTEND_MAX_BODY_SIZE=50m
ENV AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT=300s
ENV AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT=300s
COPY frontend/web/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /workspace/frontend/web/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
