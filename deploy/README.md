# Deploying with Docker Compose

Host-specific details (addresses, users, firewall, DNS) live in the operator's private
infrastructure runbook. Nothing host-specific belongs in this repository.

## Run

```bash
cp .env.example .env        # optional: CHESS_TUTOR_LLM_API_KEY, or CHESS_TUTOR_LLM_PROVIDER=none
docker compose up -d --build
docker compose ps           # both services should report "healthy"
```

The backend serves `/health`; the frontend serves the app. Both ports bind to loopback by
default (`CHESS_TUTOR_BACKEND_BIND`, `CHESS_TUTOR_FRONTEND_BIND`) so only a reverse proxy
reaches them. The first game downloads the Maia-3 weights into the data volume.

### Ports (shared VPS)

Check that the host ports are free before `up`; the check is interface-aware:

```bash
python3 scripts/check_ports.py CHESS_TUTOR_BACKEND_PORT=8000 FRONTEND_PORT=3000
```

Change `CHESS_TUTOR_BACKEND_PORT` and `FRONTEND_PORT` in `.env` on a conflict. The
containers always listen on 8000 and 3000 internally.

## Frontend build argument

Next.js inlines `NEXT_PUBLIC_API_BASE_URL` into the browser bundle **at build time**. It must
be the backend URL the user's browser reaches (for example `https://api.chess.example.com`),
and changing it needs `docker compose build frontend`.

## CORS

Set `CHESS_TUTOR_FRONTEND_ORIGINS` (a JSON list) to include the public frontend origin:

```
CHESS_TUTOR_FRONTEND_ORIGINS=["https://chess.example.com"]
```

A missing or wrong origin shows up as failed browser requests while `curl` still works.

## Reverse proxy with automatic HTTPS (Caddy)

```caddyfile
chess.example.com {
	reverse_proxy 127.0.0.1:3000
}

api.chess.example.com {
	reverse_proxy 127.0.0.1:8000 {
		flush_interval -1   # keep server-sent events (opponent moves, coach) streaming
	}
}
```

With these, set `NEXT_PUBLIC_API_BASE_URL=https://api.chess.example.com` and
`CHESS_TUTOR_FRONTEND_ORIGINS=["https://chess.example.com"]`. Replace the placeholder domains
and point their DNS at the host; Caddy obtains and renews certificates itself.

## Data and backups

One named volume, `chess_tutor_data`, holds the SQLite database (`/data/chess_tutor.db`) and
the Hugging Face model cache (`/data/huggingface`). Migrations run on backend startup.

Back up the database (a consistent copy while the app runs):

```bash
docker compose exec backend python -c \
  "import sqlite3; s=sqlite3.connect('/data/chess_tutor.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
docker compose cp backend:/data/backup.db ./chess_tutor-$(date +%F).db
docker compose exec backend rm /data/backup.db
```

The model cache needs no backup; it re-downloads. Restore by stopping the stack, copying the
file back to `/data/chess_tutor.db` in the volume, and starting again.

## Update

```bash
git pull && docker compose up -d --build
```
