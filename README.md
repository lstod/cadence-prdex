## About this fork

This fork of Cadence shows one ticket, per-API-key rate limiting, built end to end with PRdex, my own agentic SDLC kit. **Start with the pull request: https://github.com/lstod/cadence-prdex/pull/2**
The ticket passed three human gates (intent, spec, and plan), a spec critique, test-first implementation with proof that each test can fail, and three review agents. Each step left an artifact in `docs/work/`, and every acceptance criterion has recorded proof.
The kit itself is deliberately kept out of git, so only the work artifacts and the code appear here. That also means no CI or review runs on GitHub. The tests, checks, and review agents ran locally, and their findings are posted as a comment on the pull request.
More on PRdex: https://lstod.github.io/Psite/#prdex
Everything below this line is the original project's README.
---

Cadence — workflow automation for ops teams

## what is this

Cadence lets you wire together internal tools, webhooks, and APIs into automated workflows. built with FastAPI + SQLAlchemy + Postgres.

## getting started

copy `.env.example` to `.env` and fill in your values. you'll need a running Postgres instance and Redis.

```
docker compose up
```

or run locally without Docker:

```
python3.11 -m venv .venv
source .venv/bin/activate
make install
make run
```

Run `make help` to see the rest of the dev targets (test, lint, typecheck, etc.).

## environment variables

| variable | description |
|---|---|
| `DATABASE_URL` | postgres connection string |
| `REDIS_URL` | redis connection string |
| `SECRET_KEY` | used for token signing |

## endpoints

- POST /login — get a session token
- GET /health — health check
- GET /workflows — list workflows
- POST /workflows — create a workflow
- GET /executions — list workflow executions
- POST /executions — trigger a workflow execution
- GET /hooks/slack — slack webhook receiver
- POST /hooks/github — github webhook receiver
- GET /admin/audit_log — audit log search (admin only)
- GET /billing/usage — usage summary for the current billing period

## running tests

```
pytest
```

run a specific file:

```
pytest tests/services/test_notifications.py
```

## pre-commit hooks

run `pre-commit install` after cloning, then `pre-commit run --all-files` to lint.

## deployment

we use Docker. build the image:

```
docker build -t cadence .
```

push to your registry and update the `IMAGE` env var in your deploy config.

## stack

- python 3.11
- fastapi
- sqlalchemy
- postgres
- redis (queue)
