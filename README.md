# Agent Bus MCP

Agent Bus MCP is one logical bus for many configured agent principals behind authenticated adapters. Its local durable core stores policy-controlled, directed many-to-many pathways for quick advice and bounded jobs. A controller can give different jobs to multiple workers—virtual one-to-many assignments over shared state—without owning or monopolizing the transport.

This slice is the local durable core and state model. It does not implement multi-host network federation, service discovery, replicated storage, or consensus. The explicit two-agent, one-edge mode remains available as a compatibility path and a simple deployment.

## Two ways to use it

- **Quick question:** use an edge whose policy allows `consultation` to ask for one short piece of advice, such as “Which heading is clearer?” The question expires, its addressed adviser may answer it once, and nothing is carried out automatically.
- **Formal task:** use an edge whose policy allows `task` to hand off a bounded job with a goal and optional public web references. Its addressed worker claims it, then marks it completed or failed.

For example, an agent preparing a weekly update can ask, “Should the risks be a table or a short list?” The adviser can answer, “Use a short list for three items.” If someone then wants the report changed, the operator explicitly creates a normal task. The answer itself cannot make the change.

A quick question has no structured fields for file or web references, payloads, execution scope, attachments, a caller-selected source or return route, task completion, or delivery authority. The local producer selects only a policy-allowed destination. Do not put commands or credentials in free text. An answer cannot create or finish a task. Remote clients cannot create questions or tasks through this server; creation stays on a principal-bound local producer object.

## How the boundary works

Agent Bus MCP is a small, vendor-neutral, MIT-licensed Python package. `RoutePolicy` declares configured agents and a closed set of directional edges; every edge separately allows formal tasks, advice-only consultations, or both. Each `DurableQueue` API object is bound to one configured principal, so callers cannot supply a source or worker identity in enqueue, claim, read, answer, or finish calls. The exact graph is persisted with the private state root.

Task and consultation records use separate directories. Idempotency is namespaced by mode, source, and destination. A single process lock, atomic JSON replacement, file and directory `fsync`, expiring leases, and lease fencing make state transitions durable and predictable.

The MCP worker surface exposes exactly five tools:

- `claim_task`, `complete_task`, and `fail_task` for formal work.
- `claim_question` and `answer_question` for advice-only consultations.

Each bearer-authenticated `MCPApplication` wraps one principal-bound queue object. MCP calls cannot choose or override source, worker, agent, or destination identities. No tool fetches URLs, opens files, runs a shell command, routes to another worker, or creates a consultation or task.

## Consultation API

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

`claim_question` returns `status=empty` or a claimed question with its `consultation_id`, `origin_ref`, `question`, consultation expiry, lease ID, and lease expiry. A lease lasts at most 900 seconds and never extends beyond the question expiry. An expired lease may be reclaimed with a new fence while the question remains live. `answer_question` requires the exact live lease and one bounded answer. Wrong identities or leases, expired questions or leases, and every second answer are refused without revealing details.

## Formal task API

A bound local producer calls `enqueue` with a policy-allowed `destination_id`, a bounded goal, zero to four syntactically public-HTTPS references, and an idempotency key. When its source has exactly one task destination, `destination_id` may be omitted for compatibility. The addressed MCP worker calls `claim_task`, then either `complete_task` with a result of at most 2048 bytes or `fail_task` without a result.

`claim_task` returns `status=empty` or a task with a 900-second lease. Expired task leases may be reclaimed with a new fence. Terminal task calls refuse stale or mismatched leases; an exact retry returns `already_completed` or `already_failed`.

## Protocol boundary

The authenticated endpoint is stateless `POST /mcp` JSON-RPC. It accepts one request per POST and rejects batches. Notifications without an `id` receive HTTP 202 with an empty body. It supports `initialize`, `ping`, `tools/list`, and calls to the five tools above.

This is intentionally not full Streamable HTTP: there are no sessions, SSE streams, resumability, server-initiated messages, or heartbeats. One MCP application serves one bound worker principal; multiple policy agents do not turn this adapter into a federated or multi-host transport. Confirm that the selected MCP client can use this stateless POST-only contract.

Every tool response includes both ordinary MCP text content and the same object as structured content:

```json
{"result":{"content":[{"type":"text","text":"{\"status\":\"empty\"}"}],"structuredContent":{"status":"empty"}}}
```

## Install and configure

Requires Python 3.11+ and Unix `fcntl`; running the bridge also requires `uvicorn`.

```sh
git clone https://github.com/OWNER/agent-bus-mcp.git
cd agent-bus-mcp
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

`OWNER` is a placeholder for the repository owner. Create a private environment file and a persistent state directory; tokens never belong in git.

```sh
install -d -m 700 "$HOME/.config/agent-bus-mcp" "$HOME/.local/state/agent-bus-mcp"
cp .env.example "$HOME/.config/agent-bus-mcp/env"
chmod 600 "$HOME/.config/agent-bus-mcp/env"
```

Replace every placeholder in the private file:

```dotenv
AGENT_BUS_STATE_DIR=/path/to/private/persistent/state
AGENT_BUS_BEARER_SECRET=REPLACE_WITH_A_UNIQUE_RANDOM_VALUE
AGENT_BUS_BIND_HOST=loopback
AGENT_BUS_PORT=8765
AGENT_BUS_PRODUCER_ID=example_producer
AGENT_BUS_WORKER_ID=example_worker
```

The state root is forced to mode 0700. The bearer secret must be unique, high entropy, private, and rotated after suspected exposure. The bind setting must be `loopback`; the port must be decimal. Both IDs must be 1–64 characters from `[A-Za-z0-9_-]`. This environment-driven entry point is the simple two-agent compatibility deployment; applications that use a larger graph construct one `RoutePolicy` and bind each local adapter to its configured `agent_id`.

Run locally:

```sh
set -a; . "$HOME/.config/agent-bus-mcp/env"; set +a
agent-bus-mcp
```

For remote use, place an authenticated TLS-terminating reverse proxy in front of the loopback service and explicitly decide who may reach it. Do not expose the service directly. HTTPS reference validation is syntax checking, not a network safety or trust decision.

Generic remote MCP configuration:

```json
{"mcpServers":{"agent-bus":{"url":"https://mcp.example.invalid/mcp","headers":{"Authorization":"Bearer REPLACE_WITH_A_UNIQUE_RANDOM_VALUE"}}}}
```

## Optional recurring-task example

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

## What this is not

It is not a general job platform, a remote creation API, a network-fetching agent, a multi-host federation layer, a service-discovery system, replicated storage, consensus, a full Streamable HTTP server, a result-trust boundary, or a substitute for an operator’s TLS, authentication, authorization, backup, and incident-response decisions. See [SECURITY.md](SECURITY.md).
