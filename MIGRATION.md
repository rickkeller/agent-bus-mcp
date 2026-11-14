# Migration from 0.1.0

0.2.0 is the previous publication. 0.3.0 adds an optional ask tool and does not change the 0.1.0 migration path below. `tools/list` on 0.3.0 has twelve names. The eleventh-name check further down is the 0.2.0 check, not the current list. No upgrade test of a live 0.1.0 directory was run. Treat the notes below as the safe path, not as a proof that every old client still interoperates.

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
- Runtime dependency remains `uvicorn`.
- The loopback MCP server still requires `AGENT_BUS_STATE_DIR`, `AGENT_BUS_BEARER_SECRET`, and `AGENT_BUS_PORT`.
- `AGENT_BUS_BIND_HOST` still accepts only `loopback`.
- The simple pair still uses `AGENT_BUS_PRODUCER_ID` and `AGENT_BUS_WORKER_ID`.
- `claim_task`, `complete_task`, `fail_task`, `claim_question`, and `answer_question` keep the same required arguments.
- Identities remain 1–64 ASCII letters, digits, `_`, and `-`. That rule was already in 0.1.0.

## What changes

- `tools/list` grows from five tools to eleven when the server is in graph mode. The simple pair still exposes the board tools in the list and refuses to run them. Clients that assume the list length is five need to tolerate the added names.
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

A 0.1.0-shaped client was run against a fresh 0.2.0 simple-pair server on loopback. `claim_task`, `complete_task`, `claim_question`, and `answer_question` used the old arguments and succeeded. The ordinary claim did not include `board` or `post_id`. `tools/list` returned eleven names, not five. After that server stopped, a 0.1.0 process opened the same directory and read the completed result and the answer. That was not a pre-existing 0.1.0 directory, not graph mode, and not the TLS door. A two-computer deployment was not rerun. A model was not called. A person who is not the author has not been shown to onboard from these docs alone.
