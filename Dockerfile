# Image unique : le front est compilé puis servi par l'API.
FROM node:22-slim AS front
WORKDIR /front
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --extra postgres
COPY backend/ ./
COPY --from=front /front/dist /app/frontend/dist
# Exécution sous un compte sans privilèges ; la base SQLite vit dans un volume.
RUN useradd --system --uid 10001 pea && mkdir /data && chown pea /data
USER pea
ENV ENVIRONMENT=prod DATABASE_URL=sqlite:////data/pea_radar.db
EXPOSE 8000
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
