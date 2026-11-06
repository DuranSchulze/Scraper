# Run everything from the repository root; no cd needed.
#   make setup     install backend and frontend dependencies (once)
#   make migrate   apply database migrations
#   make api       backend API on http://127.0.0.1:8765
#   make web       frontend GUI on http://127.0.0.1:3000
#   make dev       both at once (Ctrl+C stops both)
#   make test      backend tests + frontend typecheck and lint

.PHONY: setup migrate api web dev test

setup:
	uv sync --directory backend
	npm --prefix frontend install

migrate:
	uv run --directory backend python -m scraper.db migrate

api:
	uv run --directory backend python -m scraper.app

web:
	npm --prefix frontend run dev

dev:
	$(MAKE) -j2 api web

test:
	uv run --directory backend pytest -q
	npm --prefix frontend run typecheck
	npm --prefix frontend run lint
