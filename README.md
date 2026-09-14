# Agent Bus MCP

Agent Bus MCP is a public, vendor-neutral, MIT-licensed durable task queue with a minimal MCP bridge. It is intended for an operator who wants a small, inspectable handoff between a local producer and one configured MCP worker route.

It exposes exactly three remote tools: `claim_task`, `complete_task`, and `fail_task`.

## Capability boundary

It can durably accept locally produced bounded tasks, let the configured worker claim one task, and let that worker complete or fail its exact live lease. It uses atomic filesystem JSON records, a process lock, file and directory `fsync`, durable idempotency keys, 900-second leases with fencing, and immutable terminal `completed`/`failed` states. Producer and worker identities use a closed format and are fixed at startup; an MCP request cannot select either identity.

It cannot enqueue remotely; expose fail reasons; create sessions, SSE, heartbeats, or multi-worker routing; fetch a network URL; access a filesystem or shell through an MCP tool; integrate with a vendor; or publish packages/releases. References receive syntactic public-HTTPS validation only: HTTPS plus a hostname is not proof that a source is safe, public, accurate, or appropriate to fetch.

## Protocol boundary

The endpoint is authenticated, stateless `POST /mcp` JSON-RPC. It accepts one request per POST and rejects batches. Notifications without an `id` receive HTTP 202 with an empty body. It supports `initialize`, `ping`, `tools/list`, and calls to the three tools above. This is **not** full Streamable-HTTP compatibility: there are no sessions, SSE streams, resumability, or server-initiated messages. Confirm that the selected MCP client can use this stateless POST-only contract before deploying it.

Every tool response has this envelope; `content[0].text` is a JSON string encoding the same object as `structuredContent`:

```json
{"result":{"content":[{"type":"text","text":"{\"status\":\"empty\"}"}],"structuredContent":{"status":"empty"}}}
```

`claim_task` returns either `{"status":"empty"}` or `{"status":"claimed","task_id":"...","goal":"...","references":["..."],"lease_id":"...","lease_expires_at":"..."}`. Its lease is 900 seconds. `complete_task` requires `task_id`, `lease_id`, and a `result` of at most 2048 bytes; it returns `{"status":"completed","task_id":"..."}` or `{"status":"already_completed","task_id":"..."}`. `fail_task` requires only `task_id` and `lease_id`, takes no result, and returns `failed` or `already_failed` in the same shape. Both terminal tools refuse stale or mismatched leases.

## Install and configure

Requires Python 3.11+ and Unix `fcntl`; running the bridge also requires `uvicorn`.

```sh
git clone https://github.com/rickkeller/agent-bus-mcp
cd agent-bus-mcp
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
# Alternative, install directly from the public repository:
python -m pip install 'git+https://github.com/rickkeller/agent-bus-mcp.git'
```

Create a private environment file (mode 0600 is appropriate). Tokens never belong in git.

```sh
mkdir -p /home/operator/.config/agent-bus-mcp /home/operator/.local/state/agent-bus-mcp
chmod 700 /home/operator/.local/state/agent-bus-mcp
cp .env.example /home/operator/.config/agent-bus-mcp/env
chmod 600 /home/operator/.config/agent-bus-mcp/env
```

`/home/operator` is deliberately fake; replace it with the operator's actual home directory. Edit the private file and replace every value:

```dotenv
AGENT_BUS_STATE_DIR=/home/operator/.local/state/agent-bus-mcp
AGENT_BUS_BEARER_SECRET=EXAMPLE_NOT_A_REAL_TOKEN_REPLACE_ME
AGENT_BUS_BIND_HOST=loopback
AGENT_BUS_PORT=8765
AGENT_BUS_PRODUCER_ID=example_producer
AGENT_BUS_WORKER_ID=example_worker
```

`AGENT_BUS_STATE_DIR` must be the persistent 0700 directory. `AGENT_BUS_BEARER_SECRET` must be a unique high-entropy token kept only in the private file and rotated after suspected exposure. `AGENT_BUS_BIND_HOST` must be `loopback`; `AGENT_BUS_PORT` is a decimal port. The two identities must each be 1--64 characters of `[A-Za-z0-9_-]` and must match between bridge and producer. The shown token and identities are intentionally fake.

Run locally:

```sh
set -a; . /home/operator/.config/agent-bus-mcp/env; set +a
agent-bus-mcp
```

The service listens only on loopback. For remote use, the operator must place an authenticated TLS-terminating reverse proxy in front of it and decide who may reach it. Do not expose the loopback service directly or treat syntactic HTTPS reference validation as a network safety control.

Generic remote MCP configuration:

```json
{"mcpServers":{"agent-bus":{"url":"https://mcp.example.invalid/mcp","headers":{"Authorization":"Bearer EXAMPLE_NOT_A_REAL_TOKEN_REPLACE_ME"}}}}
```

The URL and token above are placeholders; use a client that supports the stateless compatibility limit described above.

## Operator smoke test

With the private env loaded and bridge running, these requests demonstrate the contract:

```sh
curl -sS -X POST http://localhost:8765/mcp -H 'Authorization: Bearer EXAMPLE_NOT_A_REAL_TOKEN_REPLACE_ME' -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
curl -sS -X POST http://localhost:8765/mcp -H 'Authorization: Bearer EXAMPLE_NOT_A_REAL_TOKEN_REPLACE_ME' -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
curl -sS -X POST http://localhost:8765/mcp -H 'Authorization: Bearer EXAMPLE_NOT_A_REAL_TOKEN_REPLACE_ME' -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"claim_task","arguments":{}}}'
```

After a claimed response, keep its `task_id` and `lease_id`, finish within 900 seconds, and submit either `complete_task` with a bounded result or `fail_task` without one. A lease that expires or does not match is refused; do not retry it as a new lease.

## Local example producer and systemd

The checked-in public-research manifest is only an example workload, not the product. Its sole reference uses a reserved `.invalid` URL and it never fetches that URL. The timer and service run it weekly, matching the manifest:

```sh
agent-bus-public-research --cadence weekly --state-dir /home/operator/.local/state/agent-bus-mcp --producer-id example_producer --worker-id example_worker
mkdir -p /home/operator/.config/systemd/user
cp systemd/* /home/operator/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-bus-mcp.service agent-bus-public-research.timer
```

The shipped user services load `%h/.config/agent-bus-mcp/env`, preserve state at `%h/.local/state/agent-bus-mcp`, and permit writes only there. Create that directory with 0700 before enabling them; state must survive reboot.

## Development

```sh
python3 -m pytest -q
python3 -m compileall -q src tests
systemd-analyze --user verify systemd/*.service systemd/*.timer
git diff --check
```

## What this is not

It is not a general job platform, a network agent, a remote enqueue API, a full Streamable-HTTP server, a task-result trust boundary, or a substitute for an operator's TLS, authorization, backup, and incident-response decisions. See [SECURITY.md](SECURITY.md).
