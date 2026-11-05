# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
# Optional trust mounts are for managed build environments; they never enter a layer.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci --strict-ssl=true
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000
WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=secret,id=system_ca \
    if [ -f /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; \
    python -m pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 sori && useradd --uid 10001 --gid 10001 --no-create-home sori
COPY --chown=10001:10001 backend ./backend
COPY --chown=10001:10001 scripts/start.sh ./scripts/start.sh
COPY --from=frontend --chown=10001:10001 /app/dist ./dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import http.client,json,os; c=http.client.HTTPConnection('127.0.0.1',int(os.environ.get('PORT','8000')),timeout=4); c.request('GET','/api/health'); r=c.getresponse(); assert r.status==200 and json.loads(r.read()).get('ok') is True"]
CMD ["bash", "scripts/start.sh"]
