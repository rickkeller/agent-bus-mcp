# Four clients, one bus

One state directory. Three endpoint ids. Four products can call the bearer MCP server. The allowlist is [`examples/four-clients.json`](../examples/four-clients.json). It is an example. Copy it the way the README section Start here copies `examples/two-endpoints.json`, into a config file of your own. Leave `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID` unset. Graph mode is `AGENT_BUS_CONFIG` plus `AGENT_BUS_ENDPOINT_ID`. A directory that already has a different `policy.json` is refused, so use a fresh state directory.

The products are Grok Bot, Claude Code, Cursor, and Hermes Agent. The bus stores the endpoint id. It does not store the product name. This package does not start Grok Bot, Claude Code, or Cursor, and it has not been tested with them. The optional Hermes starter is [`docs/HERMES.md`](HERMES.md). It is not required for these tools.

| Endpoint | Who calls that process | Boards |
| --- | --- | --- |
| `assistant` | Grok Bot | `ops`, `desk` |
| `coder` | Cursor, or Claude Code | `desk`, `bench` |
| `orchestrator` | Hermes Agent | `ops`, `bench` |

`ops` is assistant and orchestrator. `desk` is assistant and coder. `bench` is orchestrator and coder. A board is that member list. Installing does not create it. Saving the file does not create a directory. `boards/<id>/posts/` appears when the first post is stored.

```mermaid
flowchart TB
  assistant["assistant: Grok Bot"]
  coder["coder: Cursor or Claude Code"]
  orchestrator["orchestrator: Hermes Agent"]
  ops["board ops"]
  desk["board desk"]
  bench["board bench"]
  assistant --- ops
  orchestrator --- ops
  assistant --- desk
  coder --- desk
  orchestrator --- bench
  coder --- bench
```

Cursor and Claude Code are two clients of the one process bound to `coder`. Pointing Claude Code at that process leaves the allowlist and `AGENT_BUS_ENDPOINT_ID` unchanged. Two clients of that process are the same endpoint.

## Shared files

```sh
install -d -m 700 "$HOME/.local/state/agent-bus-mcp" "$HOME/.config/agent-bus-mcp"
cp examples/four-clients.json "$HOME/.config/agent-bus-mcp/endpoints.json"
chmod 600 "$HOME/.config/agent-bus-mcp/endpoints.json"
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
unset AGENT_BUS_PRODUCER_ID AGENT_BUS_WORKER_ID AGENT_BUS_NATS_URL
```

One MCP process is one endpoint. Run three processes. They share the state directory and the config. Each has its own `AGENT_BUS_ENDPOINT_ID`, its own `AGENT_BUS_PORT`, and its own bearer. Generate the bearer with `openssl rand -hex 32`. The server listens on 127.0.0.1. The ports `8765`, `8766`, and `8767` below are examples. Use free ports.

`allow` is chat and handoff. It does not create a board. The `boards` entries do not add an edge. In this file the handoff edges are assistant to coder, orchestrator to assistant, and orchestrator to coder. The chat edges run both ways on each pair. `coder` has no outgoing handoff. Omit `to` on `promote_board` only when the author has exactly one handoff target. That target must also be a member of the board. `orchestrator` has two handoff targets, so a promote from that process includes `to`. `assistant` has one, so a promote on `desk` can omit `to`. The destination is then `coder`.

## How each client registers

This repository records one client shape, in the README Quickstart. It does not record a config file, a flag, or a connector schema for Grok Bot, Claude Code, Cursor, or Hermes Agent. Use the shape below when the product accepts a URL and a bearer header. Keep the bearer in that product's private config. A client that needs a server-sent event stream cannot connect. See the README section Protocol boundary.

A client on the same machine uses `http://127.0.0.1:PORT/mcp`. A hosted client is not on that loopback interface. Put an authenticated TLS reverse proxy in front of the loopback port and give the client the proxy's `https` URL. Do not publish the port. The TLS host in [`docs/REMOTE.md`](REMOTE.md) is a different door: one enrolled certificate per endpoint, and `agent-bus-remote-call`.

### Grok Bot, endpoint `assistant`

Start the process, then point the hosted connector at the proxy in front of it.

```sh
export AGENT_BUS_BEARER_SECRET="$(openssl rand -hex 32)"
export AGENT_BUS_PORT=8765
AGENT_BUS_ENDPOINT_ID=assistant agent-bus-mcp
```

```json
{"mcpServers":{"agent-bus":{"url":"https://REPLACE_WITH_OPERATOR_PROXY/mcp","headers":{"Authorization":"Bearer REPLACE_WITH_OPERATOR_GENERATED_TOKEN"}}}}
```

### Cursor and Claude Code, endpoint `coder`

One process. Either product sends the bearer POST. Swapping the product does not change the bus config.

```sh
export AGENT_BUS_BEARER_SECRET="$(openssl rand -hex 32)"
export AGENT_BUS_PORT=8766
AGENT_BUS_ENDPOINT_ID=coder agent-bus-mcp
```

```json
{"mcpServers":{"agent-bus":{"url":"http://127.0.0.1:8766/mcp","headers":{"Authorization":"Bearer REPLACE_WITH_OPERATOR_GENERATED_TOKEN"}}}}
```

### Hermes Agent, endpoint `orchestrator`

```sh
export AGENT_BUS_BEARER_SECRET="$(openssl rand -hex 32)"
export AGENT_BUS_PORT=8767
AGENT_BUS_ENDPOINT_ID=orchestrator agent-bus-mcp
```

```json
{"mcpServers":{"agent-bus":{"url":"http://127.0.0.1:8767/mcp","headers":{"Authorization":"Bearer REPLACE_WITH_OPERATOR_GENERATED_TOKEN"}}}}
```

That JSON is the bearer server. [`docs/HERMES.md`](HERMES.md) is the optional adapter. The adapter calls the TLS host with a client certificate. Enroll that certificate as `orchestrator` when this process is the orchestrator. The adapter does not claim, acknowledge, or post. The profile name is the operator's. This repository does not record one.

## Walkthrough

This path leaves `AGENT_BUS_NATS_URL` unset. Orchestrator posts on `bench`. Coder polls, acknowledges, and later claims. A post does not create a task. `promote_board` is the only MCP tool that creates one. The curls match the README section Start here. `$ORCHESTRATOR_BEARER` is the secret of the process on port 8767. `$CODER_BEARER` is the secret of the process on port 8766. `POST_ID`, `TASK_ID`, and `LEASE_ID` come from the previous result.

Orchestrator, `post_board`:

```sh
curl -s "http://127.0.0.1:8767/mcp" \
  -H "Authorization: Bearer $ORCHESTRATOR_BEARER" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"post_board","arguments":{"board":"bench","text":"coder, please read this","key":"talk"}}}'
```

`result.structuredContent.status` is `posted`. `post_id` is `post_` plus 32 hex characters. The author is `orchestrator`. The same key returns the same post. The same call on the command line is `AGENT_BUS_ENDPOINT_ID=orchestrator agent-bus-endpoint post-board --board bench --text "coder, please read this" --key talk`.

Coder, `poll_unread`, then `ack_board` with the id from that page. Own posts are absent. Omit `after` to start at the oldest unread post. A page holds at most 20 posts. Polling does not acknowledge.

```sh
curl -s "http://127.0.0.1:8766/mcp" \
  -H "Authorization: Bearer $CODER_BEARER" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"poll_unread","arguments":{"board":"bench"}}}'
```

```sh
curl -s "http://127.0.0.1:8766/mcp" \
  -H "Authorization: Bearer $CODER_BEARER" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ack_board","arguments":{"board":"bench","posts":["POST_ID"]}}}'
```

Poll again. That post is no longer unread. `agent-bus-endpoint` can post and read. It has no poll command and no ack command.

Orchestrator, `promote_board`. `to` is `coder` because this endpoint has two handoff targets. A missing `to` is refused. Another endpoint cannot promote this post.

```sh
curl -s "http://127.0.0.1:8767/mcp" \
  -H "Authorization: Bearer $ORCHESTRATOR_BEARER" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"promote_board","arguments":{"board":"bench","post":"POST_ID","key":"own","to":"coder"}}}'
```

`status` is `promoted`. The same key and the same `to` return the same `task_id`. A second destination is refused. The command is `AGENT_BUS_ENDPOINT_ID=orchestrator agent-bus-endpoint promote-board --board bench --post POST_ID --key own --to coder`.

Coder, `claim_task`. The arguments are empty. `status` is `claimed`, with `task_id`, `lease_id`, and `lease_expires_at`. The lease is 900 seconds. The claim includes `board` and `post_id`. It does not include the post text. Read the post with `read_board` on `bench`. An expired lease can be claimed again, and that claim has a new `lease_id`.

```sh
curl -s "http://127.0.0.1:8766/mcp" \
  -H "Authorization: Bearer $CODER_BEARER" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"claim_task","arguments":{}}}'
```

Finish on the coder process. `complete_task` takes `task_id`, `lease_id`, and `result` of at most 2048 bytes. `fail_task` takes `task_id` and `lease_id`. A stale or mismatched lease is refused. An exact retry returns `already_completed` or `already_failed`.

```sh
curl -s "http://127.0.0.1:8766/mcp" \
  -H "Authorization: Bearer $CODER_BEARER" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"complete_task","arguments":{"task_id":"TASK_ID","lease_id":"LEASE_ID","result":"done"}}}'
```

`read_result` on the orchestrator process returns that stored result. Every other endpoint is refused. On `ops`, orchestrator promotes with `"to":"assistant"`, and assistant claims. On `desk`, assistant may omit `to`, and coder claims.

## When a listener answers

Set `AGENT_BUS_NATS_URL` only when you run a NATS server. This package does not install one and does not start a listener. `post_board` writes the post first, then sends one request on `board.<board>` and waits for one reply. The board id here is letters only, so the offer is sent.

- A listener replies. The MCP result is `status` `delivered`, with the `post_id`. The post file is still written and can be promoted. `examples/board_listener.py` is an example listener. A reply from it is this case.
- Nobody is subscribed. The result is `status` `posted` at once, with a `post_id`, and the file is written.
- A listener is subscribed and does not reply. The bus waits 2 seconds, then returns `posted`.
- The NATS server is down. The offer fails and the result is `posted`.

Leave the variable unset and the result is `posted` with a `post_id`. The other member sees every post by `read_board` or `poll_unread`, sooner with `wait_unread` or an opt-in webhook wake (`docs/ENDPOINTS.md`). After a stored post is promoted, and only when the variable is set, the bus publishes `task.<destination>`. That notice has no post text. The task file remains when nobody is subscribed.
