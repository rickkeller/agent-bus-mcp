# AGENTS.md

This package is a local durable bus. A worker may claim, answer, complete, or fail. A graph-bound member may post and read a board, and the author may promote one post into one handoff. The endpoint that promoted that post may read the stored result. Every other endpoint is refused. An answer does not become a task. A board post does not become a task.

Do not add secrets, host paths, chat ids, or tokens. `.env.example` holds only `REPLACE_WITH_…` placeholders.

## Layout

```
src/agent_bus_mcp/
  __init__.py            exports DurableQueue, QueueRefused, RoutePolicy
  queue.py               RoutePolicy and the principal-bound DurableQueue
  mcp.py                 MCPApplication and the `agent-bus-mcp` entry point
  remote.py              TLS host: one state directory, one enrolled certificate per endpoint
  enroll.py              local enrollment and revocation, not a remote tool
  remote_call.py         TLS client for that host
  endpoint.py            `agent-bus-endpoint`, the local sender for one configured endpoint
  endpoints.py           allowlist loader for chat and handoff
  hermes_adapter.py      optional local process that may start one operator argv
  recurring.py           `agent-bus-public-research`, an example recurring task producer
  research_manifest.json example manifest (uses a .invalid domain)
tests/                   pytest suite
systemd/                 example user units. Review them before use and never enable them from here
docs/REQUIREMENTS.md     requirements and specification
docs/ENDPOINTS.md        how to connect installed endpoints
docs/REMOTE.md           TLS host
docs/HERMES.md           optional process adapter
.env.example             environment placeholders
SECURITY.md              threat model and operator responsibilities
```

## Run and test

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[test]'
python3 -m pytest -q
python3 -m compileall -q src tests
```

For a manual run, use a throwaway state directory such as `$(mktemp -d)`, a random bearer secret, and a free port.

## Conventions

- Keep the boundary closed. Don't add MCP tools that create questions, chats, pointers, or ordinary tasks, and don't add tools that fetch URLs, open files, run commands, route to another worker, or call an external webhook. A graph-bound member may post, read, and poll a board. Polling does not acknowledge. `ack_board` names the exact posts. Only the author may promote that post, which creates one handoff to one other member. Remote callers never choose source, worker, author, or endpoint IDs.
- Every state change goes through the process lock, atomic JSON replacement, and fsync. Keep lease fencing intact.
- Refusals don't reveal details. Raise `QueueRefused` or return a generic error.
- `uvicorn` is the only runtime dependency.
- Every behaviour change needs a test.
