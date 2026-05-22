FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY sandbox/requirements.txt /app/sandbox/requirements.txt
RUN pip install --no-cache-dir -r /app/sandbox/requirements.txt

COPY sandbox /app/sandbox
COPY 44ma /app/44ma
COPY "financially free" /app/financially free
COPY KALI /app/KALI

RUN pip install --no-cache-dir -r /app/44ma/requirements.txt \
    && pip install --no-cache-dir -r "/app/financially free/requirements.txt" \
    && pip install --no-cache-dir -e /app/KALI

ENV PYTHONPATH=/app:/app/44ma:/app/KALI/src:/app/financially free

RUN chmod +x /app/sandbox/scripts/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/sandbox/scripts/docker-entrypoint.sh"]
