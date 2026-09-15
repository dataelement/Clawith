#!/bin/bash
# Docker entrypoint for the single-process target ASGI application.

set -e

# The image enters as root only so the process can drop to the application user.
if [ "$(id -u)" = '0' ]; then
    exec gosu clawith /bin/bash "$0" "$@"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
