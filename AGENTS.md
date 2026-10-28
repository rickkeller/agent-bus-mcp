# Agent Bus MCP

This repository is the local durable core. It is not the fabric proof. Do not deploy this revision. Do not point a live worker at it. Do not enable a timer from this repository.

A worker may claim, answer, complete, or fail. It does not create a task. An answer does not become a task. Do not add secrets, host paths, chat ids, or tokens.

## Layout

```
src/agent_bus_mcp/
  __init__.py            exports DurableQueue, QueueRefused, RoutePolicy
  queue.py               RoutePolicy and the principal-bound DurableQueue (tasks, consultations, leases, idempotency)
  mcp.py                 MCPApplication (ASGI, bearer auth, POST /mcp JSON-RPC, five tools) and the `agent-bus-mcp` entry point
  endpoint.py            `agent-bus-endpoint`, the local sender for one configured endpoint
  endpoints.py           allowlist loader for chat and handoff
  recurring.py           `agent-bus-public-research`, an example recurring task producer
  research_manifest.json example manifest for recurring.py (uses a .invalid domain)
tests/                   pytest suite
systemd/                 example user units. Review them before use and never enable them from here
docs/REQUIREMENTS.md     requirements
.env.example             the six AGENT_BUS_* variables, with placeholders only
SECURITY.md              threat model and operator responsibilities
```

## Run and test

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[test]'
python3 -m pytest -q
python3 -m compileall -q src tests
git diff --check
```

For a manual run, use a throwaway state directory such as `$(mktemp -d)`, a random bearer secret and a free port. Never use the real state directory.

## Conventions

- Keep the boundary closed. Don't add MCP tools that create tasks or questions, fetch URLs, open files, run commands or route to another worker. Remote callers never choose source, worker or destination IDs.
- Every state change goes through the process lock, atomic JSON replacement and fsync. Keep lease fencing intact.
- Keep the limits (byte caps, expiry ranges, the four-reference cap, the ID character set) in step with README.md. Change both together.
- Refusals don't reveal details. Raise `QueueRefused` or return a generic error.
- `uvicorn` is the only runtime dependency. Don't add more without a strong reason.
- Every behaviour change needs a test.

## Secrets: never commit them

- Never commit a real `AGENT_BUS_BEARER_SECRET`, a filled-in env file, state directories, `.env`, keys or certificates.
- `.env.example` holds only `REPLACE_WITH_…` placeholders. Keep it that way.
- Don't put real hostnames, home paths, chat ids or agent IDs from a live deployment in code, tests, docs or commit messages.
