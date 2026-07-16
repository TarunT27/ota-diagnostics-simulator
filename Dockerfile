FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OTA_DATABASE_PATH=/data/ota-lab.db

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY pyproject.toml README.md ./
COPY ota_simulator ./ota_simulator
COPY main.py ./

RUN pip install --no-cache-dir . \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

USER appuser
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8000"]
