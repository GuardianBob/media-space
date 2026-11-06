# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend ----
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MEDIA_MANAGER_HOST=0.0.0.0 \
    MEDIA_MANAGER_DATA=/data

# ffprobe is optional but lets scans measure resolution
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY mediamanager/ ./mediamanager/
RUN pip install --no-cache-dir .
COPY --from=frontend /app/frontend/dist ./frontend/dist

# Resolve PROJECT_ROOT (frontend/dist lookup) to /app, not site-packages
RUN pip uninstall -y media-space && rm -rf build

RUN useradd --create-home --uid 1000 app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8787/', headers={'Host': '127.0.0.1:8787'}))" || exit 1

CMD ["python", "-m", "mediamanager", "--port", "8787"]
