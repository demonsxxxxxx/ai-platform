FROM python:3.11-slim

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown
ARG AI_PLATFORM_BUILD_REPOSITORY=unknown

LABEL org.opencontainers.image.title=ai-platform
LABEL org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.runtime-subject=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source_revision=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source_commit=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.runtime_subject=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source_tree_commit=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.build-dirty="$AI_PLATFORM_BUILD_DIRTY"
LABEL ai-platform.source-commit=$AI_PLATFORM_BUILD_COMMIT
LABEL ai-platform.source-repository=$AI_PLATFORM_BUILD_REPOSITORY
LABEL ai-platform.release-role=backend

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG APT_MIRROR

WORKDIR /app

RUN printf '%s\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.ai-platform-source-revision \
    && printf '%s\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.codex-source-revision \
    && printf '%s\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.source-commit \
    && AI_PLATFORM_BUILD_COMMIT="$AI_PLATFORM_BUILD_COMMIT" \
       AI_PLATFORM_BUILD_DIRTY="$AI_PLATFORM_BUILD_DIRTY" \
       python -c "import json, os; from pathlib import Path; commit = os.environ.get('AI_PLATFORM_BUILD_COMMIT', 'unknown').strip() or 'unknown'; dirty_text = os.environ.get('AI_PLATFORM_BUILD_DIRTY', 'unknown').strip().lower(); dirty = dirty_text != 'false'; dirty_paths = [] if not dirty else ['unknown_runtime_affecting_dirty_paths']; payload = dict(schema_version='ai-platform.source-snapshot.v1', source_tree_commit_sha=commit, runtime_subject_commit_sha=commit, source_tree_dirty=dirty, runtime_affecting_changes_since_runtime_subject=[], runtime_affecting_dirty_paths=dirty_paths, snapshot_source='dockerfile_build_args'); Path('/app/.ai-platform-source-snapshot.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')"

RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org/debian|$APT_MIRROR|g; s|http://security.debian.org/debian-security|$APT_MIRROR-security|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk git passwd \
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

RUN groupadd --gid 10001 ai-platform \
    && useradd --uid 10001 --gid 10001 --home-dir /home/ai-platform --create-home --shell /usr/sbin/nologin ai-platform \
    && install -d -o 10001 -g 10001 -m 0700 \
       /home/ai-platform/tmp \
       /home/ai-platform/.cache \
       /home/ai-platform/.config \
       /home/ai-platform/.local/share

ENV APP_MODULE=app.main:create_app
ENV APP_PORT=8020
ENV HOME=/home/ai-platform
ENV TMPDIR=/home/ai-platform/tmp
ENV XDG_CACHE_HOME=/home/ai-platform/.cache
ENV XDG_CONFIG_HOME=/home/ai-platform/.config
ENV XDG_DATA_HOME=/home/ai-platform/.local/share

EXPOSE 8020

# Executor override example: APP_MODULE=app.runtime.sandbox.executor_app:create_executor_app APP_PORT=18000
USER 10001:10001
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn"]
