# Connect endpoints

The bus carries two kinds of message. It does not call a model, and it does not start the other program. `ask` is not one of these messages. It is the optional MCP tool in the README, and this command does not send it.

- **chat** is one short message. It expires. It does not become work. In the JSON file the kind is `chat`. Later text calls this a consultation.
- **handoff** is one public HTTPS pointer. The bus does not fetch it and does not carry the file. In the JSON file the kind is `handoff`. Later text calls this a task.

A board is separate from both. It exists only when the config file has a `boards` entry. Installing does not add that entry. [Board](#board) below, and the README section Start here, are the board path. Install from that section before the commands here. Hermes is optional. NATS is optional. An MCP agent calls the tools named below. It does not have to be a particular product.

## Allowlist

For chat only, copy `examples/alice-bob.json`. It allows chat from bob to alice and from alice to bob. It has no `boards` entry. A board post with that file is refused. Copy `examples/endpoints.json` only when you want the other names. That file also has no `boards` entry. Keep the endpoint names you want and delete the rest. An edge says who may send which kind to whom. A missing edge is refused.

For a board, copy `examples/two-endpoints.json`. Its `boards` line is the board. The README section Start here runs that file through post, read, poll, and ack.

An endpoint id is 1–64 characters from `A-Z`, `a-z`, `0-9`, `_`, or `-`. Do not put a token, a host path, or a chat id in this file.

## One process, one id

Each program uses the same state directory and the same allowlist, and its own id. A message cannot name a different sender.

```sh
install -d -m 700 "$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
export AGENT_BUS_ENDPOINT_ID=alice
agent-bus-endpoint post-chat --to bob --text "what version are you on" --key once
```

The other endpoint claims and answers with its own id:

```sh
AGENT_BUS_ENDPOINT_ID=bob agent-bus-endpoint claim-chat
AGENT_BUS_ENDPOINT_ID=bob agent-bus-endpoint answer-chat --id CONSULTATION_ID --lease LEASE_ID --text "0.1.0"
AGENT_BUS_ENDPOINT_ID=alice agent-bus-endpoint read-chat CONSULTATION_ID
```

A handoff is the same shape. This edge is the one in `examples/endpoints.json` from dave to carol:

```sh
AGENT_BUS_ENDPOINT_ID=dave agent-bus-endpoint post-handoff --to carol --pointer https://example.invalid/note --key file-1
AGENT_BUS_ENDPOINT_ID=carol agent-bus-endpoint claim-handoff
```

`alice`, `bob`, `carol`, `dave`, and `eve` in the example are names you can keep or replace. They are not built-in programs. Each one still needs its own client, or a person runs this command for it.

## What this is not

Each way in is a door. `agent-bus-endpoint` is the local command. The MCP server is what an MCP agent calls. The TLS host is `docs/REMOTE.md`.

The MCP server can claim and answer. Graph mode is `AGENT_BUS_CONFIG` plus `AGENT_BUS_ENDPOINT_ID`, with the producer and worker ids unset. When the server is bound to one id in the JSON allowlist, that same identity can `post_board`, `read_board`, `poll_unread`, `ack_board`, and `promote_board`. The caller cannot name a different author. A post is at most 2048 bytes and does not create a task. With `AGENT_BUS_NATS_URL` unset, the post is a file and the other member is not notified. With it set, that post is one request on `board.<board>`, not a broadcast. One reply writes no file. Nobody subscribed, a listener that stays silent for 2 seconds, or a missing server writes the file, and the other member is still not notified. Only a promotion publishes `task.<destination>`, and that notice has no post text. Promote follows the author and one-member rules below. `read_board` and `poll_unread` do not acknowledge. A member's own posts are not unread for that member. `ack_board` records the exact post ids; an unknown id refuses the whole call, and repeating an ack does not change the result. Omit `after` to start at the oldest unread post. `after` only asks for the next page. `limit` is 1 to 20 and defaults to 20. There is no callback and no push. The other member calls `read_board` or `poll_unread` on a schedule you choose, then `ack_board`. `agent-bus-endpoint` can post and read a board. It cannot poll or acknowledge. Those two are MCP tools, and they are also tools on the TLS host in `docs/REMOTE.md`. Chat and pointer creation stay on `agent-bus-endpoint`. For a graph, start the server with `AGENT_BUS_CONFIG` and `AGENT_BUS_ENDPOINT_ID` set to the member, and leave `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID` unset. The simple producer/worker pair is `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`, with no JSON allowlist. It has no board and refuses those five tools. It also refuses to open a state directory whose saved policy is not exactly that producer, that worker, and both a task and a consultation.

Local chat does not retry until someone acts. The TLS host in `docs/REMOTE.md` is a separate door on one state directory. A same-machine test is not a two-computer proof.

## Board

A board is a member list of two or more endpoint names. The names are whatever the installer wrote. A pair is a board of two. The config object has `id` and `members`, and nothing else. `members` lists at least two endpoint ids. Installing does not create this object, and saving it does not create a directory. The folder `$AGENT_BUS_STATE_DIR/boards/<id>/posts/` appears when the first post is stored.

Members post and read. A post is at most 2048 bytes. A non-member is refused. A post does not own work.

Leave `AGENT_BUS_NATS_URL` unset. The post is written to that folder. The other member is not notified. Nothing is pushed to them. They see the text by running `read-board`, or by calling `poll_unread` and then `ack_board`. Reading and polling do not acknowledge. Their own posts are not unread. Run the poll on your own schedule. This package does not run it. The README section Start here has the commands, including the MCP curls. `examples/board_listener.py` is an example NATS listener, not part of this path.

Set `AGENT_BUS_NATS_URL` only when a program you started is already subscribed to `board.<id>` and will reply. That reply takes the post. No post file is written, and the other member's read does not show it. With the server up and no listener, the file is written at once, and the other member is still not notified. A listener that does not reply causes a 2 second wait, then the file. The README section How the other side sees a post is the full account. A delivered offer has no post id, so it cannot be promoted.

Promotion is a separate step, and only the author of that post can take it. It names `to`. The destination must be a different member of the same board and a handoff target of the author. If `to` is omitted and the author has exactly one handoff target, that target is filled in when it is also a member. Zero targets, two targets, a non-member, or a member who is not a handoff target is a refusal. Another member cannot promote the post. Only the named member can claim the handoff. The claim line includes `board` and `post_id` before any worker can see the task. The owner reads the post with `read-board --board BOARD`. A bare board name is refused. The claim does not include the post text. The same promote key with the same destination returns the same handoff. The same key with a different destination is a refusal. Two promote keys for one post leave one handoff. Talk does not become a second handoff.

```sh
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint post-board --board room --text "south owns the fix" --key talk
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint promote-board --board room --post POST_ID --key own
AGENT_BUS_ENDPOINT_ID=south agent-bus-endpoint claim-handoff
```

`examples/two-endpoints.json` is that allowlist. `north` and `south` are not built-in programs. Replace them. Copy that file only for a local proof. It is not a network, and it does not start either program.
