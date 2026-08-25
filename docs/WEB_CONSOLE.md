# Signal Desk web research console

Signal Desk is a single-user, local-first web layer over TradingAgents. It is a
research notebook, not an execution engine: it starts analyses, preserves each
agent section as it arrives, records the exact model configuration, and makes
past calls easier to annotate and compare.

## Local setup

The web console has its own dependencies so the upstream package remains
untouched.

```bash
make web-install
make web-dev
```

In a second terminal:

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:5174`. The API listens only on
`http://127.0.0.1:8765` by default. API keys can be supplied through the
existing environment variables or saved from Settings; saved values are
encrypted in the console data directory and only their final four characters
are returned.

For a containerized local deployment:

```bash
docker compose up --build web-backend web-frontend
```

Open `http://127.0.0.1:4173`.

## Runtime state

All console-owned state is redirected under `backend/data/` (or
`TRADING_CONSOLE_DATA_DIR`):

- `console.sqlite3` — runs, sections, events, decisions, usage, notes, and keys
- `checkpoints/` — LangGraph per-ticker checkpoint databases
- `memory/` — TradingAgents decision memory log
- `results/` — JSON state and markdown report trees

The directory is git-ignored. `POST /api/runs` is rate-limited in process to
three launches per minute per client. Set `TRADING_CONSOLE_DAILY_CAP_USD` to a
positive value to turn on the optional estimated daily spend cap.

## Security boundary

This release deliberately has no login and binds to localhost. Do not expose it
to a public network: it can hold paid API credentials and trigger costly model
calls. If the scope changes beyond a private, single-user console, authentication,
CSRF protection, a shared rate limiter, and a real task queue become release
requirements.
