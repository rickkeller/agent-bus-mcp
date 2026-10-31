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

The MCP server is a separate door. It can claim and answer. It cannot create a message. Creation stays on `agent-bus-endpoint`.

This is one machine. The state directory is local. It is not a network between computers, and it does not retry a chat until someone acts.

Jig can file a claimed chat as an ask when its data directory has `bus.json`. That file is documented in the Jig setup notes.
