FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY bridge.py /app/bridge.py

# The SSH tunnel owns its socket as root:0600 inside a root:0700 directory.
# Keep those permissions; deployment drops capabilities and mounts it read-only.
USER 0:0

ENTRYPOINT ["python", "/app/bridge.py"]
