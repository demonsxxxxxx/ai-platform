FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST

WORKDIR /app

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
