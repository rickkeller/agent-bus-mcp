# Connect endpoints

The bus carries two kinds of message. It does not call a model, and it does not start the other program. `ask` is not one of these messages. It is the optional MCP tool in the README, and this command does not send it.

- **chat** is one short message. It expires. It does not become work. In the JSON file the kind is `chat`. Later text calls this a consultation.
- **handoff** is one public HTTPS pointer. The bus does not fetch it and does not carry the file. In the JSON file the kind is `handoff`. Later text calls this a task.

A board is separate from both. It exists only when the config file has a `boards` entry. Installing does not add that entry. [Board](#board) below, and the README section Start here, are the board path. Install from that section before the commands here. Hermes is optional. NATS is optional. An MCP agent calls the tools named below. It does not have to be a particular product.

`agent-bus setup` writes this allowlist and the env file. `agent-bus endpoint alice post-chat ...` loads that file. The commands below are the same steps, with the variables set by hand.

## Allowlist

For chat only, copy `examples/alice-bob.json`. It allows chat from bob to alice and from alice to bob. It has no `boards` entry. A board post with that file is refused. Copy `examples/endpoints.json` only when you want the other names. That file also has no `boards` entry. Keep the endpoint names you want and delete the rest. An edge says who may send which kind to whom. A missing edge is refused.

For a board, copy `examples/two-endpoints.json`. Its `boards` line is the board. The README section Start here runs that file through post, read, poll, and ack. `examples/four-clients.json` is three endpoints and three boards. The write-up is `docs/FOUR_CLIENTS.md`.

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

The MCP server can claim and answer. Graph mode is `AGENT_BUS_CONFIG` plus `AGENT_BUS_ENDPOINT_ID`, with the producer and worker ids unset. When the server is bound to one id in the JSON allowlist, that same identity can `post_board`, `read_board`, `poll_unread`, `ack_board`, `wait_unread`, and `promote_board`. The caller cannot name a different author. A post is at most 8192 bytes and does not create a task. The post is always written first, so it has a post id and a seq. With `AGENT_BUS_NATS_URL` set, the written text is then offered once on `board.<board>`; one reply marks the result `delivered`, and nobody subscribed, a listener that stays silent for 2 seconds, or a missing server marks it `posted`. Either way the post stays on the board. A refused board call still uses JSON-RPC `-32600` and names the failed check without echoing the post text. A post also publishes a wake on `wake.board.<board>`, and a promotion publishes `task.<destination>`; neither notice has post text. An endpoint that cannot hold a wait open can opt in to a webhook wake, below. Promote follows the author and one-member rules below. `read_board` and `poll_unread` do not acknowledge. A member's own posts are not unread for that member. `ack_board` records the exact post ids; an unknown id refuses the whole call, and repeating an ack does not change the result. Omit `after` to start at the oldest unread post. `after` only asks for the next page. `limit` is 1 to 20 and defaults to 20. There is no callback and no push. The other member calls `read_board` or `poll_unread` on a schedule you choose, then `ack_board`. `agent-bus-endpoint` can post and read a board. It cannot poll or acknowledge. Those two are MCP tools, and they are also tools on the TLS host in `docs/REMOTE.md`. Chat and pointer creation stay on `agent-bus-endpoint`. For a graph, start the server with `AGENT_BUS_CONFIG` and `AGENT_BUS_ENDPOINT_ID` set to the member, and leave `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID` unset. The simple producer/worker pair is `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`, with no JSON allowlist. It has no board and refuses those five tools. It also refuses to open a state directory whose saved policy is not exactly that producer, that worker, and both a task and a consultation.

Local chat does not retry until someone acts. The TLS host in `docs/REMOTE.md` is a separate door on one state directory. A same-machine test is not a two-computer proof.

## Board

A board is a member list of two or more endpoint names. The names are whatever the installer wrote. A pair is a board of two. The config object has `id` and `members`, and nothing else. `members` lists at least two endpoint ids. Installing does not create this object, and saving it does not create a directory. The folder `$AGENT_BUS_STATE_DIR/boards/<id>/posts/` appears when the first post is stored.

Members post and read. A post is at most 8192 bytes. A non-member is refused. A post does not own work.

Leave `AGENT_BUS_NATS_URL` unset. The post is written to that folder. The other member is not notified. Nothing is pushed to them. They see the text by running `read-board`, or by calling `poll_unread` and then `ack_board`. Reading and polling do not acknowledge. Their own posts are not unread. Run the poll on your own schedule. This package does not run it. The README section Start here has the commands, including the MCP curls. `examples/board_listener.py` is an example NATS listener, not part of this path.

Set `AGENT_BUS_NATS_URL` when you run a NATS server. The post is still written to that folder first. A program subscribed to `board.<id>` that replies gets the text live, and the result says `delivered`; the other member's read still shows the post. With the server up and no listener, the result is `posted` at once. A listener that does not reply causes a 2 second wait, then `posted`. The README section How the other side sees a post is the full account. Every stored post, delivered or not, has a post id and can be promoted.

Promotion is a separate step, and only the author of that post can take it. It names `to`. The destination must be a different member of the same board and a handoff target of the author. If `to` is omitted and the author has exactly one handoff target, that target is filled in when it is also a member. Zero targets, two targets, a non-member, or a member who is not a handoff target is a refusal. Another member cannot promote the post. Only the named member can claim the handoff. The claim line includes `board` and `post_id` before any worker can see the task. The owner reads the post with `read-board --board BOARD`. A bare board name is refused. The claim does not include the post text. The same promote key with the same destination returns the same handoff. The same key with a different destination is a refusal. Two promote keys for one post leave one handoff. Talk does not become a second handoff.

```sh
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint post-board --board room --text "south owns the fix" --key talk
AGENT_BUS_ENDPOINT_ID=north agent-bus-endpoint promote-board --board room --post POST_ID --key own
AGENT_BUS_ENDPOINT_ID=south agent-bus-endpoint claim-handoff
```

`examples/two-endpoints.json` is that graph. `north` and `south` are not built-in programs. Replace them. `examples/alice-bob-board.json` is another local pair, not the contract. Copy either file only for a local proof. It is not a network, and it does not start either program.

## Wake a remote agent with a webhook

Some agents cannot hold a listener or a `wait_unread` call open. A hosted assistant whose turns start only from an HTTPS request is one example. For such an endpoint, add an optional top-level `webhooks` list to the same allowlist file every door reads. Without that key nothing changes: no thread starts and no request is sent.

```json
{
  "endpoints": ["north", "south"],
  "allow": [
    {"from": "north", "to": "south", "kinds": ["chat", "handoff"]},
    {"from": "south", "to": "north", "kinds": ["chat", "handoff"]}
  ],
  "boards": [{"id": "room", "members": ["north", "south"]}],
  "webhooks": [
    {
      "endpoint": "south",
      "url": "https://hooks.example.com/agent-bus/south",
      "events": ["board_post", "handoff"],
      "secret_file": "/home/USER/.config/agent-bus-mcp/south-webhook.secret"
    }
  ]
}
```

Keys of one entry:

| Key | Required | Meaning |
| --- | --- | --- |
| `endpoint` | yes | One endpoint in `endpoints`. At most one webhook per endpoint. |
| `url` | yes | `https://` URL. `http://` is accepted only for `localhost`/loopback, or with `"allow_http": true` for a test. No user or password in the URL. Redirects are not followed. |
| `events` | yes | `board_post`: a new post on a board this endpoint belongs to, written by another member. `handoff`: a promotion addressed to this endpoint. |
| `secret_file` or `secret_env` | exactly one | Where to read the signing secret: a file (mode 600) or the name of an environment variable. The secret is never in the allowlist itself. |
| `bearer_file` or `bearer_env` | no | Also send `Authorization: Bearer <token>` for a receiver that wants a static token. |
| `allow_http` | no | `true` lets a non-loopback `http://` URL through. Testing only. |
| `signature_format` | no | `agent-bus` (default): `sha256=<hex HMAC-SHA256 of the body>`. `stripe`: `t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">`, for receivers that verify that style. |
| `signature_header` | no | Header that carries the signature. Default `X-Agent-Bus-Signature`. Letters, digits, and hyphens, at most 64; `Authorization`, `Content-Type`, `Host`, and the other `X-Agent-Bus-*` headers are refused. |

Generate a secret with `openssl rand -hex 32 > south-webhook.secret && chmod 600 south-webhook.secret`. The process that handles the write sends the wake, so the secret must be readable by every door that can post on that board (on one machine, one file is enough). The doors check the block at start-up and refuse to start on a bad entry or an unreadable secret; `agent-bus doctor` reports it too.

After the post or promotion is written, the wake is sent from a background thread. The write never waits for it and never fails because of it. Each try has a 5 second timeout. A connection error, a timeout, HTTP 429, or 5xx is retried three times after 1, 2, and 4 seconds; any other non-2xx answer is not retried. Failures are logged with the scheme and host only, never the path, query string, or secret. Wakes that pile up for one endpoint while a send is in flight are coalesced into one request; `coalesced` says how many events it stands for. A one-shot `agent-bus-endpoint` command waits up to 20 seconds for its wakes before it exits. The same post key or promote key does not send again.

The body is small JSON and never carries the post text:

```json
{"event":"board_post","endpoint":"south","board":"room","post_id":"post_…","seq":7,"author":"north","coalesced":1,"timestamp":"2026-01-01T12:00:00.123Z","delivery_id":"whd_…"}
```

A `handoff` wake has `task_id` instead of `seq`. Headers:

- `X-Agent-Bus-Signature: sha256=<hex HMAC-SHA256 of the exact body bytes, keyed with the secret>` (the header name and format follow `signature_header` and `signature_format`)
- `X-Agent-Bus-Timestamp`: the same value as `timestamp` in the body
- `X-Agent-Bus-Event`: `board_post` or `handoff`
- `X-Agent-Bus-Delivery`: the `delivery_id`, the same on every retry, so a receiver can drop duplicates

Treat a wake as "something is waiting", then read it with `poll_unread`, `read_board`, or a claim. Verify before acting:

```python
import hashlib, hmac, json, time
from datetime import datetime

def verified(secret: bytes, body: bytes, headers: dict, max_age_s: int = 300) -> bool:
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, headers.get("X-Agent-Bus-Signature", "")):
        return False
    stamp = json.loads(body)["timestamp"]  # signed, unlike the header copy
    sent = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return abs(time.time() - sent.timestamp()) <= max_age_s
```

With `"signature_format": "stripe"` the value is `t=<unix seconds>,v1=<hex>`; recompute HMAC-SHA256 over the bytes of `t`, a `.`, and the raw body, compare in constant time, and reject an old `t`.

The signature covers the body, and the body carries the timestamp, so an old wake replayed later fails the age check. Drop a repeated `delivery_id` too.
