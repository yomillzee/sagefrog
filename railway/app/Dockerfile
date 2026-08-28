# Browser scanner + API service.
#
# Based on Microsoft's official Playwright image, which ships Chromium AND all of
# its OS libraries pre-installed under /ms-playwright (with PLAYWRIGHT_BROWSERS_PATH
# already pointing there). This replaces the old nixpacks build that ran
# `playwright install --with-deps chromium` on every deploy — an apt install of
# ~30-40 packages plus a ~150 MB browser download that dominated build time.
#
# The image tag is pinned to the same Playwright version as requirements.txt so the
# bundled browser always matches the client library. Bump both together.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

# Chromium won't start its sandbox as root (how the container runs); the scanner
# passes --no-sandbox when this is set. PLAYWRIGHT_BROWSERS_PATH is already set to
# /ms-playwright by the base image, which the scanner auto-detects.
ENV BROWSER_SCANNER_NO_SANDBOX=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python deps first so this layer is cached and only rebuilds when
# requirements.txt changes — the common deploy (app code only) skips it entirely.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application source.
COPY . .

# Railway injects $PORT at runtime; fall back to 8000 for local runs.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
