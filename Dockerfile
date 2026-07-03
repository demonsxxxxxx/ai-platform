FROM python:3.11-slim

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown

LABEL org.opencontainers.image.title=ai-platform
LABEL org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.runtime-subject=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source_revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source_commit=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.runtime_subject=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source_tree_commit=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.build-dirty="$AI_PLATFORM_BUILD_DIRTY"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG APT_MIRROR

WORKDIR /app

RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org/debian|$APT_MIRROR|g; s|http://security.debian.org/debian-security|$APT_MIRROR-security|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY app /app/app
COPY tools /app/tools
COPY scripts /app/scripts
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN if [ -n "$PIP_INDEX_URL" ]; then pip config set global.index-url "$PIP_INDEX_URL"; fi \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi \
    && pip install --no-cache-dir -e . \
    && chmod +x /app/docker-entrypoint.sh
COPY skills /app/skills
COPY docs/release-evidence /app/docs/release-evidence

ENV APP_MODULE=app.main:create_app
ENV APP_PORT=8020

EXPOSE 8020

# Executor override example: APP_MODULE=app.runtime.sandbox.executor_app:create_executor_app APP_PORT=18000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn"]
