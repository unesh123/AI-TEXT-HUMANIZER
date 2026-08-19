FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    NATURALIZER_STATE_DIR=/app/state

WORKDIR /app
COPY . /app

RUN addgroup --system naturalizer && \
    adduser --system --ingroup naturalizer naturalizer && \
    mkdir -p /app/state && \
    chown -R naturalizer:naturalizer /app

USER naturalizer
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import json, urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4); assert json.load(r)['status']=='ok'"

CMD ["python", "server.py"]
