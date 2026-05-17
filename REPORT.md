# Project Report: Load Balancing with Nginx and Docker

**Repository:** https://github.com/daminow/load-balancing-nginx-docker
**Course:** System and Network Administration (SNA), Innopolis University
**Author:** Timur Daminov

---

## I. Goal / Tasks of the Project

### Goal
Build a working reverse-proxy + load-balancing setup that distributes HTTP traffic across multiple backend servers, demonstrates round-robin scheduling, and survives the failure of any single backend without dropping client requests.

### Concrete tasks
1. Containerize three independent backend web servers, each serving a uniquely identifiable page.
2. Put an Nginx reverse proxy in front of them with round-robin upstream selection.
3. Use an isolated Docker bridge network so backends are reachable **only** through the proxy.
4. Implement automatic failover (`proxy_next_upstream`, `max_fails`, `fail_timeout`).
5. Expose a `/health` endpoint on the load balancer.
6. Make the public port configurable through `.env`.
7. Document and verify the behavior (round-robin, failover, recovery) reproducibly.

### Team responsibilities
This is a solo project — all roles below were performed by the author:

| Role | Responsibility |
|---|---|
| Infrastructure / DevOps | `docker-compose.yml`, network design, `.env` parameterization |
| Backend / Configuration | `nginx.conf` upstream, headers, failover policy, health check |
| Frontend (static) | Three distinct `index.html` pages for visual round-robin verification |
| QA / Documentation | `README.md`, `validate.sh`, this report |

---

## II. Execution plan / Methodology

### Planned infrastructure

```
                    ┌───────────────────────────┐
                    │         Client            │
                    │  (curl / browser)         │
                    └─────────────┬─────────────┘
                                  │
                          host:${LB_PORT:-8080}
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │  nginx-lb (reverse proxy)     │
                  │  - upstream backend { ... }   │
                  │  - round-robin                │
                  │  - max_fails=3 / 10s          │
                  │  - X-Upstream-Server header   │
                  │  - /health → 200 OK           │
                  └────────┬─────────┬────────────┘
                           │         │
        ┌──────────────────┼─────────┼──────────────────┐
        │                  │         │                  │
        ▼                  ▼         ▼                  ▼
   ┌─────────┐        ┌─────────┐ ┌─────────┐     (any future
   │  web1   │        │  web2   │ │  web3   │      backend N)
   │ :80     │        │ :80     │ │ :80     │
   └─────────┘        └─────────┘ └─────────┘
        │                  │         │
        └──────────────────┴─────────┴─────────────────────┐
                                                           │
              Docker bridge network: lb-network            │
                  (DNS by service name, internal-only) ◄───┘
```

### Step-by-step plan
1. **Compose topology** — 4 services on one user-defined bridge network. Backends have **no** published ports; only `nginx-lb` exposes `${LB_PORT:-8080}:80`.
2. **Round-robin** — Nginx default algorithm; no extra directives required.
3. **Failover** — `proxy_next_upstream error timeout http_502 http_503` retries on the next backend transparently; `max_fails=3 fail_timeout=10s` marks a dead backend and skips it for 10 s.
4. **Observability** — `add_header X-Upstream-Server $upstream_addr always` so clients can see which backend served each response.
5. **Health check** — separate `location /health` returning `OK` with logging off.
6. **Reproducibility** — `validate.sh` shell script automates start, round-robin check, failover check, recovery check, and teardown.

### Key design choices
- **`nginx:alpine`** for all four containers — small image (~50 MB), single tool to learn, identical config surface across LB and backends.
- **Read-only volume mounts** (`:ro`) on `nginx.conf` and HTML pages — prevents the container from mutating host files.
- **`restart: unless-stopped`** — services come back after Docker daemon restart, but a deliberate `docker compose stop` stays stopped (needed for the failover demo).
- **No hardcoded IPs** — service names (`web1`, `web2`, `web3`) resolved via Docker's embedded DNS.

---

## III. Development of the solution / Tests as the PoC

### Repository layout
```
load-balancing-project/
├── docker-compose.yml          # 4 services, 1 bridge network
├── nginx/nginx.conf            # upstream + proxy + health
├── web1/index.html             # blue "Server 1" page
├── web2/index.html             # different color "Server 2"
├── web3/index.html             # different color "Server 3"
├── .env                        # LB_PORT=8080
├── README.md                   # quick start + tests
└── REPORT.md                   # this document
```

### Key source files (links to repository)

- [`docker-compose.yml`](./docker-compose.yml) — 4 services + `lb-network` bridge.
- [`nginx/nginx.conf`](./nginx/nginx.conf) — upstream block, round-robin, failover, health.
- [`web1/index.html`](./web1/index.html), [`web2/index.html`](./web2/index.html), [`web3/index.html`](./web3/index.html) — backend pages.
- [`validate.sh`](./validate.sh) — automated test script.

### Proof-of-concept tests

#### Test 1 — Bring stack up
```bash
docker compose up -d
docker compose ps
```
**Expected:** all 4 containers in state `running` (`web1`, `web2`, `web3`, `nginx-lb`).

#### Test 2 — Round-robin distribution
```bash
for i in 1 2 3 4 5 6; do
  curl -s http://localhost:8080 | grep -oE 'Server [0-9]'
done
```
**Expected output:**
```
Server 1
Server 2
Server 3
Server 1
Server 2
Server 3
```

#### Test 3 — Upstream visibility header
```bash
curl -sI http://localhost:8080 | grep X-Upstream-Server
```
**Expected:** `X-Upstream-Server: 172.20.0.X:80` (the internal IP rotates).

#### Test 4 — Failover (kill one backend)
```bash
docker compose stop web2
for i in 1 2 3 4 5 6; do
  curl -s http://localhost:8080 | grep -oE 'Server [0-9]'
done
```
**Expected:** only `Server 1` and `Server 3` appear. No connection errors leak to the client because `proxy_next_upstream` retries inside the proxy.

#### Test 5 — Recovery
```bash
docker compose start web2
sleep 11   # wait > fail_timeout
for i in 1 2 3 4 5 6; do
  curl -s http://localhost:8080 | grep -oE 'Server [0-9]'
done
```
**Expected:** rotation restored to `Server 1 / Server 2 / Server 3`.

#### Test 6 — Load-balancer health endpoint
```bash
curl -s http://localhost:8080/health
```
**Expected:** `OK`.

#### Test 7 — Backends are NOT publicly reachable
```bash
curl -sv http://localhost:80    2>&1 | grep -i 'refused\|failed'
# any port other than ${LB_PORT} should fail to connect
```
**Expected:** connection refused — confirms backends are only reachable through `lb-network`.

All seven tests pass on a clean machine with Docker 20.10+ and Compose v2.

---

## IV. Difficulties faced, new skills acquired

### Difficulties
1. **`docker-compose` vs `docker compose`** — the old Python-based `docker-compose` is deprecated; v2 ships as a Docker CLI plugin (`docker compose`). Documentation in the wild still mixes the two; the project README pins v2 explicitly to avoid the confusion.
2. **Service-name DNS vs. hardcoded IPs** — initial drafts used `127.0.0.1` / `localhost` in `upstream`, which obviously doesn't work across containers. Switching to `web1:80`, `web2:80`, `web3:80` worked once the user-defined bridge network was in place (the default `bridge` network does **not** provide DNS by service name).
3. **`proxy_next_upstream` defaults** — by default Nginx only retries on `error` and `timeout`, not on HTTP 5xx. Adding `http_502 http_503` was needed for proper failover when a container is alive but Nginx inside it returns an error.
4. **`fail_timeout` window** — a stopped backend isn't immediately marked dead; clients can hit the dead server once before `max_fails` trips. Acceptable for this PoC, but worth knowing for production tuning.
5. **Read-only mounts** — first runs failed silently when Nginx tried to rewrite mounted config; `:ro` made the issue explicit and forced correct paths.

### Skills acquired
- Writing a multi-service Docker Compose file with a user-defined bridge network and `.env` parameterization.
- Nginx `upstream` + `proxy_pass` + `proxy_next_upstream` + `max_fails` / `fail_timeout` configuration.
- Using `add_header X-Upstream-Server $upstream_addr` for cheap upstream-level observability.
- Distinguishing internal DNS (service names) from host networking and understanding why backends should not have published ports.
- Writing a reproducible validation shell script (`validate.sh`) that exercises happy path, failure path, and recovery path end-to-end.

---

## V. Conclusion and judgment

The project delivers a working, reproducible load-balancing setup in **~35 lines of Nginx config** and **~40 lines of Docker Compose**, with no application code on the backends — just static pages used as visible "ground truth" for which container served the request. The seven PoC tests confirm round-robin distribution, transparent failover, and proper isolation of backends.

**What this PoC is good for**
- Learning the mental model: separation of concerns between the proxy, the backends, and the network.
- Demonstrating that horizontal scalability + fault tolerance can be added to an existing service **without changing the service itself** — only by changing the network edge.

**What it intentionally is NOT**
- It is not production-ready. There is no TLS, no rate limiting, no real health-checking (commercial Nginx Plus has active health checks; OSS Nginx only does passive checks via `max_fails`), no metrics export, no centralized logging, no autoscaling.
- The "failover" here is reactive (after 3 failures), not proactive. A proper production setup would add a probe (Consul / Kubernetes liveness / a custom checker) that pulls dead backends out of rotation before any client request fails.

**Most valuable lesson**
Almost every "load balancer" problem in practice is actually a **DNS, network, or health-check** problem in disguise. The Nginx directives themselves are short and stable; the time goes into understanding how containers find each other, how failures propagate, and how the proxy decides "dead enough to skip". Building this PoC made those three questions concrete.

**Natural next steps** (out of scope here)
- Add TLS termination at the LB with `certbot`/Let's Encrypt.
- Swap round-robin for `least_conn` or IP-hash and compare under uneven load with `wrk` or `k6`.
- Replace static backends with a real app (Node/Flask) and measure tail latency under partial failure.
- Add Prometheus + Grafana with `nginx-prometheus-exporter` for visible upstream metrics.

---

## Appendix A — Full `nginx.conf`

```nginx
events {
    worker_connections 1024;
}

http {
    upstream backend {
        server web1:80 max_fails=3 fail_timeout=10s;
        server web2:80 max_fails=3 fail_timeout=10s;
        server web3:80 max_fails=3 fail_timeout=10s;
    }

    server {
        listen 80;
        server_name localhost;

        location / {
            proxy_pass http://backend;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

            proxy_next_upstream error timeout http_502 http_503;

            add_header X-Upstream-Server $upstream_addr always;
        }

        location /health {
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }
    }
}
```

## Appendix B — Full `docker-compose.yml`

```yaml
services:
  web1:
    image: nginx:alpine
    container_name: web1
    volumes:
      - ./web1:/usr/share/nginx/html:ro
    networks:
      - lb-network
    restart: unless-stopped

  web2:
    image: nginx:alpine
    container_name: web2
    volumes:
      - ./web2:/usr/share/nginx/html:ro
    networks:
      - lb-network
    restart: unless-stopped

  web3:
    image: nginx:alpine
    container_name: web3
    volumes:
      - ./web3:/usr/share/nginx/html:ro
    networks:
      - lb-network
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    container_name: nginx-lb
    ports:
      - "${LB_PORT:-8080}:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - web1
      - web2
      - web3
    networks:
      - lb-network
    restart: unless-stopped

networks:
  lb-network:
    driver: bridge
```

## Appendix C — One-shot validation transcript

```bash
$ docker compose up -d
[+] Running 5/5
 ✔ Network load-balancing-project_lb-network  Created
 ✔ Container web1                              Started
 ✔ Container web2                              Started
 ✔ Container web3                              Started
 ✔ Container nginx-lb                          Started

$ for i in 1 2 3; do curl -s http://localhost:8080 | grep -oE 'Server [0-9]'; done
Server 1
Server 2
Server 3

$ docker compose stop web2
$ for i in 1 2 3; do curl -s http://localhost:8080 | grep -oE 'Server [0-9]'; done
Server 1
Server 3
Server 1

$ docker compose start web2
$ sleep 11
$ for i in 1 2 3; do curl -s http://localhost:8080 | grep -oE 'Server [0-9]'; done
Server 1
Server 2
Server 3

$ curl -s http://localhost:8080/health
OK

$ docker compose down
```

## Appendix D — Useful links

- Repository: https://github.com/daminow/load-balancing-nginx-docker
- Nginx `upstream` reference: https://nginx.org/en/docs/http/ngx_http_upstream_module.html
- Nginx `proxy_next_upstream` reference: https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_next_upstream
- Docker Compose networking: https://docs.docker.com/compose/networking/
