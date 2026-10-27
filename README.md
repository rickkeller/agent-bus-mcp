# Agent Bus MCP

A small, local, authenticated [MCP](https://modelcontextprotocol.io) server that lets one AI agent hand **bounded tasks** and **quick advice-only questions** to other agents. Everything goes through a durable on-disk queue. A local producer creates the work, and a worker agent connects over MCP to claim it and answer it.

> **Status: early (0.1.0).** This is the local durable core. It doesn't do network federation, service discovery or replicated storage. Don't treat it as a production service, and don't point a live worker at it until you've reviewed [SECURITY.md](SECURITY.md).

Agent Bus MCP is one logical bus for many configured agent principals behind authenticated adapters. Its local durable core stores policy-controlled, directed many-to-many pathways for quick advice and bounded jobs. A controller can give different jobs to multiple workers—virtual one-to-many assignments over shared state—without owning or monopolizing the transport.

## Requirements

- Python 3.11 or newer.
- Linux or macOS. It needs Unix `fcntl` locks.
- `uvicorn`, which is installed automatically as a dependency.
- An MCP client that can send a plain stateless `POST /mcp` JSON-RPC request with a bearer token. There's no SSE or session support (see [Protocol boundary](#protocol-boundary)).

## Install

```sh
git clone https://github.com/rickkeller/agent-bus-mcp.git
cd agent-bus-mcp
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

## Configuration

The server is configured only through environment variables. `.env.example` lists all six. Copy it to a private file outside the repo:

```sh
install -d -m 700 "$HOME/.config/agent-bus-mcp" "$HOME/.local/state/agent-bus-mcp"
cp .env.example "$HOME/.config/agent-bus-mcp/env"
chmod 600 "$HOME/.config/agent-bus-mcp/env"
```

| Variable | Required | Meaning |
| --- | --- | --- |
| `AGENT_BUS_STATE_DIR` | yes | Persistent state directory, for example `~/.local/state/agent-bus-mcp`. It is forced to mode 0700. |
| `AGENT_BUS_BEARER_SECRET` | yes | The bearer token clients must send. Generate a unique random value (for example `openssl rand -hex 32`), never reuse one, and rotate it if it leaks. |
| `AGENT_BUS_BIND_HOST` | no (default `loopback`) | `loopback` is the only accepted value. The server listens on 127.0.0.1. |
| `AGENT_BUS_PORT` | yes | Decimal TCP port, for example `8765`. |
| `AGENT_BUS_PRODUCER_ID` | yes | ID of the local agent that creates work. 1–64 characters from `[A-Za-z0-9_-]`. |
| `AGENT_BUS_WORKER_ID` | yes | ID of the agent that connects over MCP and does the work. Same rules. |

These six variables give the simple two-agent setup. To use a larger graph of agents, build a `RoutePolicy` in Python (see [Consultation API](#consultation-api)).

## Quickstart

1. Load the environment and start the server:

   ```sh
   set -a; . "$HOME/.config/agent-bus-mcp/env"; set +a
   agent-bus-mcp
   ```

2. In a second shell, with the same environment loaded, create a task as the producer. Tasks can only be created locally from Python. MCP clients can't create them:

   ```sh
   python - <<'PY'
   import os
   from pathlib import Path
   from agent_bus_mcp import DurableQueue
   q = DurableQueue(Path(os.environ["AGENT_BUS_STATE_DIR"]),
                    producer_id=os.environ["AGENT_BUS_PRODUCER_ID"],
                    worker_id=os.environ["AGENT_BUS_WORKER_ID"])
   print(q.enqueue(goal="Summarize the release notes",
                   references=["https://example.com/notes"],
                   idempotency_key="demo-1"))
   PY
   ```

3. Claim it as the worker over MCP:

   ```sh
   curl -s "http://127.0.0.1:$AGENT_BUS_PORT/mcp" \
     -H "Authorization: Bearer $AGENT_BUS_BEARER_SECRET" \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"claim_task","arguments":{}}}'
   ```

   The response includes `status: "claimed"`, a `task_id` and a `lease_id`. Finish the task with `complete_task` (`task_id`, `lease_id`, `result`) or `fail_task`.

4. To connect a real MCP client, point it at the endpoint with the bearer header:

   ```json
   {"mcpServers":{"agent-bus":{"url":"http://127.0.0.1:8765/mcp","headers":{"Authorization":"Bearer REPLACE_WITH_YOUR_SECRET"}}}}
   ```

   For remote access, put an authenticated TLS reverse proxy in front of the loopback port. Never expose the port directly.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `required environment is missing` | One of `AGENT_BUS_STATE_DIR`, `AGENT_BUS_BEARER_SECRET`, `AGENT_BUS_PORT`, `AGENT_BUS_PRODUCER_ID` or `AGENT_BUS_WORKER_ID` is unset. Did you run `set -a; . file; set +a` in this shell? |
| `required environment is invalid` | `AGENT_BUS_PORT` is not a decimal number. |
| `only loopback binding is supported` | Set `AGENT_BUS_BIND_HOST=loopback`, or leave it unset. |
| `install an ASGI server to run the bridge` | `uvicorn` isn't installed in this environment. Re-run `pip install -e .`. |
| HTTP 401 with an empty body | The `Authorization: Bearer …` header is missing or wrong, or the request wasn't a `POST` to exactly `/mcp`. |
| `claim_task` returns `{"status": "empty"}` | No task is waiting for this worker. Check that the producer used the same state dir and IDs. |
| `QueueRefused` from Python | The input broke a limit: an ID or route not in the policy, a non-HTTPS reference, more than 4 references, or text that is too long or has control characters. |
| My MCP client can't connect | The server is stateless POST-only JSON-RPC, with no SSE and no sessions. Some clients need Streamable HTTP with SSE. |

## How it works

### Two ways to use it

- **Quick question:** use an edge whose policy allows `consultation` to ask for one short piece of advice, such as “Which heading is clearer?” The question expires, its addressed adviser may answer it once, and nothing is carried out automatically.
- **Formal task:** use an edge whose policy allows `task` to hand off a bounded job with a goal and optional public web references. Its addressed worker claims it, then marks it completed or failed.

For example, an agent preparing a weekly update can ask, “Should the risks be a table or a short list?” The adviser can answer, “Use a short list for three items.” If someone then wants the report changed, the operator explicitly creates a normal task. The answer itself cannot make the change.

A quick question has no structured fields for file or web references, payloads, execution scope, attachments, a caller-selected source or return route, task completion, or delivery authority. The local producer selects only a policy-allowed destination. Do not put commands or credentials in free text. An answer cannot create or finish a task. Remote clients cannot create questions or tasks through this server; creation stays on a principal-bound local producer object.

### How the boundary works

Agent Bus MCP is a small, vendor-neutral, MIT-licensed Python package. `RoutePolicy` declares configured agents and a closed set of directional edges; every edge separately allows formal tasks, advice-only consultations, or both. Principal identities are exact built-in strings of 1–64 ASCII letters, digits, `_`, or `-`; non-ASCII identities are refused rather than normalized. Each `DurableQueue` API object is bound to one configured principal, so callers cannot supply a source or worker identity in enqueue, claim, read, answer, or finish calls. The exact graph is persisted with the private state root.

Task and consultation records use separate directories. Idempotency is namespaced by mode, source, and destination. A single process lock, atomic JSON replacement, file and directory `fsync`, expiring leases, and lease fencing make state transitions durable and predictable.

The MCP worker surface exposes exactly five tools:

- `claim_task`, `complete_task`, and `fail_task` for formal work.
- `claim_question` and `answer_question` for advice-only consultations.

Each bearer-authenticated `MCPApplication` wraps one principal-bound queue object. MCP calls cannot choose or override source, worker, agent, or destination identities. No tool fetches URLs, opens files, runs a shell command, routes to another worker, or creates a consultation or task.

### Consultation API

The local producer asks and reads questions through `DurableQueue`:

```python
from pathlib import Path
from agent_bus_mcp import DurableQueue, RoutePolicy

policy = RoutePolicy(
    agents={"controller", "worker_a", "worker_b", "advisor"},
    routes={
        ("controller", "worker_a"): {"task"},
        ("controller", "worker_b"): {"task"},
        ("controller", "advisor"): {"consultation"},
    },
)
controller = DurableQueue(
    Path("state/agent-bus"), policy=policy, agent_id="controller"
)
question = controller.ask_question(
    destination_id="advisor",
    question="Would a table be clearer here?",
    origin_ref="weekly-update-17",
    idempotency_key="weekly-update-17-layout",
    expires_in_seconds=3600,
)
answer = controller.read_answer(question["consultation_id"])
```

A consultation binds its generated ID, policy-derived producer and worker IDs, caller-provided origin reference, idempotency key, question, creation/claim/answer timestamps, expiry, status, lease, and terminal answer. Retrying `ask_question` with the same mode/source/destination/idempotency key returns the original record. Questions and answers are each limited to 2048 UTF-8 bytes; origin references are limited to 256 bytes; expiry must be 1–86400 seconds. Text containing disallowed control characters is refused.

For a simple or existing two-agent deployment, pass both identities explicitly. This constructs a one-edge compatibility policy that allows both modes:

```python
queue = DurableQueue(
    Path("state/agent-bus"),
    producer_id="example_producer",
    worker_id="example_worker",
)
```

When a parent-format state root has records but no `policy.json`, the first graph-bound open admits it only if every stored task edge allows `task` and every stored consultation edge allows `consultation`. Any absent edge or mode refuses the whole open before policy creation or record mutation.

`claim_question` returns `status=empty` or a claimed question with its `consultation_id`, `origin_ref`, `question`, consultation expiry, lease ID, and lease expiry. A lease lasts at most 900 seconds and never extends beyond the question expiry. An expired lease may be reclaimed with a new fence while the question remains live. `answer_question` requires the exact live lease and one bounded answer. Wrong identities or leases, expired questions or leases, and every second answer are refused without revealing details.

### Formal task API

A bound local producer calls `enqueue` with a policy-allowed `destination_id`, a bounded goal, zero to four syntactically public-HTTPS references, and an idempotency key. When its source has exactly one task destination, `destination_id` may be omitted for compatibility. The addressed MCP worker calls `claim_task`, then either `complete_task` with a result of at most 2048 bytes or `fail_task` without a result.

`claim_task` returns `status=empty` or a task with a 900-second lease. Expired task leases may be reclaimed with a new fence. Terminal task calls refuse stale or mismatched leases; an exact retry returns `already_completed` or `already_failed`.

### Protocol boundary

The authenticated endpoint is stateless `POST /mcp` JSON-RPC. It accepts one request per POST and rejects batches. Notifications without an `id` receive HTTP 202 with an empty body. It supports `initialize`, `ping`, `tools/list`, and calls to the five tools above.

This is intentionally not full Streamable HTTP: there are no sessions, SSE streams, resumability, server-initiated messages, or heartbeats. One MCP application serves one bound worker principal; multiple policy agents do not turn this adapter into a federated or multi-host transport. Confirm that the selected MCP client can use this stateless POST-only contract.

Every tool response includes both ordinary MCP text content and the same object as structured content:

```json
{"result":{"content":[{"type":"text","text":"{\"status\":\"empty\"}"}],"structuredContent":{"status":"empty"}}}
```

### Optional recurring-task example

The checked-in public-research manifest is an example formal-task producer, not the product. It remains explicitly bound to the producer and worker IDs supplied on its command line. Its reference uses the reserved `.invalid` domain and is never fetched by this package. The included user-service examples load `%h/.config/agent-bus-mcp/env` and preserve state at `%h/.local/state/agent-bus-mcp`.

```sh
agent-bus-public-research --cadence weekly --state-dir "$HOME/.local/state/agent-bus-mcp" --producer-id example_producer --worker-id example_worker
```

Review and adapt the example service files before enabling them in any environment.

## Development

```sh
python3 -m pytest -q
python3 -m compileall -q src tests
git diff --check
```

See [AGENTS.md](AGENTS.md) for the repo layout and rules.

## What this is not

It is not a general job platform, a remote creation API, a network-fetching agent, a multi-host federation layer, a service-discovery system, replicated storage, consensus, a full Streamable HTTP server, a result-trust boundary, or a substitute for an operator’s TLS, authentication, authorization, backup, and incident-response decisions. See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
