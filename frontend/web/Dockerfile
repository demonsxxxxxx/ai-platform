# syntax=docker/dockerfile:1.7

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown
ARG AI_PLATFORM_BUILD_REPOSITORY=unknown

FROM node:22-bookworm AS build

WORKDIR /workspace

COPY frontend/web/.npmrc frontend/web/package.json frontend/web/pnpm-lock.yaml frontend/web/pnpm-workspace.yaml ./frontend/web/

WORKDIR /workspace/frontend/web
RUN corepack enable && corepack pnpm install --frozen-lockfile

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown
ARG AI_PLATFORM_BUILD_REPOSITORY=unknown

ENV AI_PLATFORM_BUILD_COMMIT=${AI_PLATFORM_BUILD_COMMIT}
ENV AI_PLATFORM_BUILD_DIRTY=${AI_PLATFORM_BUILD_DIRTY}

WORKDIR /workspace

COPY frontend/web/eslint.config.js frontend/web/index.html ./frontend/web/
COPY frontend/web/postcss.config.js frontend/web/tailwind.config.js frontend/web/tsconfig.json frontend/web/vite.config.ts ./frontend/web/
COPY frontend/web/public ./frontend/web/public
COPY frontend/web/scripts ./frontend/web/scripts
COPY frontend/web/src ./frontend/web/src
COPY tools ./tools
COPY .github/workflows/ai-platform-frontend.yml ./.github/workflows/ai-platform-frontend.yml

WORKDIR /workspace/frontend/web
RUN corepack pnpm run ci:verify

FROM nginx:1.27-alpine AS runtime

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown
ARG AI_PLATFORM_BUILD_REPOSITORY=unknown

LABEL org.opencontainers.image.title=ai-platform-frontend
LABEL org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source-commit=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.build-dirty="$AI_PLATFORM_BUILD_DIRTY"
LABEL ai-platform.source-repository=$AI_PLATFORM_BUILD_REPOSITORY
LABEL ai-platform.release-role=frontend

ENV AI_PLATFORM_API_UPSTREAM=http://api:8020
ENV AI_PLATFORM_FRONTEND_MAX_BODY_SIZE=50m
ENV AI_PLATFORM_FRONTEND_PROXY_READ_TIMEOUT=300s
ENV AI_PLATFORM_FRONTEND_PROXY_SEND_TIMEOUT=300s
RUN mkdir -p /etc/nginx/templates /etc/nginx/templates-opensandbox
COPY frontend/web/nginx.conf.template /etc/nginx/templates-opensandbox/default.conf.template
RUN sed '/^# AI_PLATFORM_S72_BRIDGE_BEGIN$/,$d' \
      /etc/nginx/templates-opensandbox/default.conf.template \
      > /etc/nginx/templates/default.conf.template
COPY --from=build /workspace/frontend/web/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD test -f /usr/share/nginx/html/index.html && test -s /usr/share/nginx/html/index.html && stat -c %a /usr/share/nginx/html/index.html | grep -Eq '[4567]$' && test -f /usr/share/nginx/html/manifest.json && test -s /usr/share/nginx/html/manifest.json && stat -c %a /usr/share/nginx/html/manifest.json | grep -Eq '[4567]$' && test -f /usr/share/nginx/html/icons/icon.svg && test -s /usr/share/nginx/html/icons/icon.svg && stat -c %a /usr/share/nginx/html/icons/icon.svg | grep -Eq '[4567]$' && test -f /usr/share/nginx/html/icons/icon-192.png && test -s /usr/share/nginx/html/icons/icon-192.png && stat -c %a /usr/share/nginx/html/icons/icon-192.png | grep -Eq '[4567]$' && test -f /usr/share/nginx/html/icons/icon-512.png && test -s /usr/share/nginx/html/icons/icon-512.png && stat -c %a /usr/share/nginx/html/icons/icon-512.png | grep -Eq '[4567]$' && wget -q -O /dev/null http://127.0.0.1:8080/healthz && wget -q -O /dev/null http://127.0.0.1:8080/manifest.json && wget -q -O /dev/null http://127.0.0.1:8080/icons/icon.svg && wget -q -O /dev/null http://127.0.0.1:8080/icons/icon-192.png && wget -q -O /dev/null http://127.0.0.1:8080/icons/icon-512.png
