# Agent Bus MCP

A local durable bus where configured endpoints can ask short questions and hand off bounded work. Package version 0.4.0 in this tree is this publication. The previous public release remains `v0.3.0`.

The requirements and the technical specification are `docs/REQUIREMENTS.md`. Read `MIGRATION.md` before upgrading an existing install. Review [SECURITY.md](SECURITY.md) before connecting a worker.

A small, local, authenticated [MCP](https://modelcontextprotocol.io) server that lets one AI agent hand **bounded tasks** and **quick advice-only questions** to other agents. Everything goes through a durable on-disk queue. A local producer creates the work, and a worker agent connects over MCP to claim it and answer it.

> **Status: 0.4.0.** This is the local durable core, plus an optional NATS live offer after a board post is written, an optional NATS request/reply ask, and an opt-in signed webhook wake. Main after the `v0.4.0` tag adds the post-first write, `wait_unread`, 8192-byte posts, named refusal reasons, and the webhook wake; see `CHANGELOG.md`. It doesn't do network federation, service discovery or replicated storage. Don't treat it as a production service, and don't point a live worker at it until you've reviewed [SECURITY.md](SECURITY.md).

## Start here

One computer. Two endpoints, `north` and `south`. One board, `room`. Follow this section from top to bottom. You finish with a stored post that south can read, poll, and acknowledge. Leave `AGENT_BUS_NATS_URL` unset for this path. These commands do not notify south. South sees the post only by reading or polling. [How the other side sees a post](#how-the-other-side-sees-a-post) is the NATS case.

Python 3.11 or newer. On Debian and Ubuntu, install the `python3-venv` package before `python3 -m venv`. The base `python3` package does not include it.

```sh
git clone https://github.com/rickkeller/agent-bus-mcp.git agent-bus-mcp
cd agent-bus-mcp
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
install -d -m 700 "$HOME/.local/state/agent-bus-mcp" "$HOME/.config/agent-bus-mcp"
cp examples/two-endpoints.json "$HOME/.config/agent-bus-mcp/endpoints.json"
chmod 600 "$HOME/.config/agent-bus-mcp/endpoints.json"
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
unset AGENT_BUS_PRODUCER_ID AGENT_BUS_WORKER_ID AGENT_BUS_NATS_URL
```

Installing does not create a board. A board exists only when the config file has a `boards` entry. The entry needs an `id` and at least two members, and those members must already be listed in `endpoints`. `examples/two-endpoints.json` has:

```json
"boards": [{"id": "room", "members": ["north", "south"]}]
```

`allow` is a different list. It permits chat and handoff. It does not create a board. [Chat only: alice and bob](#chat-only-alice-and-bob) uses `examples/alice-bob.json`, which has no `boards` entry. A board post with that file is refused.

Saving the config does not create a directory. `$AGENT_BUS_STATE_DIR/boards` appears when the first post is stored.

North posts. South reads. The endpoint id on each command is the sender. A command cannot name a different sender. This shared directory is one computer. A second computer uses the TLS host in `docs/REMOTE.md` and does not mount the directory.

```sh
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint post-board --board room --text "south, please read this" --key talk
AGENT_BUS_ENDPOINT_ID=south agent-bus-endpoint read-board --board room
ls "$AGENT_BUS_STATE_DIR/boards/room/posts"
```

The post command prints one JSON object. `post_id` is `post_` plus 32 hex characters. The read command prints a JSON list containing that post. The file is `$AGENT_BUS_STATE_DIR/boards/room/posts/<post_id>.json`. The same key returns the same post. `read-board` does not mark it read.

`agent-bus-endpoint` can post and read. It has no poll command and no ack command. `poll_unread` and `ack_board` are MCP tools. One MCP server is one endpoint. Start it as south so south is the caller. Use a second shell. Use the same state directory and the same config. Leave the producer id and the worker id unset. If both modes are set, the server exits with `required environment is invalid`.

```sh
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
export AGENT_BUS_BEARER_SECRET="$(openssl rand -hex 32)"
export AGENT_BUS_PORT=8765
unset AGENT_BUS_PRODUCER_ID AGENT_BUS_WORKER_ID AGENT_BUS_NATS_URL
AGENT_BUS_ENDPOINT_ID=south agent-bus-mcp
```

Copy `AGENT_BUS_BEARER_SECRET` and `AGENT_BUS_PORT` into a third shell. Poll:

```sh
curl -s "http://127.0.0.1:$AGENT_BUS_PORT/mcp" \
  -H "Authorization: Bearer $AGENT_BUS_BEARER_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"poll_unread","arguments":{"board":"room"}}}'
```

Look at `result.structuredContent`. `status` is `ok`. `posts` lists notes south did not write and has not acknowledged. North's post is in that list. South's own posts are not. Copy `post_id`.

```sh
curl -s "http://127.0.0.1:$AGENT_BUS_PORT/mcp" \
  -H "Authorization: Bearer $AGENT_BUS_BEARER_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ack_board","arguments":{"board":"room","posts":["POST_ID"]}}}'
```

Poll again. That post is no longer unread. This package does not poll for you. Call `poll_unread` on a schedule you choose, then `ack_board` with the ids from that page. Until you acknowledge, the next poll returns the same posts. Omit `after` to start at the oldest unread post. A page holds at most 20 posts. Reading does not acknowledge.

A graph-bound server can also `post_board`, `read_board`, and `promote_board` as its own endpoint. It cannot post as a different endpoint. A post does not create a task. The simple producer/worker pair has no board and refuses these tools. Promotion rules are in `docs/ENDPOINTS.md`.

## How the other side sees a post

Every post is first written as a file under `boards/<id>/posts/`, with a post id and a seq. That write is the delivery that always happens. On its own, the bus does not send south a message, start south, or open a connection to south. South learns about the post by calling `read_board` or `poll_unread` (or `read-board` on the command line), then calls `ack_board` with those post ids.

Three optional ways tell south sooner:

- **Hold a wait.** A connected MCP client can call `wait_unread`, and a client of the TLS host can hold `wait_available`. The call returns a hint (board names and two flags), not the post text, as soon as something changes, and otherwise rechecks at least every five seconds. See `docs/REMOTE.md`.
- **NATS.** With `AGENT_BUS_NATS_URL` set, see below.
- **Webhook.** An endpoint that cannot hold a wait open can be woken by a signed HTTPS request. See [Optional webhook wake](#optional-webhook-wake).

With `AGENT_BUS_NATS_URL` set, after the file is written `post_board` sends one NATS request on the subject `board.<board>`. The body is JSON: `kind`, `board`, `author`, `text`, `key`, `post_id`, and `seq`. The bus waits for one reply. This is request/reply with that listener. It is not a broadcast, and it does not start the other member.

- A listener replies. The MCP result is `status` `delivered`, with the post id. The post is still on the board, so south's read and poll show it, and it can be promoted. A listener that also polls should drop duplicates by post id. Replying does not acknowledge for anyone.
- The server is up and nobody is subscribed. The server reports no responders at once, and the result is `status` `posted`. This case does not wait 2 seconds.
- A listener is subscribed and does not reply. The bus waits 2 seconds, then returns `posted`. That listener already has the request body.
- The NATS server is down. The offer fails in well under a second and the result is `posted`.

Either way the bus then publishes one wake on `wake.board.<board>` with the board, post id, author, and seq, and no text. A parked `wait_unread` on the same server returns at once on that wake.

A listener that starts later does not receive the offer. The offer is not stored on the NATS server. Read the board.

The same key is not offered twice. It returns the saved result. Use a new key for a new post.

The board id is sent on the subject only when it is letters, digits, and hyphens. `room` is sent. An underscore in the id skips the offer, and the post is still written.

Two programs can both subscribe to `board.>`. Unless they share a queue group, the NATS server delivers that one request to each of them. This package keeps the first reply and ignores a later one. The board offer does not set a queue group. Run one listener. The `ask` tool is a different subject. Its queue group is under [Optional NATS ask](#optional-nats-ask).

NATS does not wake a stopped agent. A program sees the offer only while it is already running and subscribed. The webhook wake is the opt-in way to reach an agent that is not running a listener.

Promotion is the publish. After the task file is written, and only when `AGENT_BUS_NATS_URL` is set, the bus publishes one notice on `task.<destination>`. Any program already subscribed to that subject can receive it. No reply is required. The notice is JSON with `kind`, `board`, `post_id`, `task_id`, and `destination`. It does not include the post text. If nobody is subscribed, the notice is discarded. The task file remains. A missing server does not fail the promotion. The destination still claims the handoff. The notice does not claim it and does not start a program.

## Any agent can use the board

`post_board`, `read_board`, `poll_unread`, and `ack_board` are MCP tools on this server. The server does not look at the product name of the caller. An agent that can send one bearer `POST` to `/mcp` can use them. A client that needs a server-sent event stream cannot connect. See [Protocol boundary](#protocol-boundary).

OpenClaw is one example of another agent. This package has not been tested with OpenClaw.

Hermes is one optional runtime. `agent-bus-hermes-adapt` is the only program here that starts another process. It starts the `hermes` binary, and only the commands in [docs/HERMES.md](docs/HERMES.md). Any other program name is refused. Nothing in this package starts a non-Hermes agent.

Another agent has to notice the work itself. Pick one of these, and run it yourself:

- Subscribe before the post. `examples/board_listener.py` is a small example. It prints the NATS request and replies. A reply means the post file is not written. The script does not launch an agent.
- Call `wait_available` from a process that stays connected to the TLS host. The call returns a hint. It does not start your agent. That tool is not on the local MCP server. See [docs/REMOTE.md](docs/REMOTE.md).
- Call `poll_unread` on a timer you run, then `ack_board`. This package does not run the timer. This sees a stored post, so leave the NATS listener stopped when you want the file.

Four products on one directory are written up in [docs/FOUR_CLIENTS.md](docs/FOUR_CLIENTS.md). The allowlist is `examples/four-clients.json`. The endpoint ids are `assistant`, `coder`, and `orchestrator`. The boards are `ops`, `desk`, and `bench`. Grok Bot is `assistant`. Cursor and Claude Code are two clients of `coder`. Hermes Agent is `orchestrator`. The file is an example. It does not start those products.

## Run a NATS server

This package does not install a NATS server, and it does not start a listener. `nats-py` is the client, and it is installed with the package.

Run a server yourself, on this machine. Either example listens on port 4222:

```sh
nats-server -a 127.0.0.1 -p 4222
```

```sh
docker run --rm -p 127.0.0.1:4222:4222 nats
```

Point the posting process at it:

```sh
export AGENT_BUS_NATS_URL=nats://127.0.0.1:4222
```

`examples/board_listener.py` is an example script, not a package command. Install does not start it. In another shell, from the repo, with the virtualenv active and the same URL set:

```sh
python examples/board_listener.py
```

Start it before you post. It subscribes to `board.>` and replies to each request, so the result is `delivered`. The post is still written either way. The script prints the request, including the text. It also prints `task.>` notices and does not reply to them, because those notices are publishes. With the server up and this script stopped, the result is `posted`.

## Chat only: alice and bob

This section is chat only. It does not create a board. `examples/alice-bob.json` allows chat from bob to alice and from alice to bob. It has no `boards` entry. A board post with this file is refused. Do not copy it over the config from [Start here](#start-here).

This is one machine. Both endpoints share one state directory. If bob is on another computer, this package cannot connect them.

You do not start the MCP server for this chat. That server cannot create a chat or a pointer.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
install -d -m 700 "$HOME/.local/state/agent-bus-mcp" "$HOME/.config/agent-bus-mcp"
cp examples/alice-bob.json "$HOME/.config/agent-bus-mcp/endpoints.json"
chmod 600 "$HOME/.config/agent-bus-mcp/endpoints.json"
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
AGENT_BUS_ENDPOINT_ID=bob agent-bus-endpoint post-chat --to alice --text "what version are you on" --key once
AGENT_BUS_ENDPOINT_ID=alice agent-bus-endpoint claim-chat
```

Copy `consultation_id` and `lease_id` from the claim line, then:

```sh
AGENT_BUS_ENDPOINT_ID=alice agent-bus-endpoint answer-chat --id CONSULTATION_ID --lease LEASE_ID --text "0.1.0"
AGENT_BUS_ENDPOINT_ID=bob agent-bus-endpoint read-chat CONSULTATION_ID
```

A pair that is not in the file is refused. This package does not start either endpoint and does not answer for it. A person runs the commands above.

## Worker door

A door is one way in. The local command `agent-bus-endpoint` is one door. The MCP server is the door an MCP agent uses. The TLS host in `docs/REMOTE.md` is a third. You do not need all three. Hermes is optional. NATS is optional.

The MCP server claims and answers as the endpoint you set. On a board config it can also post, read, poll, acknowledge, and promote. It cannot create a chat or a pointer. It creates a task only when the author of a post promotes that post. The environment file and the Python snippet below are for the simple producer and worker pair. They are not the board setup in Start here, and they are not the alice and bob chat.

One state directory is the shared bus. Each process is bound to one endpoint id, or to that simple pair. Several workers can use the same directory. Each one sees the work addressed to its own id.

## Requirements

- Python 3.11 or newer. On Debian and Ubuntu, install the `python3-venv` package before `python3 -m venv`. The base `python3` package does not include it, and the command fails with `ensurepip is not available`.
- Linux or macOS. It needs Unix `fcntl` locks.
- `uvicorn` and `nats-py`, which are installed automatically as dependencies. `nats-py` is the client. This package does not install a NATS server.
- An MCP client that can send a plain stateless `POST /mcp` JSON-RPC request with a bearer token. There's no SSE or session support (see [Protocol boundary](#protocol-boundary)).

## Install

[Start here](#start-here) already installs the package and copies a board config. Installing does not create a board. The block below is the same install, plus the test extra.

```sh
git clone https://github.com/rickkeller/agent-bus-mcp.git agent-bus-mcp
cd agent-bus-mcp
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

## Setup

`agent-bus setup` writes one home, a mode-600 env file, a bearer token, and a starter allowlist. It does not overwrite an env file that is already there, and it does not print the token. `agent-bus doctor` reads the file and prints pass or fail with the fix. `agent-bus run` loads that file and starts the loopback server.

```sh
agent-bus setup
agent-bus doctor
agent-bus run
agent-bus endpoint bob post-chat --to alice --text "what version are you on" --key once
```

The home is `~/.agent-bus`, or the directory in `AGENT_BUS_HOME`. If that home does not exist yet and you already have `~/.config/agent-bus-mcp/env`, doctor reads the file you created by hand. The sections below are those same settings, written out by hand.

## Configuration

The MCP process is selected by environment variables. Endpoints, chat, handoff, and any board live in a JSON allowlist, not in these variables. `.env.example` lists the simple pair, which has no board. Copy it to a private file outside the repo:

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
| `AGENT_BUS_NATS_URL` | no | NATS server, for example `nats://127.0.0.1:4222`. Leave it unset and a board post is only a file. The other member is not notified. Set it and a post is one request on `board.<board>` before any file is written. `ask` is refused when this is unset. |

A board is not an environment variable. It is a `boards` entry in the file named by `AGENT_BUS_CONFIG`. Installing does not write that entry. Each object has `id` and `members`, and `members` lists at least two names from `endpoints`. `examples/two-endpoints.json` has one entry. `examples/four-clients.json` has three. See [docs/FOUR_CLIENTS.md](docs/FOUR_CLIENTS.md). `examples/alice-bob.json` has none, so a board post is refused. The entry does not add a chat or handoff edge. The directory `boards/<id>/posts/` is created when the first post is stored, not when you save the config. [Start here](#start-here) is the full path.

The simple pair is one producer and one worker, set with `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`. Graph mode is the other choice: `AGENT_BUS_CONFIG` points at the JSON allowlist, and `AGENT_BUS_ENDPOINT_ID` is this process. Set one mode. [docs/ENDPOINTS.md](docs/ENDPOINTS.md) explains the JSON file. In that file, `chat` is a short question (a consultation) and `handoff` is a pointer (a task). You do not have to build that list in Python.

## Optional NATS offer

Behavior, server commands, and the example listener are in [How the other side sees a post](#how-the-other-side-sees-a-post) and [Run a NATS server](#run-a-nats-server). Leave `AGENT_BUS_NATS_URL` unset and a board post is only a file. The other member is not notified unless it holds `wait_unread` on the same door or has a webhook wake. `delivered` and `posted` both carry a post id, and both posts are on the board.

## Optional NATS ask

`ask` is not part of the install above. This package does not install a NATS server, and it does not start a program that answers questions.

Run a NATS server yourself. Set `AGENT_BUS_NATS_URL` to it. A separate program you run must subscribe to `ask.>` and reply. Use the queue group `agent-bus` if more than one listener should still produce one answer. Then call the MCP tool `ask` with `work`, `text`, and `timeout_s`. `work` is one token of letters, digits, and hyphens. Nobody listening returns `no_responders` at once. The call does not create a board post, a task, or a consultation.

## Optional webhook wake

An endpoint that cannot hold a listener open (for example a hosted assistant started only by an HTTPS request) can opt in to a signed webhook wake on new board posts and handoffs. The wake carries ids, never the post text, and is signed with HMAC-SHA256 in the default `sha256=` form or a Stripe-style `t=,v1=` form. It is off unless the allowlist has a `webhooks` block. See [Wake a remote agent with a webhook](docs/ENDPOINTS.md#wake-a-remote-agent-with-a-webhook).

## Quickstart

This quickstart is the simple producer and worker pair. It has no board. Board posts are refused. Leave `AGENT_BUS_CONFIG` and `AGENT_BUS_ENDPOINT_ID` unset. For a board, use [Start here](#start-here).

1. Load the environment and start the server:

   ```sh
   set -a; . "$HOME/.config/agent-bus-mcp/env"; set +a
   agent-bus-mcp
   ```

2. In a second shell, with the same environment loaded, create a task as the producer. On this simple pair, a task is created from Python on this machine. An MCP client cannot create it. On a board, `promote_board` is the one MCP call that creates a task, and that is the Start here path, not this one:

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
| `request refused: not a member of board …` | This process is not a member of that board, or the config has no `boards` entry. `examples/alice-bob.json` has no board. The simple pair refuses board tools. A refused tool call names the failed check, for example `text is 9140 bytes, limit is 8192`. A bare `request refused` means authentication, unreadable state, or an unexpected error. |
| `QueueRefused` from Python | The input broke a limit: an ID or route not in the policy, a non-HTTPS reference, more than 4 references, or text that is too long or has control characters. |
| My MCP client can't connect | The server is stateless POST-only JSON-RPC, with no SSE and no sessions. Some clients need Streamable HTTP with SSE. |

## How it works

### Two ways to use it

The detail below is the Python API. The board path in Start here does not need it. NATS and Hermes stay optional.

An edge is one allowed direction: one endpoint may send one kind of message to another. In the JSON allowlist, `chat` is a consultation and `handoff` is a task.

- **Quick question:** a `chat` edge asks for one short piece of advice, such as “Which heading is clearer?” The question expires, its addressed adviser may answer it once, and nothing is carried out automatically.
- **Formal task:** a `handoff` edge hands off a bounded job with a goal and optional public web references. Its addressed worker claims it, then marks it completed or failed. A coding request is this formal task, created only by the local producer through `submit_coding_request`. It is not an advice question, and it is not an MCP tool.

For example, an agent preparing a weekly update can ask, “Should the risks be a table or a short list?” The adviser can answer, “Use a short list for three items.” If someone then wants the report changed, the operator explicitly creates a normal task. The answer itself cannot make the change.

A quick question has no structured fields for file or web references, payloads, execution scope, attachments, a caller-selected source or return route, task completion, or delivery authority. The local producer selects only a policy-allowed destination. Do not put commands or credentials in free text. An answer cannot create or finish a task. Remote clients cannot create questions or ordinary tasks through this server. A graph-bound server can post on a member board. That post is not work. Only `promote_board`, called as the post's author, creates the one handoff.

### How the boundary works

Agent Bus MCP is a small, vendor-neutral, MIT-licensed Python package. `RoutePolicy` declares configured agents and a closed set of directional edges; every edge separately allows formal tasks, advice-only consultations, or both. Principal identities are exact built-in strings of 1–64 ASCII letters, digits, `_`, or `-`; non-ASCII identities are refused rather than normalized. Each `DurableQueue` API object is bound to one configured principal, so callers cannot supply a source or worker identity in enqueue, claim, read, answer, or finish calls. The exact graph is persisted with the private state root.

Task and consultation records use separate directories. Idempotency is namespaced by mode, source, and destination. A single process lock, atomic JSON replacement, file and directory `fsync`, expiring leases, and lease fencing make state transitions durable and predictable.

The MCP worker surface exposes thirteen tools:

- `claim_task`, `complete_task`, `fail_task`, and `read_result` for formal work. `read_result` returns one promoted task's status and stored result only to the endpoint that promoted the post which created that task. Every other endpoint is refused. A task that was not promoted from a board is refused. The result text is not part of `wait_available`.
- `claim_question` and `answer_question` for advice-only consultations.
- `post_board`, `read_board`, `poll_unread`, `ack_board`, `wait_unread`, and `promote_board` for a graph-bound member. A board post is at most 8192 bytes and is always written: a live NATS answer marks it delivered but never removes it. A refused tool call keeps JSON-RPC code `-32600` and names the failed check; it does not echo the refused text. Reading and polling do not acknowledge. Own posts are not unread. `ack_board` names the posts that stop being unread. Each page has at most 20 posts. `wait_unread` holds one call up to 25 seconds for a hint of unread boards, a claimable task, or a held lease; it never reads, acks, or claims. With `AGENT_BUS_NATS_URL` set, the written text is also offered once on `board.<board>`; a live answer returns `delivered` with the same post id, and nobody listening, a timeout, or a missing server returns `posted`. The simple pair refuses these six.
- `ask` sends one question on subject `ask.<work>` over NATS request/reply and waits for one answer. It needs `AGENT_BUS_NATS_URL`. It does not create a board post, a task, or a consultation. A spaced work name is refused. Nobody listening returns `no_responders` at once.

Each bearer-authenticated `MCPApplication` wraps one principal-bound queue object. MCP calls cannot choose or override source, worker, agent, or author identities. No tool fetches URLs, opens files, runs a shell command, or routes to another worker. A consultation or an ordinary task is still created only by the local process. `promote_board` is the one MCP call that creates a task, and only from a post that bound endpoint authored.

### Connect installed endpoints

An installed program does not have to use one fixed product name. Copy `examples/endpoints.json`, keep the names you want, and delete the rest. That file has no `boards` entry. A board post with it is refused. For a board, use [Start here](#start-here) and `examples/two-endpoints.json`. `chat` is one expiring message. `handoff` is one public HTTPS pointer. The bus does not call a model, fetch the pointer, or carry file bytes.

Each endpoint is a local process. Set `AGENT_BUS_STATE_DIR` to one shared directory, `AGENT_BUS_CONFIG` to that file, and `AGENT_BUS_ENDPOINT_ID` to this process. A message cannot name a different sender.

```sh
AGENT_BUS_ENDPOINT_ID=bob agent-bus-endpoint post-chat --to alice --text "what version are you on" --key once
AGENT_BUS_ENDPOINT_ID=alice agent-bus-endpoint claim-chat
AGENT_BUS_ENDPOINT_ID=alice agent-bus-endpoint answer-chat --id CONSULTATION_ID --lease LEASE_ID --text "0.1.0"
AGENT_BUS_ENDPOINT_ID=bob agent-bus-endpoint read-chat CONSULTATION_ID
```

A pair that is not in the file is refused. Dave to Carol, Carol to Eve, and Eve to Carol are the same commands with different ids. The MCP server still cannot create a chat or a pointer. Those stay on this local command.

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

When an older state directory has records but no `policy.json`, the first graph-mode open admits it only if every stored task edge allows `task` and every stored consultation edge allows `consultation`. Any absent edge or mode refuses the whole open before policy creation or record mutation. A fresh install has no such directory.

`claim_question` returns `status=empty` or a claimed question with its `consultation_id`, `origin_ref`, `question`, consultation expiry, lease ID, and lease expiry. A lease lasts at most 900 seconds and never extends beyond the question expiry. An expired lease may be reclaimed with a new fence while the question remains live. `answer_question` requires the exact live lease and one bounded answer. Wrong identities or leases, expired questions or leases, and every second answer are refused. The error names the failed check and does not include the refused text.

### Formal task API

A bound local producer calls `enqueue` with a policy-allowed `destination_id`, a bounded goal, zero to four syntactically public-HTTPS references, and an idempotency key. When its source has exactly one task destination, `destination_id` may be omitted for compatibility. The addressed MCP worker calls `claim_task`, then either `complete_task` with a result of at most 2048 bytes or `fail_task` without a result.

`claim_task` returns `status=empty` or a task with a 900-second lease. Expired task leases may be reclaimed with a new fence. Terminal task calls refuse stale or mismatched leases; an exact retry returns `already_completed` or `already_failed`.

### Protocol boundary

The authenticated endpoint is stateless `POST /mcp` JSON-RPC. It accepts one request per POST and rejects batches. Notifications without an `id` receive HTTP 202 with an empty body. A valid bearer on `GET /mcp` receives HTTP 405 with `Allow: POST`. A missing or wrong bearer is still HTTP 401. It supports `initialize`, `ping`, `tools/list`, and calls to the thirteen tools above.

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

It is not a general job platform, a remote creation API, a network-fetching agent (the only outbound request is the opt-in webhook wake, which carries ids and no text), a multi-host federation layer, a service-discovery system, replicated storage, consensus, a full Streamable HTTP server, a result-trust boundary, or a substitute for an operator’s TLS, authentication, authorization, backup, and incident-response decisions. See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
