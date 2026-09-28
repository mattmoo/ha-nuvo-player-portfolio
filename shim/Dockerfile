# Build from the repo root:  docker build -f shim/Dockerfile -t nuvo-shim .
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1000 --home /app nuvo

WORKDIR /app
COPY pyproject.toml README.md ./
COPY custom_components/nuvo_player/aionuvo ./custom_components/nuvo_player/aionuvo
COPY shim ./shim
RUN pip install --no-cache-dir ".[shim]"

USER nuvo
ENV NUVO_CALLBACK_PORT=8096 \
    NUVO_CACHE=/data/locations.json \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8095 8096

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fs localhost:8095/health || exit 1

# Host networking is required: SSDP multicast and GENA callbacks from the zones.
CMD ["uvicorn", "shim.main:app", "--host", "0.0.0.0", "--port", "8095"]
