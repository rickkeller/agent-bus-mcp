# Agent Bus MCP

A local durable bus where configured endpoints can ask short questions and hand off bounded work. Package version 0.3.0 in this tree is this publication. The previous public release remains `v0.2.0`.

The requirements and the technical specification are `docs/REQUIREMENTS.md`. Read `MIGRATION.md` before upgrading an existing install. Review [SECURITY.md](SECURITY.md) before connecting a worker.

A small, local, authenticated [MCP](https://modelcontextprotocol.io) server that lets one AI agent hand **bounded tasks** and **quick advice-only questions** to other agents. Everything goes through a durable on-disk queue. A local producer creates the work, and a worker agent connects over MCP to claim it and answer it.

> **Status: 0.3.0.** This is the local durable core, plus one optional NATS request/reply ask. It doesn't do network federation, service discovery or replicated storage. Don't treat it as a production service, and don't point a live worker at it until you've reviewed [SECURITY.md](SECURITY.md).

## Connect two endpoints

The names are yours. Copy `examples/two-endpoints.json`, replace `north` and `south`, and share one state directory on this machine. That shared directory is one computer. A second computer calls the TLS host in `docs/REMOTE.md` and does not mount the directory. Only the author can promote a post, and only to one other member of that board.

```sh
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint post-board --board room --text "south owns the fix" --key talk
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint promote-board --board room --post POST_ID --key own
```

A graph-bound MCP server can post and read that board as its own endpoint, and the author can promote. Use the same state directory and config. Set `AGENT_BUS_ENDPOINT_ID` to the member, and leave `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID` unset. A post does not create a task. The claim of the promoted handoff includes `board` and `post_id`. The server cannot post as a different endpoint, and the simple pair has no board. The commands and the refusal rules are in `docs/ENDPOINTS.md`.

## Connect jig and grokbot

This is one machine. Both programs share one state directory. If grokbot is on another computer, this package cannot connect them.

You do not start the MCP server for this chat. That server cannot create a chat or a pointer.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
install -d -m 700 "$HOME/.local/state/agent-bus-mcp" "$HOME/.config/agent-bus-mcp"
cp examples/jig-grokbot.json "$HOME/.config/agent-bus-mcp/endpoints.json"
chmod 600 "$HOME/.config/agent-bus-mcp/endpoints.json"
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
AGENT_BUS_ENDPOINT_ID=grokbot agent-bus-endpoint post-chat --to jig --text "what version are you on" --key once
AGENT_BUS_ENDPOINT_ID=jig agent-bus-endpoint claim-chat
```

Copy `consultation_id` and `lease_id` from the claim line, then:

```sh
AGENT_BUS_ENDPOINT_ID=jig agent-bus-endpoint answer-chat --id CONSULTATION_ID --lease LEASE_ID --text "0.1.0"
AGENT_BUS_ENDPOINT_ID=grokbot agent-bus-endpoint read-chat CONSULTATION_ID
```

A pair that is not in the file is refused. Jig can file a claimed chat as an ask when its data directory has `bus.json` with `state_dir`, `config`, and `endpoint_id` set to `jig`. Jig does not reply. grokbot has no client in this package, so a person runs the command above for it.

## Worker door

The MCP server is a later door. It can claim and answer. On a graph it can also post, read, and promote a board it belongs to. It cannot create a chat, a pointer, or a task except by that author promotion. The environment variables and the Python snippet below are for that door, not for connecting jig to grokbot.

Agent Bus MCP is one logical bus for many configured agent principals behind authenticated adapters. Its local durable core stores policy-controlled, directed many-to-many pathways for quick advice and bounded jobs. A controller can give different jobs to multiple workers—virtual one-to-many assignments over shared state—without owning or monopolizing the transport.

## Requirements

- Python 3.11 or newer. On Debian and Ubuntu, install the `python3-venv` package before `python3 -m venv`. The base `python3` package does not include it, and the command fails with `ensurepip is not available`.
- Linux or macOS. It needs Unix `fcntl` locks.
- `uvicorn`, which is installed automatically as a dependency.
- An MCP client that can send a plain stateless `POST /mcp` JSON-RPC request with a bearer token. There's no SSE or session support (see [Protocol boundary](#protocol-boundary)).

## Install

```sh
git clone https://github.com/rickkeller/agent-bus-mcp.git agent-bus-mcp
cd agent-bus-mcp
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

## Configuration

The server is configured only through environment variables. `.env.example` lists the simple pair. Copy it to a private file outside the repo:

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
| `AGENT_BUS_PRODUCER_ID` | simple pair | ID of the local agent that creates work. 1–64 characters from `[A-Za-z0-9_-]`. Omit this in graph mode. |
| `AGENT_BUS_WORKER_ID` | simple pair | ID of the agent that connects over MCP and does the work. Same rules. Omit this in graph mode. |
| `AGENT_BUS_CONFIG` | graph mode | Path of the endpoints allowlist. Set this with `AGENT_BUS_ENDPOINT_ID`, and leave the simple pair unset. |
| `AGENT_BUS_ENDPOINT_ID` | graph mode | Endpoint id this MCP process claims and answers as. Same character rules. |

The simple pair is one producer and one worker. A graph server binds to one id in the allowlist instead. To connect more than those two, use [docs/ENDPOINTS.md](docs/ENDPOINTS.md). That file is the allowlist. You do not have to build the policy in Python.

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
| `required environment is missing` | A required variable for the selected mode is unset. The simple pair needs `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`. Graph mode needs `AGENT_BUS_CONFIG` and `AGENT_BUS_ENDPOINT_ID`. State, bearer, and port are always required. |
| `required environment is invalid` | `AGENT_BUS_PORT` is not a decimal number, or the simple pair and graph mode are both set. |
| `only loopback binding is supported` | Set `AGENT_BUS_BIND_HOST=loopback`, or leave it unset. |
| `install an ASGI server to run the bridge` | `uvicorn` isn't installed in this environment. Re-run `pip install -e .`. |
| HTTP 401 with an empty body | The `Authorization: Bearer …` header is missing or wrong, or the request wasn't a `POST` to exactly `/mcp`. |
| `claim_task` returns `{"status": "empty"}` | No task is waiting for this worker. Check that the producer used the same state dir and IDs. |
| `QueueRefused` from Python | The input broke a limit: an ID or route not in the policy, a non-HTTPS reference, more than 4 references, or text that is too long or has control characters. |
| My MCP client can't connect | The server is stateless POST-only JSON-RPC, with no SSE and no sessions. Some clients need Streamable HTTP with SSE. |

## How it works

### Two ways to use it

- **Quick question:** use an edge whose policy allows `consultation` to ask for one short piece of advice, such as “Which heading is clearer?” The question expires, its addressed adviser may answer it once, and nothing is carried out automatically.
- **Formal task:** use an edge whose policy allows `task` to hand off a bounded job with a goal and optional public web references. Its addressed worker claims it, then marks it completed or failed. A coding request is this formal task, created only by the local producer through `submit_coding_request`. It is not an advice question, and it is not an MCP tool.

For example, an agent preparing a weekly update can ask, “Should the risks be a table or a short list?” The adviser can answer, “Use a short list for three items.” If someone then wants the report changed, the operator explicitly creates a normal task. The answer itself cannot make the change.

A quick question has no structured fields for file or web references, payloads, execution scope, attachments, a caller-selected source or return route, task completion, or delivery authority. The local producer selects only a policy-allowed destination. Do not put commands or credentials in free text. An answer cannot create or finish a task. Remote clients cannot create questions or ordinary tasks through this server. A graph-bound server can post on a member board. That post is not work. Only `promote_board`, called as the post's author, creates the one handoff.

### How the boundary works

Agent Bus MCP is a small, vendor-neutral, MIT-licensed Python package. `RoutePolicy` declares configured agents and a closed set of directional edges; every edge separately allows formal tasks, advice-only consultations, or both. Principal identities are exact built-in strings of 1–64 ASCII letters, digits, `_`, or `-`; non-ASCII identities are refused rather than normalized. Each `DurableQueue` API object is bound to one configured principal, so callers cannot supply a source or worker identity in enqueue, claim, read, answer, or finish calls. The exact graph is persisted with the private state root.

Task and consultation records use separate directories. Idempotency is namespaced by mode, source, and destination. A single process lock, atomic JSON replacement, file and directory `fsync`, expiring leases, and lease fencing make state transitions durable and predictable.

The MCP worker surface exposes twelve tools:

- `claim_task`, `complete_task`, `fail_task`, and `read_result` for formal work. `read_result` returns one promoted task's status and stored result only to the endpoint that promoted the post which created that task. Every other endpoint is refused. A task that was not promoted from a board is refused. The result text is not part of `wait_available`.
- `claim_question` and `answer_question` for advice-only consultations.
- `post_board`, `read_board`, `poll_unread`, `ack_board`, and `promote_board` for a graph-bound member. A board post is at most 2048 bytes. Reading and polling do not acknowledge. Own posts are not unread. `ack_board` names the posts that stop being unread. Each page has at most 20 posts. The simple pair refuses these five.
- `ask` sends one question on subject `ask.<work>` over NATS request/reply and waits for one answer. It needs `AGENT_BUS_NATS_URL`. It does not create a board post, a task, or a consultation. A spaced work name is refused. Nobody listening returns `no_responders` at once.

Each bearer-authenticated `MCPApplication` wraps one principal-bound queue object. MCP calls cannot choose or override source, worker, agent, or author identities. No tool fetches URLs, opens files, runs a shell command, or routes to another worker. A consultation or an ordinary task is still created only by the local process. `promote_board` is the one MCP call that creates a task, and only from a post that bound endpoint authored.

### Connect installed endpoints

An installed program does not have to use one fixed product name. Copy `examples/endpoints.json`, keep the names you want, and delete the rest. `chat` is one expiring message. `handoff` is one public HTTPS pointer. The bus does not call a model, fetch the pointer, or carry file bytes.

Each endpoint is a local process. Set `AGENT_BUS_STATE_DIR` to one shared directory, `AGENT_BUS_CONFIG` to that file, and `AGENT_BUS_ENDPOINT_ID` to this process. A message cannot name a different sender.

```sh
AGENT_BUS_ENDPOINT_ID=grokbot agent-bus-endpoint post-chat --to jig --text "what version are you on" --key once
AGENT_BUS_ENDPOINT_ID=jig agent-bus-endpoint claim-chat
AGENT_BUS_ENDPOINT_ID=jig agent-bus-endpoint answer-chat --id CONSULTATION_ID --lease LEASE_ID --text "0.1.0"
AGENT_BUS_ENDPOINT_ID=grokbot agent-bus-endpoint read-chat CONSULTATION_ID
```

A pair that is not in the file is refused. Dots to OpenClaw, OpenClaw to Hermes, and Hermes to OpenClaw are the same commands with different ids. The MCP server still cannot create a chat or a pointer. Those stay on this local command.

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

The authenticated endpoint is stateless `POST /mcp` JSON-RPC. It accepts one request per POST and rejects batches. Notifications without an `id` receive HTTP 202 with an empty body. A valid bearer on `GET /mcp` receives HTTP 405 with `Allow: POST`. A missing or wrong bearer is still HTTP 401. It supports `initialize`, `ping`, `tools/list`, and calls to the twelve tools above.

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
