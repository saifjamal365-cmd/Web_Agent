FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    WA_BROWSER_CHANNEL=chromium WA_HEADLESS=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY requirements.txt .
# libatomic1: needed by the Node driver that ships inside the playwright package
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/* \
 && pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && useradd --create-home --uid 1000 agent \
 && mkdir -p /app/data/live && chown -R agent /app /ms-playwright

COPY --chown=agent webagent ./webagent
COPY --chown=agent web ./web
USER agent

EXPOSE 8020
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8020/api/health').status==200 else 1)" || exit 1
CMD ["uvicorn", "webagent.api.main:app", "--host", "0.0.0.0", "--port", "8020", "--proxy-headers"]
