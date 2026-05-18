# Project Report — Automated Deployment of a Multi-Service Web Application with CI/CD

| | |
|---|---|
| Course | System and Network Administration (S25), Innopolis University |
| Team | Timur Daminov, Mikail Khamkhoev, Almir Avkhadiev, Anton Bugaev |
| Approved topic | Automated Deployment of a Multi-Service Web Application with CI/CD |
| Stack | Docker, Docker Compose, Nginx, FastAPI, PostgreSQL, GitHub Actions |
| Source | `load-balancing-project/` in the team repository |

---

## I. Goal and tasks

### Goal

Deliver a self-contained deployment that covers every item of the approved scope: a multi-service web application (FastAPI + PostgreSQL), Nginx as a reverse proxy balancing several application replicas, persistent storage on a Docker named volume, configuration kept out of git, and a GitHub Actions pipeline that builds, tests and ships the container image.

### Tasks

1. Write a FastAPI service on Python 3.12 that talks to PostgreSQL over async SQLAlchemy 2.0 with `asyncpg`. Expose a representative CRUD surface on `/api/v1/items`, plus a liveness probe and a readiness probe that issues a real database round-trip.
2. Package the service in a multi-stage `Dockerfile`. Run as a non-root user, drop all Linux capabilities, mark the root filesystem read-only at runtime, use `tini` as PID 1.
3. Compose the stack with Docker Compose: PostgreSQL, three identical API replicas, a one-shot Alembic migration container, and an Nginx reverse proxy. Order startup with healthchecks and `condition: service_completed_successfully`.
4. Use a Docker named volume (`lb_postgres_data`) for PostgreSQL data. Keep all configuration in `.env`, commit only the `.env.example` template, gitignore the real `.env`.
5. Configure Nginx as a real reverse proxy: `least_conn` upstream with `keepalive`, passive health checks, `proxy_next_upstream` for transparent failover, rate limiting, security response headers, JSON access logs.
6. Build a GitHub Actions pipeline that lints, type-checks, runs `pytest` against a real PostgreSQL service container, runs a Compose smoke build, and pushes a multi-arch image to GHCR with provenance and SBOM attestations. Add a separate workflow for gitleaks, Trivy (filesystem + image) and hadolint.
7. Demonstrate the running balancer and its failover behaviour from outside the cluster using `curl`.

### Division of responsibilities

The work was split into four owner areas. Every patch went through a peer review by at least one other member before it landed on the mainline branch.

| Member | Owner area | Primary artefacts |
|---|---|---|
| **Timur Daminov** | Application code and database layer | [`app/main.py`](./app/main.py), [`app/core/config.py`](./app/core/config.py), [`app/core/logging.py`](./app/core/logging.py), [`app/db/`](./app/db/), [`app/models/item.py`](./app/models/item.py), [`app/schemas/item.py`](./app/schemas/item.py), [`app/crud/item.py`](./app/crud/item.py), [`app/api/`](./app/api/), [`alembic/`](./alembic/), [`alembic.ini`](./alembic.ini), [`pyproject.toml`](./pyproject.toml) |
| **Mikail Khamkhoev** | Image build and runtime hardening | [`Dockerfile`](./Dockerfile), [`.dockerignore`](./.dockerignore), choice of non-root UID/GID, `cap_drop` / `read_only` / `tmpfs` setup, `HEALTHCHECK` definition, `tini` integration, triage of Trivy image-scan findings |
| **Almir Avkhadiev** | Reverse proxy, networking and Compose orchestration | [`nginx/nginx.conf`](./nginx/nginx.conf), [`docker-compose.yml`](./docker-compose.yml), upstream design (`least_conn`, keepalive, passive checks, `proxy_next_upstream`), named volume layout, request limits, JSON access log format, the `lb-network` bridge |
| **Anton Bugaev** | CI/CD, testing and quality gates | [`.github/workflows/ci.yml`](./.github/workflows/ci.yml), [`.github/workflows/security.yml`](./.github/workflows/security.yml), [`tests/conftest.py`](./tests/conftest.py), [`tests/test_health.py`](./tests/test_health.py), [`tests/test_items.py`](./tests/test_items.py), ruff/mypy/pytest configuration inside `pyproject.toml`, hadolint / gitleaks / Trivy integration, GHCR push with provenance and SBOM, structural `validate.sh` checker |

---

## II. Execution plan and methodology

### System overview

External traffic enters the deployment on the host port exposed by the `nginx` container. Nginx terminates the connection, picks one of three API replicas using `least_conn` over an HTTP-keepalive pool, and forwards the request over the private `lb-network` bridge. Each replica handles the request inside an async event loop and reaches PostgreSQL through the `asyncpg` connection pool. A separate `migrate` container runs Alembic exactly once at startup; the API replicas wait for it to exit successfully before they begin accepting traffic. Only `nginx` publishes a port to the host; PostgreSQL and the API replicas are internal-only.

| Service | Image | Role | Listens on | Talks to |
|---|---|---|---|---|
| `nginx` | `nginx:1.27-alpine` | Reverse proxy + load balancer | `${LB_PORT:-8080}/tcp` on the host, `80/tcp` inside the network | `api1`, `api2`, `api3` on `8000/tcp` |
| `api1`, `api2`, `api3` | local image built from `Dockerfile` | FastAPI replicas (Uvicorn `--workers 2`) | `8000/tcp` on `lb-network` | `postgres` on `5432/tcp` |
| `postgres` | `postgres:16.4-alpine` | Database, holds all state in a named volume | `5432/tcp` on `lb-network` | — |
| `migrate` | same image as the API | One-shot Alembic upgrade; exits with status 0 on success | — | `postgres` on `5432/tcp` |

Networking and persistence rules:

- All four backend services are attached to a single user-defined bridge network called `lb-network`. No other ports are published to the host.
- Database state lives in the named volume `lb_postgres_data`. The volume is created and owned by Docker, so its lifecycle is decoupled from any individual container.
- The API replicas mount only a 64 MB `tmpfs` at `/tmp`; everything else in the root filesystem is read-only at runtime.

### Implementation plan

We split the work into six stages that mirror the layered structure of the stack.

1. **Application code.** FastAPI 0.128 with the documented `lifespan` context manager, async SQLAlchemy 2.0 with `asyncpg`, Pydantic v2 + `pydantic-settings` for typed configuration, `structlog` for JSON logging, `tenacity` for a bounded startup retry against the database. A small middleware adds an `X-Served-By` header carrying the replica id, which makes load balancing visible to any external caller.
2. **Schema management.** Alembic with an async `env.py` that builds an `AsyncEngine` and uses `connection.run_sync(do_run_migrations)` for the DDL pass. Migrations are not run inside the API process — they are a separate one-shot service that runs `alembic upgrade head` and exits. API replicas wait on `condition: service_completed_successfully`.
3. **Reverse proxy and load balancing.** Nginx in front of three identical API replicas. The upstream uses `least_conn`, `keepalive 32`, passive health checks (`max_fails=3 fail_timeout=10s`) and `proxy_next_upstream error timeout http_502 http_503 http_504` with `proxy_next_upstream_tries 3` so a failing replica never propagates to the client. The same Nginx instance enforces `limit_req_zone` rate limiting and adds hardening response headers.
4. **Container hardening.** Multi-stage build. The `builder` stage installs into `/opt/venv`; the runtime stage copies the venv only, runs as a fixed non-root UID/GID (10001), drops every Linux capability, marks the root filesystem read-only, mounts a small `tmpfs` for `/tmp`, and uses `tini` as PID 1 so signals propagate correctly.
5. **Secrets and configuration.** `.env` is the only source of truth at compose time and is gitignored. The previously committed `.env` from the course starter was removed with `git rm --cached .env`. The public template is `.env.example`. `POSTGRES_PASSWORD` is typed as `pydantic.SecretStr` and validated to be at least eight characters long when the application starts.
6. **Automation.** Four sequential jobs in `ci.yml`: `lint` → `test` → `compose-smoke` → `build-and-push`. The `test` job uses a real PostgreSQL service container, so the same async SQLAlchemy code that runs in production is exercised in CI. The `compose-smoke` job builds the full stack and waits for healthchecks before probing the LB. The push step uses Buildx and tags the image in GHCR with `provenance: true` and `sbom: true`. A separate `security.yml` workflow runs gitleaks, Trivy (filesystem and image) and hadolint on every push and weekly on cron.

### Design choices

- **Async SQLAlchemy 2.0 with `asyncpg`.** Matches the FastAPI event loop end-to-end and avoids thread-pool stalls under concurrent load.
- **Alembic in a dedicated job.** Migrations are applied exactly once per release, not on every replica start. This removes a race condition we hit during the first iteration.
- **`least_conn` upstream.** Under uneven load (slow query, GC pause), `least_conn` avoids piling more work onto an already busy replica. Round-robin does not have this property.
- **`pool_pre_ping=True`.** A short ping is sent on connection checkout, so a PostgreSQL restart does not leave a generation of broken connections in the pool.
- **`X-Served-By` middleware.** Gives an external caller a one-byte answer to "which replica served this request". The same header is the basis for the failover and balancing tests in section III.
- **Trivy + gitleaks + hadolint.** Three small tools that cover three different concerns (known CVEs, leaked secrets, Dockerfile anti-patterns), so each one stays small and fast.

---

## III. Development and proof-of-concept tests

### Repository layout

| Path | Content |
|---|---|
| [`app/main.py`](./app/main.py) | FastAPI application factory; lifespan that builds the engine, runs the bounded DB retry, and tears the engine down; CORS, JSON error handler, `X-Served-By` middleware |
| [`app/core/config.py`](./app/core/config.py) | Pydantic settings (`Settings`, `get_settings`); `SecretStr` password; computed `database_url` |
| [`app/core/logging.py`](./app/core/logging.py) | `structlog` JSON logging setup, replica id added to every event |
| [`app/db/base.py`](./app/db/base.py), [`app/db/session.py`](./app/db/session.py) | `DeclarativeBase` with `created_at` / `updated_at`; async engine and `async_sessionmaker` factories |
| [`app/models/item.py`](./app/models/item.py) | `Item` ORM model with `CheckConstraint`s on `name` length and `quantity` sign |
| [`app/schemas/item.py`](./app/schemas/item.py) | Pydantic v2 schemas: `ItemCreate`, `ItemUpdate`, `ItemRead`, `ItemPage` |
| [`app/crud/item.py`](./app/crud/item.py) | Async CRUD functions; pagination helper using `func.count` + `LIMIT/OFFSET` |
| [`app/api/deps.py`](./app/api/deps.py) | `SessionDep`, `SettingsDep` reusable dependencies |
| [`app/api/v1/health.py`](./app/api/v1/health.py), [`app/api/v1/items.py`](./app/api/v1/items.py) | Liveness, readiness, and CRUD endpoints |
| [`alembic/env.py`](./alembic/env.py), [`alembic/versions/0001_initial_items_table.py`](./alembic/versions/0001_initial_items_table.py) | Async Alembic environment and the initial migration |
| [`tests/`](./tests/) | `conftest.py` with engine and ASGI client fixtures; `test_health.py`, `test_items.py` |
| [`nginx/nginx.conf`](./nginx/nginx.conf) | Reverse proxy configuration: upstream, rate limit, headers, JSON access log |
| [`docker-compose.yml`](./docker-compose.yml) | Six-service compose file with the named volume and per-service hardening |
| [`Dockerfile`](./Dockerfile) | Multi-stage build, non-root runtime, `tini`, `HEALTHCHECK` |
| [`.github/workflows/ci.yml`](./.github/workflows/ci.yml), [`.github/workflows/security.yml`](./.github/workflows/security.yml) | Pipelines |
| [`.env.example`](./.env.example), [`.gitignore`](./.gitignore) | Public configuration template; `.env*` ignored, `.env.example` re-included |

### Test scenarios

Every scenario below was run against this repository on a clean machine (macOS host, Docker Engine 27.4, Compose v2). Measured output is reproduced where it is useful.

**1. Bring the stack up.**

```bash
cp .env.example .env
docker compose up -d --build --wait --wait-timeout 240
```

Observed terminal state:

```
Container lb-postgres   Healthy
Container lb-migrate    Exited (0)
Container lb-api1       Healthy
Container lb-api2       Healthy
Container lb-api3       Healthy
Container lb-nginx      Healthy
```

**2. Liveness and readiness through the load balancer.**

```bash
curl -s http://localhost:8080/api/v1/healthz   # {"status":"ok","instance":"apiN"}
curl -s http://localhost:8080/api/v1/readyz    # {"status":"ready","instance":"apiN"}
```

`/readyz` runs `SELECT 1` against PostgreSQL inside the request, so a 200 from `/readyz` is also a check of the API-to-database path through the proxy.

**3. CRUD against the real database.**

```bash
curl -sX POST http://localhost:8080/api/v1/items \
  -H 'content-type: application/json' \
  -d '{"name":"smoke-1","quantity":3,"description":"compose smoke"}'

curl -s 'http://localhost:8080/api/v1/items?limit=10'
```

Returned `201 Created` followed by a paginated list including the new row.

**4. Load balancing distribution.**

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -D - http://localhost:8080/api/v1/healthz |
    grep -i '^x-served-by'
done | sort | uniq -c
```

Measured distribution over 12 requests:

```
4 x-served-by: api1
4 x-served-by: api2
4 x-served-by: api3
```

**5. Transparent failover.**

```bash
docker compose stop api2
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/api/v1/healthz
done
```

All twelve responses were `200`. After `docker compose start api2` and an interval longer than `fail_timeout`, the distribution returned to `4 / 4 / 4`.

**6. Persistence across `compose down`.**

```bash
curl -s 'http://localhost:8080/api/v1/items?limit=100' | jq '.total'   # 1
docker compose down                                                    # volume preserved
docker compose up -d --wait
curl -s 'http://localhost:8080/api/v1/items?limit=100' | jq '.total'   # still 1
```

Data survives because PostgreSQL state lives in the named volume `lb_postgres_data`. Only `docker compose down -v` would erase it.

**7. Automated test suite.**

```bash
pip install -e ".[dev]"
pytest -q --cov=app
```

```
9 passed
TOTAL  258 statements, 46 missed, 82% coverage
```

The tests use `pytest-asyncio` together with `httpx.ASGITransport`, and they run against a real PostgreSQL instance so the async path is the same one we use in production.

**8. CI pipeline ([`.github/workflows/ci.yml`](./.github/workflows/ci.yml)).**

- `lint` — `ruff format --check`, `ruff check`, `mypy app`.
- `test` — `alembic upgrade head` then `pytest --cov` against a PostgreSQL service container.
- `compose-smoke` — `docker compose up -d --build --wait`, external probes against the LB, then teardown.
- `build-and-push` (on `main` only) — Buildx push to `ghcr.io/<repo>` with `provenance: true` and `sbom: true`.

**9. Security pipeline ([`.github/workflows/security.yml`](./.github/workflows/security.yml)).**

- `gitleaks` — full-history secret scan.
- `trivy-fs` and `trivy-image` — CVE scans uploaded as SARIF, visible in the GitHub Security tab.
- `hadolint` — Dockerfile static analysis with `failure-threshold: warning`.

A structural check is available outside the project directory at `../validate.sh` (file-existence, gitignore status, `docker compose config`, `nginx -t`). It reported `Passed: 22, Failed: 0`.

---

## IV. Difficulties faced and skills acquired

### Issues we hit, and how we resolved them

1. **Async-aware Alembic environment.** Alembic's stock `env.py` template builds a synchronous engine, which is incompatible with our `postgresql+asyncpg://` URL. We rewrote `env.py` to build an `AsyncEngine`, open it with `async with connectable.connect()`, and call `connection.run_sync(do_run_migrations)` for the DDL pass.
2. **ASGI lifespan and `httpx.ASGITransport`.** `ASGITransport` does not trigger ASGI lifespan events. Our `tests/conftest.py` therefore builds the engine and session factory directly and assigns them to `app.state.session_factory` before any request, which is the same state that `lifespan` would have produced in production. This kept the test setup deterministic.
3. **Startup race against PostgreSQL.** Without explicit ordering, the API replicas finished starting before PostgreSQL accepted connections and exited with a connection error. The fix was a combination of `depends_on: condition: service_healthy` on the database, `depends_on: condition: service_completed_successfully` on the `migrate` service, and a bounded `tenacity` retry inside `lifespan` so the application itself can wait for the first successful `SELECT 1`.
4. **Named volume versus bind mount.** The original course starter used bind mounts for static HTML pages, which was a poor fit for a database (host UID coupling, host-side cleanup behaviour). We switched PostgreSQL to a Docker named volume (`lb_postgres_data`) so its lifecycle stays inside Docker and survives `docker compose down`.
5. **Read-only root filesystem and Python.** Setting `read_only: true` on the API replicas initially broke Uvicorn because of small writes to `/tmp`. Mounting a 64 MB `tmpfs` only at `/tmp` and keeping the rest of the filesystem read-only fixed it without giving up the safety property.
6. **Single-request blind spot in passive Nginx health checks.** Until `max_fails` trips, one request can still hit a failed backend. Adding `http_502 http_503 http_504` to `proxy_next_upstream` and setting `proxy_next_upstream_tries 3` masks that single retry from the client.
7. **`.env` already in git.** The starter committed `.env`. We removed it with `git rm --cached .env`, replaced it with `.env.example`, tightened `.gitignore` to cover `.env*` while re-including `.env.example`, and added `gitleaks` to the security workflow so this cannot regress silently.

### Skills acquired

- Building an async-first FastAPI service with `lifespan`, `pydantic-settings`, async SQLAlchemy 2.0 and `asyncpg`.
- Writing Alembic migrations against an async engine and gating application startup on a one-shot migration container.
- Hardening a container image: multi-stage build, non-root user, `cap_drop`, `read_only`, `no-new-privileges`, `tmpfs`, `tini` as PID 1.
- Operating Nginx as a real reverse proxy: keepalive upstream pools, `least_conn`, passive health checks, `proxy_next_upstream`, `limit_req_zone`, security response headers, JSON access logs.
- Wiring up a GitHub Actions DAG with `needs:` and `concurrency:`, a PostgreSQL service container, Buildx with the GHA cache backend, GHCR pushes with SBOM and provenance attestations, and SARIF upload into the Security tab.
- Practical secret hygiene: gitignored `.env`, public `.env.example`, `SecretStr` in settings, gitleaks in CI.

---

## V. Conclusion

### Summary

The project delivers what the approved topic asks for. A FastAPI service backed by PostgreSQL runs as three independent replicas behind an Nginx reverse proxy. Database state is on a Docker named volume. Configuration is in environment variables that are not in git. A GitHub Actions pipeline lints, tests, smoke-builds, scans and ships the image. Balancing and failover are visible from outside the cluster with a `curl` loop reading `X-Served-By`.

### Out of scope

- **TLS termination.** The demo serves plaintext HTTP on the LB port. A production deployment would terminate TLS at the proxy (Nginx with ACME, or Traefik/Caddy in front).
- **AuthN/AuthZ on the CRUD endpoints.** They are open by design — adding OAuth 2.0 or JWT at the edge is the natural next step but was not part of the topic.
- **High availability for PostgreSQL.** One instance with a named volume is enough for the assignment; real durability under failure needs replication, point-in-time backups and either a managed service or a Patroni cluster.
- **Application metrics.** Logging is JSON to stdout; Prometheus, `nginx-prometheus-exporter` and a few application histograms would be the obvious next addition.

### Lessons learned

The technical surface of "deploying a small service" is small, but most of the work is in the spaces between the boxes: ordering startup correctly so the API does not race the database, making sure a failing replica does not surface as an error to the client, and making the result observable from outside without a debugger. The Nginx and Docker directives that achieve this are short; the time goes into picking the right ones and proving they work.

---

## Appendix A — `nginx/nginx.conf` highlights

See [`nginx/nginx.conf`](./nginx/nginx.conf). Notable directives:

- `least_conn` upstream with `keepalive 32`.
- Passive health checks: `max_fails=3 fail_timeout=10s` per backend.
- `proxy_next_upstream error timeout http_502 http_503 http_504` with `proxy_next_upstream_tries 3`.
- `limit_req_zone $binary_remote_addr zone=api_rl:10m rate=20r/s` plus `limit_conn api_cc 50`.
- Hardening headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`.
- JSON `log_format` with `upstream_addr`, `upstream_status`, `request_time`, `upstream_response_time`.

## Appendix B — `docker-compose.yml` highlights

See [`docker-compose.yml`](./docker-compose.yml). Notable elements:

- Services: `postgres`, `migrate`, `api1`, `api2`, `api3`, `nginx`.
- Top-level **named volume** `lb_postgres_data` for PostgreSQL durability.
- `depends_on: condition: service_healthy` for `postgres` and the API replicas; `condition: service_completed_successfully` for `migrate`.
- API replicas use `read_only: true`, `cap_drop: [ALL]`, `security_opt: no-new-privileges:true`, `tmpfs: /tmp`.
- Per-service CPU and memory limits under `deploy.resources.limits`.

## Appendix C — `Dockerfile` highlights

See [`Dockerfile`](./Dockerfile). Notable elements:

- Base `python:3.12.7-slim-bookworm`, multi-stage.
- Builder installs into `/opt/venv`; runtime copies the venv plus the application code.
- Non-root `app` user (UID/GID 10001), `tini` as PID 1.
- `HEALTHCHECK` curls `/api/v1/healthz`.
- Runtime command: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips * --no-server-header`.

## Appendix D — GitHub Actions

See [`.github/workflows/ci.yml`](./.github/workflows/ci.yml) and [`.github/workflows/security.yml`](./.github/workflows/security.yml).

## Appendix E — References

- FastAPI lifespan: <https://fastapi.tiangolo.com/advanced/events/>
- SQLAlchemy 2.0 async ORM: <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html>
- Nginx upstream module: <https://nginx.org/en/docs/http/ngx_http_upstream_module.html>
- Nginx `proxy_next_upstream`: <https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_next_upstream>
- Docker Compose healthchecks: <https://docs.docker.com/compose/compose-file/05-services/#healthcheck>
- GitHub Actions service containers: <https://docs.github.com/actions/using-containerized-services/about-service-containers>
- Trivy action: <https://github.com/aquasecurity/trivy-action>
- gitleaks action: <https://github.com/gitleaks/gitleaks-action>
