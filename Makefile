SHELL := /bin/bash
export

CHESS_TUTOR_BACKEND_HOST ?= 127.0.0.1
CHESS_TUTOR_BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000
# Interface the frontend binds. Loopback is safe; use 0.0.0.0 to reach it from a phone.
DEV_HOST ?= 127.0.0.1
# Address the phone uses to reach this machine (LAN IP, or the Tailscale MagicDNS name). When set,
# the browser calls the API through the frontend at /api, so the backend can stay on loopback.
PUBLIC_HOST ?=
BACKEND_INTERNAL_URL ?= http://127.0.0.1:$(CHESS_TUTOR_BACKEND_PORT)
# Keep CORS in step with the frontend port unless the operator set the origins explicitly.
CHESS_TUTOR_FRONTEND_ORIGINS ?= ["http://localhost:$(FRONTEND_PORT)","http://127.0.0.1:$(FRONTEND_PORT)"]
NEXT_PUBLIC_API_BASE_URL ?= $(if $(PUBLIC_HOST),/api,http://127.0.0.1:$(CHESS_TUTOR_BACKEND_PORT))

.PHONY: dev test lint

dev:
	@python3 scripts/check_ports.py --host $(DEV_HOST) CHESS_TUTOR_BACKEND_PORT=$(CHESS_TUTOR_BACKEND_PORT) FRONTEND_PORT=$(FRONTEND_PORT)
	@cd backend && uv run python -c "from chess_tutor.settings import Settings; Settings()"
	@trap 'kill 0' INT TERM EXIT; \
	(cd backend && exec uv run uvicorn chess_tutor.main:create_app --factory --reload --host $(CHESS_TUTOR_BACKEND_HOST) --port $(CHESS_TUTOR_BACKEND_PORT)) & \
	(cd frontend && exec npx next dev --hostname $(DEV_HOST) --port $(FRONTEND_PORT)) & \
	wait -n

test:
	cd backend && uv run pytest
	cd frontend && npm test

lint:
	cd backend && uv run ruff check && uv run black --check . && uv run mypy src tests
	cd frontend && npm run lint && npm run typecheck
