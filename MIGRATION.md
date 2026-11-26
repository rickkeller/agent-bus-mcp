# Migration from 0.1.0

After 0.4.0, main writes every board post before the NATS offer, so `delivered` now carries a post id and the post stays on the board. Refusals name the failed check after `request refused: `; match that prefix, not the exact old message. `tools/list` gains `wait_unread`, so a current server lists thirteen names. The webhook wake is off unless the allowlist has a `webhooks` block. 0.4.0 offers a board post on NATS before writing it, when `AGENT_BUS_NATS_URL` is set. An answer has no post id there. Leave that variable unset and the 0.3.0 write stays. 0.3.0 adds an optional ask tool and does not change the 0.1.0 migration path below. `tools/list` still has twelve names. The eleventh-name check further down is the 0.2.0 check, not the current list. No upgrade test of a live 0.1.0 directory was run. Treat the notes below as the safe path, not as a proof that every old client still interoperates.

## Pin the old package

The published 0.1.0 baseline is commit `8014257a313e8e5310a524f517976ae13b00acb1`, tagged `v0.1.0`. Do not move that tag, and do not rewrite the old commits.

Pin an install with the tag, or with the commit:

```sh
pip install "git+https://github.com/rickkeller/agent-bus-mcp.git@8014257a313e8e5310a524f517976ae13b00acb1"
```

## Backup

Stop the process that owns the state directory. Copy the whole directory, including `policy.json` if it exists. A partial copy is not a backup. Keep the bearer token and any TLS private keys with the operator who already holds them. Do not commit them.

There is no automatic migration command. Do not delete the old directory as part of upgrading.

## What stays

- Python 3.11 or newer, and the `python3-venv` package on Debian and Ubuntu.
- Runtime dependencies are `uvicorn` and `nats-py`. `nats-py` is the client. This package does not install a NATS server, and a normal board install leaves `AGENT_BUS_NATS_URL` unset. `ask` is refused until that variable is set. A board post is still written as a file.
- The loopback MCP server still requires `AGENT_BUS_STATE_DIR`, `AGENT_BUS_BEARER_SECRET`, and `AGENT_BUS_PORT`.
- `AGENT_BUS_BIND_HOST` still accepts only `loopback`.
- The simple pair still uses `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`.
- `claim_task`, `complete_task`, `fail_task`, `claim_question`, and `answer_question` keep the same required arguments.
- Identities remain 1–64 ASCII letters, digits, `_`, and `-`. That rule was already in 0.1.0.

## What changes

- In 0.2.0, `tools/list` grew from five names to eleven. The simple pair still lists the board tools and refuses to run them. 0.3.0 added `ask`, so a current server lists twelve names in both modes. Clients that assume five names, or eleven, need to tolerate twelve. The check further down that saw eleven names was run against 0.2.0.
- A promoted task's claim can include `board` and `post_id`. An ordinary task from the simple pair does not.
- Graph mode sets `AGENT_BUS_CONFIG` and `AGENT_BUS_ENDPOINT_ID` and leaves the simple-pair ids unset. Setting both modes at once is refused.
- New state appears only when those features are used: board files, enrollment, and remote call records. A 0.1.0 process was not tested against a directory a 0.2.0 process has written.
- The TLS host is a different door. It uses certificates, not the bearer token. Chat and questions are not on that door.

## Rollback

Stop the new process. Move the new state directory aside. Restore the backup taken before the upgrade. Reinstall the pinned 0.1.0 commit. Start the old process against the restored directory.

If the new process already wrote the directory, rolling back the code without restoring the directory is untested. Do not improvise a partial delete.

## Certificates and keys

Generate certificates for a test when that test needs them. Keep each private key on the machine that generated it. Do not copy a key into this repository, a ticket, or a log. Revoke by removing the enrollment row, not by publishing the key.

## Not proven

A 0.1.0-shaped client was run against a fresh 0.2.0 simple-pair server on loopback. `claim_task`, `complete_task`, `claim_question`, and `answer_question` used the old arguments and succeeded. The ordinary claim did not include `board` or `post_id`. `tools/list` returned eleven names, not five. That eleven is the 0.2.0 list. A current server returns twelve, because `ask` was added in 0.3.0. After that server stopped, a 0.1.0 process opened the same directory and read the completed result and the answer. That was not a pre-existing 0.1.0 directory, not graph mode, and not the TLS door. A two-computer deployment was not rerun. A model was not called. A person who is not the author has not been shown to onboard from these docs alone.
