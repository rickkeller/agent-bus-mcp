# Connect endpoints

The bus carries two kinds of message. It does not call a model, and it does not start the other program.

- **chat** is one short message. It expires. It does not become work.
- **handoff** is one public HTTPS pointer. The bus does not fetch it and does not carry the file.

## Allowlist

For the first pair, copy `examples/jig-grokbot.json`. It allows chat from grokbot to jig and from jig to grokbot. Copy `examples/endpoints.json` only when you want the other names. Keep the endpoint names you want and delete the rest. An edge says who may send which kind to whom. A missing edge is refused.

An endpoint id is 1–64 characters from `A-Z`, `a-z`, `0-9`, `_`, or `-`. Do not put a token, a host path, or a chat id in this file.

## One process, one id

Each program uses the same state directory and the same allowlist, and its own id. A message cannot name a different sender.

```sh
install -d -m 700 "$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
export AGENT_BUS_ENDPOINT_ID=jig
agent-bus-endpoint post-chat --to grokbot --text "what version are you on" --key once
```

The other endpoint claims and answers with its own id:

```sh
AGENT_BUS_ENDPOINT_ID=grokbot agent-bus-endpoint claim-chat
AGENT_BUS_ENDPOINT_ID=grokbot agent-bus-endpoint answer-chat --id CONSULTATION_ID --lease LEASE_ID --text "0.1.0"
AGENT_BUS_ENDPOINT_ID=jig agent-bus-endpoint read-chat CONSULTATION_ID
```

A handoff is the same shape:

```sh
agent-bus-endpoint post-handoff --to openclaw --pointer https://example.invalid/note --key file-1
AGENT_BUS_ENDPOINT_ID=openclaw agent-bus-endpoint claim-handoff
```

`dots`, `openclaw`, `hermes`, `grokbot`, and `jig` in the example are names you can keep or replace. They are not built-in programs. Each one still needs its own client, or a person runs this command for it.

## What this is not

The MCP server is a separate door. It can claim and answer. When it is bound to a graph endpoint, that same identity can `post_board`, `read_board`, `poll_unread`, `ack_board`, and `promote_board`. The caller cannot name a different author. A post is at most 2048 bytes and does not create a task. Promote follows the author and one-member rules above. `read_board` and `poll_unread` do not acknowledge. A member's own posts are not unread for that member. `ack_board` records the exact post ids; an unknown id refuses the whole call, and repeating an ack does not change the result. Omit `after` to start at the oldest unread post. `after` only asks for the next page. `limit` is 1 to 20 and defaults to 20. There is no callback. Chat and pointer creation stay on `agent-bus-endpoint`. For a graph, start the server with `AGENT_BUS_CONFIG` and `AGENT_BUS_ENDPOINT_ID` set to the member, and leave `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID` unset. The simple producer/worker pair has no board and refuses those five tools. It also cannot open a graph that has more than that one edge.

Local chat does not retry until someone acts. The TLS host in `docs/REMOTE.md` is a separate door on one state directory. A same-machine test is not a two-computer proof.

## Board

A board is a member list of two or more endpoint names. The names are whatever the installer wrote. A pair is a board of two. Members post and read. A post is at most 2048 bytes. A non-member is refused. A post does not own work.

Promotion is a separate step, and only the author of that post can take it. It names `to`. The destination must be a different member of the same board and a handoff target of the author. If `to` is omitted and the author has exactly one handoff target, that target is filled in when it is also a member. Zero targets, two targets, a non-member, or a member who is not a handoff target is a refusal. Another member cannot promote the post. Only the named member can claim the handoff. The claim line includes `board` and `post_id` before any worker can see the task. The owner reads the post with `read-board --board BOARD`. A bare board name is refused. The claim does not include the post text. The same promote key with the same destination returns the same handoff. The same key with a different destination is a refusal. Two promote keys for one post leave one handoff. Talk does not become a second handoff.

```sh
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint post-board --board room --text "south owns the fix" --key talk
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint promote-board --board room --post POST_ID --key own
AGENT_BUS_ENDPOINT_ID=south agent-bus-endpoint claim-handoff
```

`examples/two-endpoints.json` is that graph. `north` and `south` are not built-in programs. Replace them. Copy that file only for a local proof. It is not a network, and it does not start either program.

Jig can file a claimed chat as an ask when its data directory has `bus.json`. That file is documented in the Jig setup notes.
