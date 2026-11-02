FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS source-markers

ARG AI_PLATFORM_BUILD_COMMIT=unknown
ARG AI_PLATFORM_BUILD_DIRTY=unknown

WORKDIR /app

RUN printf '%s\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.ai-platform-source-revision \
    && printf '%s\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.codex-source-revision \
    && printf '%s\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.source-commit \
    && AI_PLATFORM_BUILD_COMMIT="$AI_PLATFORM_BUILD_COMMIT" \
       AI_PLATFORM_BUILD_DIRTY="$AI_PLATFORM_BUILD_DIRTY" \
       python -c "import json, os; from pathlib import Path; commit = os.environ.get('AI_PLATFORM_BUILD_COMMIT', 'unknown').strip() or 'unknown'; dirty_text = os.environ.get('AI_PLATFORM_BUILD_DIRTY', 'unknown').strip().lower(); dirty = dirty_text != 'false'; dirty_paths = [] if not dirty else ['unknown_runtime_affecting_dirty_paths']; payload = dict(schema_version='ai-platform.source-snapshot.v1', source_tree_commit_sha=commit, runtime_subject_commit_sha=commit, source_tree_dirty=dirty, runtime_affecting_changes_since_runtime_subject=[], runtime_affecting_dirty_paths=dirty_paths, snapshot_source='dockerfile_build_args'); Path('/app/.ai-platform-source-snapshot.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')"

FROM ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded AS uv

FROM node:22.23.2-bookworm@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a AS document-node
WORKDIR /opt/ai-platform/document-dependencies
COPY app/platform/sandbox/document_dependencies/package*.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never
ENV PATH="/app/.venv/bin:$PATH"

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG APT_MIRROR
ARG APT_SECURITY_MIRROR

WORKDIR /app

RUN APT_MIRROR="$APT_MIRROR" APT_SECURITY_MIRROR="$APT_SECURITY_MIRROR" python -c 'import os; from pathlib import Path; p = Path("/etc/apt/sources.list.d/debian.sources"); archive = os.environ.get("APT_MIRROR", ""); security = os.environ.get("APT_SECURITY_MIRROR", ""); mirrors = {key: archive for key in ("http://deb.debian.org/debian", "https://deb.debian.org/debian") if archive} | {key: security for key in ("http://deb.debian.org/debian-security", "https://deb.debian.org/debian-security", "http://security.debian.org/debian-security", "https://security.debian.org/debian-security") if security}; lines = p.read_text(encoding="utf-8").splitlines(); rewritten = [line.partition(":")[0] + ": " + " ".join(mirrors.get(uri.rstrip("/"), uri) for uri in line.partition(":")[2].split()) if line.lstrip().startswith("URIs:") else line for line in lines]; p.write_text("\n".join(rewritten) + "\n", encoding="utf-8")' \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk fonts-liberation gcc libc6-dev git libexpat1 libssh2-1 libreoffice-calc libreoffice-impress libreoffice-writer pandoc passwd poppler-utils qpdf tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra unzip zip \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ai-platform \
    && useradd --uid 10001 --gid 10001 --home-dir /home/ai-platform --create-home --shell /usr/sbin/nologin ai-platform \
    && install -d -o 10001 -g 10001 -m 0700 \
       /home/ai-platform/tmp \
       /home/ai-platform/.cache \
       /home/ai-platform/.config \
       /home/ai-platform/.local/share

COPY --from=uv /uv /uvx /bin/
COPY --from=document-node /usr/local/bin/node /usr/local/bin/node
COPY --from=document-node /opt/ai-platform/document-dependencies /opt/ai-platform/document-dependencies
ENV NODE_PATH=/opt/ai-platform/document-dependencies/node_modules
COPY pyproject.toml uv.lock /app/
RUN if [ -n "$PIP_INDEX_URL" ]; then export UV_DEFAULT_INDEX="$PIP_INDEX_URL"; fi \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then export UV_INSECURE_HOST="$PIP_TRUSTED_HOST"; fi \
    && uv sync --locked --no-dev --no-install-project

COPY app /app/app
RUN python -c "from urllib.request import urlretrieve; from pathlib import Path; from app.execution.domain.document_plugin import DOCUMENT_PLUGIN_REVISION; from app.execution.infrastructure.document_plugin import install_document_plugin, DOCUMENT_PLUGIN_PATH; archive = Path('/tmp/anthropic-document-skills.zip'); urlretrieve('https://codeload.github.com/anthropics/skills/zip/' + DOCUMENT_PLUGIN_REVISION, archive); install_document_plugin(archive, DOCUMENT_PLUGIN_PATH); archive.unlink()"
COPY tools /app/tools
COPY scripts /app/scripts
COPY --chmod=0755 docker-entrypoint.sh /app/docker-entrypoint.sh
COPY docs/release-evidence /app/docs/release-evidence

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

COPY --from=source-markers /app/.ai-platform-source-revision /app/.codex-source-revision /app/.source-commit /app/.ai-platform-source-snapshot.json /app/

RUN chmod -R a+rX /app \
    && chmod 0755 /app/docker-entrypoint.sh

RUN install -d -o 10001 -g 10001 -m 0700 /workspace

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
