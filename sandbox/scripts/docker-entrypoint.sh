#!/bin/sh
set -eu
exec uvicorn sandbox.api.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --timeout-keep-alive 300
