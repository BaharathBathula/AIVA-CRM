# One container for hosted demos: the Next.js web app (public port) and the
# FastAPI backend (localhost only), started together by deploy/start.sh.
# Build from the repository root:  docker build -f deploy/Dockerfile .

# ---------- Web app build ----------
FROM node:22-bookworm-slim AS web-build
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
ENV NEXT_OUTPUT=standalone
RUN npm run build \
 && cp -r public .next/standalone/ \
 && cp -r .next/static .next/standalone/.next/

# ---------- Runtime ----------
FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# Node.js runtime for the web server (same Debian release as this image).
COPY --from=web-build /usr/local/bin/node /usr/local/bin/node

WORKDIR /app/api
COPY apps/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./alembic.ini

WORKDIR /app/web
COPY --from=web-build /web/.next/standalone ./

COPY deploy/start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 10000
CMD ["/app/start.sh"]
