FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
ENV HOST=0.0.0.0 GUI_PORT=8765
CMD ["sh", "-c", "uv run --no-sync python -m scraper.db push && uv run --no-sync python -m scraper.app"]
