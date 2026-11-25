# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Container image for the Docker MCP catalog. The gateway speaks MCP over stdio.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SASSYMCP_HOME=/data \
    SASSYMCP_NO_UPDATE_CHECK=1 \
    SASSYMCP_LOAD_ALL=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY sassymcp ./sassymcp

RUN pip install --no-cache-dir . \
    && mkdir -p /data

WORKDIR /data

LABEL org.opencontainers.image.title="SassyMCP" \
      org.opencontainers.image.source="https://github.com/sassyconsultingllc/SassyMCP" \
      org.opencontainers.image.description="SassyMCP stdio server for the Docker MCP catalog" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary"

# Docker MCP gateway attaches stdin/stdout. Logs stay on stderr.
ENTRYPOINT ["sassymcp", "--stdio"]
