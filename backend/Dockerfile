FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRADING_CONSOLE_DATA_DIR=/app/backend/data

WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
COPY . /app
# WITH_CLAUDE_CODE=1 adds Claude Code for the Claude plan provider; sign it in
# with a CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token` (Settings, or .env).
# One pip resolution: installed on its own, the Agent SDK's mcp dependency would
# upgrade Starlette past what the pinned FastAPI supports.
ARG WITH_CLAUDE_CODE=0
RUN extra=""; if [ "$WITH_CLAUDE_CODE" = "1" ]; then extra="[claude]"; fi \
 && pip install --no-cache-dir ".${extra}" -r /tmp/requirements.txt

RUN useradd --create-home console \
 && mkdir -p /app/backend/data \
 && mkdir -p /app/reports \
 && chown -R console:console /app
USER console

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
