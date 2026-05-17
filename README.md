# Load Balancing with Nginx and Docker

A containerized load balancing setup using Nginx as a reverse proxy distributing traffic across three backend servers via Docker Compose.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2 (the `docker compose` command, not the old `docker-compose`)

## Quick Start

```bash
docker compose up -d
```

## Verify

```bash
docker compose ps
```

All 4 containers should show status "running":
- `web1`, `web2`, `web3` - backend servers
- `nginx-lb` - the load balancer / reverse proxy

## Testing Round-Robin

Run the curl command multiple times and observe how responses rotate between the three backends:

```bash
curl http://localhost:8080
curl http://localhost:8080
curl http://localhost:8080
```

Each request returns a different page - Server 1, Server 2, Server 3 - in sequential order because Nginx uses round-robin distribution by default.

## Checking Response Headers

```bash
curl -I http://localhost:8080
```

Look for the `X-Upstream-Server` header in the response. It shows the IP address and port of the backend container that handled the request (e.g., `172.20.0.2:80`). This is the internal Docker network address, not the service name.

## Failover Test

Stop one of the backend servers and verify that traffic is rerouted to the remaining healthy ones:

```bash
docker compose stop web2
```

Now run curl multiple times - only Server 1 and Server 3 will respond:

```bash
curl http://localhost:8080
curl http://localhost:8080
curl http://localhost:8080
```

Bring the server back:

```bash
docker compose start web2
```

Run curl again - all three servers respond in rotation:

```bash
curl http://localhost:8080
curl http://localhost:8080
curl http://localhost:8080
```

## Load Balancer Health Check

```bash
curl http://localhost:8080/health
```

Returns `OK` if the load balancer itself is running.

## Stop Everything

```bash
docker compose down
```

## Architecture

Client requests hit the Nginx reverse proxy on port 8080 (configurable via `.env`). Nginx distributes these requests across three backend Nginx containers (`web1`, `web2`, `web3`) using round-robin load balancing. All containers run on an internal Docker bridge network (`lb-network`). Backend servers are not exposed to the host - they are only accessible through the proxy.

## How It Works

Docker Compose creates an isolated bridge network where all four containers can communicate. Containers resolve each other by service name (e.g., `web1`, `web2`, `web3`) through Docker's built-in DNS server - no IP addresses need to be hardcoded.

Nginx is configured with an `upstream` block that lists all three backends. When a request arrives, Nginx forwards it to the next server in the list using the default round-robin algorithm. Each backend serves a distinct HTML page so you can visually confirm which server handled each request.

If a backend fails, the `proxy_next_upstream` directive tells Nginx to retry the request on the next healthy server instead of returning an error to the client. The `max_fails` and `fail_timeout` parameters mark a backend as unavailable after 3 consecutive failures, skipping it for 10 seconds before trying again.
