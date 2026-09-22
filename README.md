# Theta

Theta is a realtime AI assistant with a browser chat UI. It consists of three Python services:

- `operations` — public API, authentication, chat history, WebSocket, and frontend
- `agents` — agent execution and streamed Responses-API output
- `mcp` — MCP tools and skill resources

Only `operations` should be exposed to users. `agents` and `mcp` communicate over the private Compose network.

## Run with Docker Compose

Requirements: Docker Compose v2 and an OpenAI-compatible Responses WebSocket endpoint at `/v1/responses`.

```bash
cp .env.example .env
# edit .env and set LLM_API_KEY
docker compose up --build
```

Open <http://127.0.0.1>. Operations listens on port 80 and serves the UI, API, WebSocket chat, and `/mcp/` endpoint from the same ASGI application. SQLite is stored in the named volume `operations-data`.

```bash
docker compose ps
docker compose logs -f operations agents
docker compose down                 # preserve the database
docker compose down -v              # also delete the database
```

The LLM URL must include `ws://` or `wss://`, for example `ws://host.docker.internal:4000/v1`.

## Local development

```bash
python3.14 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Start AgentsExecution and Operations in separate terminals. Operations also serves the MCP endpoint:

```bash
cd AgentsExecution && MCP_SERVER_URL=http://127.0.0.1:8090/mcp/ uvicorn main:app --reload --port 6000
cd Operations && PYTHONPATH=.. uvicorn main:app --reload --port 8090
```

## AWS deployment without a registry

Use an EC2 Linux instance with Docker Engine, Compose v2, Git, and a TLS certificate or load balancer for HTTPS. Clone the repository once:

```bash
sudo mkdir -p /opt/theta
sudo chown "$USER":"$USER" /opt/theta
git clone <repository-url> /opt/theta
cd /opt/theta
cp .env.example .env
# edit .env and set LLM_API_KEY
```

Build locally on the instance. Worker counts are configured in `.env`:

```bash
docker compose up -d --build --remove-orphans
docker compose ps
curl -fsS http://127.0.0.1/health
```

No image registry is required. The default configuration starts 4 Uvicorn worker processes for Agents and 1 for Operations. Each worker has a maximum of 16 concurrent requests and recycles after 1,000 requests. Four Agents workers with a pool of four LLM links each gives up to 16 LLM WebSocket links total.

Operations currently uses process-local active chat state. A reconnect or follow-up request can land in a different worker and lose the in-memory stream. Before increasing `OPERATIONS_WORKERS`, move run coordination, event buffering, and MCP session state to shared services such as Redis, and use PostgreSQL for shared persistence. The request and 1,000-request recycle limits still apply independently to every worker.

The public entrypoint is port 80. Operations serves `/mcp/` directly through the mounted FastMCP ASGI app. Operations defaults to one worker because FastMCP Streamable HTTP sessions are process-local; externalize MCP session state before increasing `OPERATIONS_WORKERS`. Do not expose ports 5000, 6000, or 8090 publicly. Put HTTPS in front of Operations for production and back up the `operations-data` volume.

## Health checks

- Operations: `GET /health` (also checks Agents)
- Agents: `GET /health` (reports loaded tools and skills)
- MCP: mounted at `/mcp/` inside Operations
