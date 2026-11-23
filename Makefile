-include .env
export

CHESS_TUTOR_BACKEND_HOST ?= 127.0.0.1
CHESS_TUTOR_BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000
# Keep CORS in step with the frontend port unless the operator set the origins explicitly.
CHESS_TUTOR_FRONTEND_ORIGINS ?= ["http://localhost:$(FRONTEND_PORT)","http://127.0.0.1:$(FRONTEND_PORT)"]
NEXT_PUBLIC_API_BASE_URL ?= http://127.0.0.1:$(CHESS_TUTOR_BACKEND_PORT)

.PHONY: dev test lint

dev:
	@python3 scripts/check_ports.py CHESS_TUTOR_BACKEND_PORT=$(CHESS_TUTOR_BACKEND_PORT) FRONTEND_PORT=$(FRONTEND_PORT)
	@trap 'kill 0' INT TERM EXIT; \
	(cd backend && exec uv run uvicorn chess_tutor.main:create_app --factory --reload --host $(CHESS_TUTOR_BACKEND_HOST) --port $(CHESS_TUTOR_BACKEND_PORT)) & \
	(cd frontend && exec npx next dev --port $(FRONTEND_PORT)) & \
	wait

test:
	cd backend && uv run pytest
	cd frontend && npm test

lint:
	cd backend && uv run ruff check && uv run black --check . && uv run mypy src tests
	cd frontend && npm run lint && npm run typecheck
