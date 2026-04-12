FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /src/frontend

COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/tools ./tools
RUN npm ci

COPY frontend/src ./src
RUN npm run build


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_HOST=0.0.0.0 \
    APP_PORT=8090 \
    DATABASE_URL=sqlite:///./data/app.db

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt manage.py alembic.ini ./
COPY app/db/alembic ./app/db/alembic
COPY app ./app
COPY frontend ./frontend
COPY docker-entrypoint.sh ./docker-entrypoint.sh
COPY .env.example ./.env.example

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt gunicorn

COPY --from=frontend-builder /src/frontend/static/index.js ./frontend/static/index.js
COPY --from=frontend-builder /src/frontend/static/settings.js ./frontend/static/settings.js

RUN mkdir -p /app/data \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 8090

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/bin/sh", "-c", "exec gunicorn --workers 1 --threads 4 --bind 0.0.0.0:${APP_PORT:-8090} 'app.web:create_app()'"]
