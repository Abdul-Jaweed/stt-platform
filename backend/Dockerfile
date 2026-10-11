# syntax=docker/dockerfile:1
#
# One image, two roles: the API (`uvicorn`) and the Temporal worker run the
# same code with the same dependencies, so they differ only in command.
#
# ffmpeg is not optional. Validation, duration measurement (a browser WebM has
# no duration header) and transcoding all shell out to it.

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.13-slim-bookworm AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first: the lock file changes far less often than the source, so
# editing a module does not re-resolve the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && rm -rf /tmp/uv-cache

# CUDA runtime for GPU mode. ctranslate2 loads cuBLAS/cuDNN at *runtime* (it
# does not ship them and it does not add the wheel paths to the loader itself),
# so the libraries and an explicit LD_LIBRARY_PATH are both required. On CPU
# these are inert; set --build-arg WITH_CUDA=0 to leave them out of a
# deliberately CPU-only image.
ARG WITH_CUDA=1
RUN if [ "$WITH_CUDA" = "1" ]; then \
      uv pip install --python /opt/venv/bin/python \
        nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12 \
      && rm -rf /tmp/uv-cache; \
    fi

ENV LD_LIBRARY_PATH=/opt/venv/lib/python3.13/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.13/site-packages/nvidia/cudnn/lib:/opt/venv/lib/python3.13/site-packages/nvidia/cuda_runtime/lib

# Non-root. uid 1000 matches the usual desktop user, which keeps a
# bind-mounted model cache writable on Linux; override with --build-arg when
# your host user differs.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid ${APP_GID} app \
 && useradd --uid ${APP_UID} --gid ${APP_GID} --create-home app \
 && mkdir -p /data/tmp /data/audio /app/models \
 && chown -R app:app /app /data
USER app

# `repo_root` is derived from the source path at import time, which lands
# outside the image: pin it. With it pinned, the model cache and the evaluation
# corpus resolve under /app/models exactly as they do on the host, so one bind
# mount serves both.
ENV STT_REPO_ROOT=/app \
    STT_TEMP_DIR=/data/tmp \
    STT_AUDIO_STORE_DIR=/data/audio \
    STT_MODEL_DIR=/app/models/faster-whisper \
    STT_HF_HOME=/app/models/huggingface \
    HF_HOME=/app/models/huggingface \
    HF_HUB_CACHE=/app/models/huggingface/hub \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

EXPOSE 8200

HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8200/v1/health/live || exit 1

CMD ["uvicorn", "stt_platform.main:app", "--host", "0.0.0.0", "--port", "8200"]
