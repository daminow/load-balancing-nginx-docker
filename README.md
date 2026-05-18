# Automated Deployment of a Multi-Service Web Application with CI/CD

FastAPI + PostgreSQL behind an Nginx reverse proxy that load-balances three application replicas. Schema is applied by a one-shot Alembic job. GitHub Actions runs lint, type-check, tests against a real PostgreSQL service container, a Compose smoke build, plus security scans (Trivy, gitleaks, hadolint), and pushes a signed multi-arch image to GHCR.

| Component | Image / version |
|---|---|
| API | FastAPI 0.128.0 on Python 3.12, Uvicorn `--workers 2`, replicas `api1` / `api2` / `api3` |
| Database | `postgres:16.4-alpine`, named volume `lb_postgres_data` |
| Reverse proxy | `nginx:1.27-alpine`, `least_conn`, passive checks, rate limiting, security headers |
| Migrations | One-shot Alembic 1.14 container |
| CI/CD | GitHub Actions — [`.github/workflows/ci.yml`](.github/workflows/ci.yml), [`.github/workflows/security.yml`](.github/workflows/security.yml) |

## Prerequisites

- Docker Engine 20.10 or newer with Docker Compose v2 (`docker compose`).
- Optional for hostside development: Python 3.12 and `pip`.

## Quick start

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD to a real value before anything that is not local.
docker compose up -d --build --wait
```

After every healthcheck turns green, the load balancer is on `http://localhost:${LB_PORT:-8080}`.

```bash
curl -s http://localhost:8080/api/v1/healthz
curl -s http://localhost:8080/api/v1/readyz
curl -s 'http://localhost:8080/api/v1/items?limit=5'
```

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/` | Service banner (`name`, `instance`, `docs`) |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/openapi.json` | OpenAPI schema |
| `GET` | `/api/v1/healthz` | Liveness probe, returns the serving instance id |
| `GET` | `/api/v1/readyz` | Readiness probe (executes `SELECT 1`) |
| `GET` | `/api/v1/items?limit=N&offset=M` | Paginated list |
| `POST` | `/api/v1/items` | Create |
| `GET` | `/api/v1/items/{id}` | Read one |
| `PATCH` | `/api/v1/items/{id}` | Partial update |
| `DELETE` | `/api/v1/items/{id}` | Delete |

Every response carries an `X-Served-By` header naming the upstream replica that handled the request.

## Observe load balancing

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -D - http://localhost:8080/api/v1/healthz |
    grep -i '^x-served-by'
done | sort | uniq -c
```

Sample run from this repo:

```
4 x-served-by: api1
4 x-served-by: api2
4 x-served-by: api3
```

## Failover

```bash
docker compose stop api2
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/api/v1/healthz
done
docker compose start api2
```

All ten responses are `200`. `proxy_next_upstream` retries on a healthy replica before the client sees any error. After `fail_timeout` elapses, distribution rebalances.

## Persistence

PostgreSQL data lives in the named Docker volume `lb_postgres_data`.

```bash
docker volume ls | grep lb_postgres_data
docker compose down       # data preserved
docker compose down -v    # data erased
```

## Migrations

The `migrate` service runs `alembic upgrade head` once and exits. The API replicas wait on `condition: service_completed_successfully` before they start.

```bash
docker compose run --rm migrate alembic revision --autogenerate -m "your change"
docker compose run --rm migrate alembic upgrade head
```

## CI/CD

`.github/workflows/ci.yml`:

1. `lint` — `ruff format --check`, `ruff check`, `mypy`.
2. `test` — `alembic upgrade head` and `pytest` against a real PostgreSQL service container with coverage upload.
3. `compose-smoke` — builds and waits on the whole stack, probes the LB end-to-end.
4. `build-and-push` (on `main` only) — Buildx push to `ghcr.io/<repo>` with `provenance: true` and `sbom: true`.

`.github/workflows/security.yml`:

- `gitleaks` — full-history secret scan.
- `trivy-fs`, `trivy-image` — CVE scans uploaded as SARIF into the GitHub Security tab.
- `hadolint` — Dockerfile lint (`failure-threshold: warning`).

## Hostside development without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export $(grep -v '^#' .env | xargs)
alembic upgrade head
uvicorn app.main:app --reload
```

## Tests

```bash
pip install -e ".[dev]"
pytest -q --cov=app
```

Tests run against a real PostgreSQL instance. Start one locally with `docker run --rm -p 5432:5432 -e POSTGRES_PASSWORD=postgres-test-pwd -e POSTGRES_DB=appdb_test postgres:16.4-alpine`, or rely on the service container in CI.

## Secrets

- `.env` is never committed: `.gitignore` covers `.env*` and explicitly re-includes `.env.example`.
- `POSTGRES_PASSWORD` is bound to `pydantic.SecretStr` and validated to be at least eight characters at startup.
- Use Docker secrets or an external KMS in production. A plaintext `.env` is fine for local and CI only.

## Project layout

| Path | Purpose |
|---|---|
| `app/` | FastAPI application (config, db, models, schemas, crud, api) |
| `alembic/` | Async Alembic environment and initial migration |
| `tests/` | pytest-asyncio + httpx ASGI transport |
| `nginx/nginx.conf` | Reverse proxy with rate limit and security headers |
| `docker-compose.yml` | postgres + migrate + api1/2/3 + nginx + named volumes |
| `Dockerfile` | Multi-stage, non-root, tini as PID 1 |
| `.github/workflows/` | `ci.yml` and `security.yml` |
| `pyproject.toml` | Pinned dependencies and ruff/mypy/pytest configuration |
| `.env.example` | Public template |
| `.gitignore` | Real `.env` ignored, `.env.example` tracked |
