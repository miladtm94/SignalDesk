FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRADING_CONSOLE_DATA_DIR=/app/backend/data

WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
COPY . /app
RUN pip install --no-cache-dir . -r /tmp/requirements.txt

RUN useradd --create-home console \
 && mkdir -p /app/backend/data \
 && mkdir -p /app/reports \
 && chown -R console:console /app
USER console

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
