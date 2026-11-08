# Remote host

One process on one machine owns the state directory. Other processes, including a later process on another computer, call it over TLS. They do not mount the directory.

This repository's tests run the host and the clients as separate processes on one virtual machine. That is not a two-physical-machine proof.

## What the first slice does

The host checks a client certificate against `enrollment.json`. The fingerprint maps to exactly one endpoint id. The request body cannot choose a different id. A board post still does not create a task. Only the author can promote, and only to one other member. `read_board` and `poll_unread` do not acknowledge. `ack_board` names post ids. `claim_task` with the same `request_id` returns the same lease. `resume_leases` lists this endpoint's unexpired leases after a lost response. `read_result` returns one promoted task's status and stored result, but only to the endpoint that promoted the post which created that task; every other endpoint and every task that was not promoted from a board are refused. Pages stay at most 20 posts. The body limit is 16 KiB and the client timeout defaults to 5 seconds.

The simple local MCP server has the same `read_result` tool with the same promoting-endpoint rule. `agent-bus-endpoint` is unchanged. Chat and questions are not on this door. There is no webhook and no timer.

## Operator files

Use `examples/two-machine/two-machine.sh`. It creates a CA with `basicConstraints=CA:TRUE` and `keyUsage=keyCertSign,cRLSign`, a server leaf with `serverAuth` and the host IP as its subject alternative name, and client leaves with `clientAuth`. The host and the client each keep their own private keys. The client sends a certificate request. The host signs that request and returns only the certificate.

If every caller is suddenly refused, the enrollment file is invalid. `agent-bus-enroll check --file enrollment.json` reports that. `add` and `revoke` do not rewrite an invalid file, and they do not drop a bad row and keep the rest. Move the invalid file aside, then add only the certificates that should still be trusted. A certificate you do not add again stays revoked. One uppercase fingerprint is enough to make the whole file invalid.

```sh
agent-bus-enroll add --file enrollment.json --endpoint ENDPOINT --cert CLIENT.pem
agent-bus-enroll revoke --file enrollment.json --endpoint ENDPOINT
agent-bus-enroll check --file enrollment.json
```

`enrollment.json` mode should be `0600`. Revocation takes effect on the next call. Restarting the host also reads the file.

Python's default SSL context on 3.12 with OpenSSL 3.0.13 leaves `VERIFY_X509_STRICT` unset. `agent-bus-remote` and `agent-bus-remote-call` set that flag on the contexts they build when the interpreter provides it. Hostname and certificate checks stay required. Inspect those contexts. An `openssl x509 -text` reading, or `ssl.create_default_context()` by itself, is not the runtime flag.

Install on each machine with a virtual environment, not a user-site script directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install /path/to/wheels/*.whl
```

On macOS and Linux the environment's programs are `.venv/bin/agent-bus-remote`, `.venv/bin/agent-bus-enroll`, and `.venv/bin/agent-bus-remote-call`. `two-machine.sh install DIR WHEEL_DIR` creates `DIR/venv` the same way. This repository does not document a wheel build. `install` is for a directory of wheels you already have. Otherwise, after `prepare-host`, create the virtual environment from the README inside the host directory:

```sh
python3 -m venv DIR/venv
DIR/venv/bin/python -m pip install -e /path/to/checkout
```

`host` and `sign` use that `DIR/venv/bin` when the programs are there. `csr` only runs `openssl` on the client directory. A client calls `agent-bus-remote-call` from an installed environment with the variables in [Call from a client](#call-from-a-client). The client directory keeps its own key and does not receive the state directory.

`prepare-host` writes `endpoints.json` with the example names `north` and `south`. Replace those names in that file before `csr` and `sign` when the endpoints are not those two. `sign` enrolls the name you pass it, and that name has to be in the allowlist.

If the client reaches the host through an SSH local forward, the server certificate name is `127.0.0.1` and the client URL is `https://127.0.0.1:PORT`. The host still listens only on loopback. Do not also listen on the tailnet address.

## Direct tailnet address

Tailscale addresses are in `100.64.0.0/10`. `prepare-host` and `host` refuse that range unless `AGENT_BUS_ALLOW_TAILSCALE` is exactly `1`. Any other value, including an empty value, leaves the range refused. The opt-in adds no other range. Loopback, `10.0.0.0/8`, `172.16.0.0/12`, and `192.168.0.0/16` stay allowed without it. A public address, `0.0.0.0`, a hostname, and an address just outside `100.64.0.0/10` stay refused with or without it. TLS client certificates, hostname checks, and strict verification are unchanged.

The certificate subject alternative name is the tailnet address. The client URL is `https://HOST_IP:PORT` with that same address. Do not use an SSH local forward for this test, and do not bind `0.0.0.0`. The shell kit is the address gate. This repository does not start that listener.

When the console scripts are not on `PATH`, set `PYTHONPATH` to the checkout's `src` directory and use these modules. `two-machine.sh` uses the same modules when `DIR/venv/bin` is absent:

```sh
python3 -m agent_bus_mcp.remote
python3 -m agent_bus_mcp.enroll
python3 -m agent_bus_mcp.remote_call
python3 -m agent_bus_mcp.endpoint
```

There is no `agent_bus_mcp.remote_host` module. `python3 -m agent_bus_mcp.endpoint --help` prints `request refused` because that command has no help text. That line is not an authentication failure. The local chat commands are `post-chat`, `claim-chat`, `answer-chat`, `read-chat`, and `claim-handoff`.

Chat is not on the TLS door. Run it with `agent_bus_mcp.endpoint` on the host state directory while the listener is stopped. A handoff claim after that chat is null. The host process holds the directory, so do not run the local command while `host` is up. Board post, acknowledgment, promotion, claim, and completion use `agent_bus_mcp.remote_call` as the enrolled certificate. The same claim request id returns the same task and lease. A different request id does not create a second task.

Use a fresh directory and a port you have just checked is closed. Do not reuse a port that is already open. Replace the `north` and `south` allowlist before `csr` and `sign`. Enroll only synthetic endpoint names. Generate the CA and server key on the host. Generate each client key in that client's directory. Copy only the CSR to the host, and copy only `ca.pem` and the signed certificate back. `stop` a second time, after the port is closed, is success. `clean` removes the host directory. Remove the client directories on the machines that hold the keys.

```sh
export PYTHONPATH="/path/to/checkout/src${PYTHONPATH:+:$PYTHONPATH}"
export AGENT_BUS_ALLOW_TAILSCALE=1
python3 -c 'import socket,sys
s=socket.socket(); s.settimeout(0.3)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(0)
else:
    sys.exit(1)
finally:
    s.close()
' "$HOST_IP" "$PORT"
sh examples/two-machine/two-machine.sh prepare-host "$HOST_DIR" "$HOST_IP" "$PORT"
# Replace endpoints.json with the synthetic pair, then:
sh examples/two-machine/two-machine.sh csr "$CLIENT_A" "$ENDPOINT_A"
sh examples/two-machine/two-machine.sh sign "$HOST_DIR" "$ENDPOINT_A" "$CLIENT_A/certs/$ENDPOINT_A.csr"
# Copy only ca.pem and the signed certificate back. Do not copy a private key.
sh examples/two-machine/two-machine.sh host "$HOST_DIR"
sh examples/two-machine/two-machine.sh stop "$HOST_DIR"
sh examples/two-machine/two-machine.sh stop "$HOST_DIR"
sh examples/two-machine/two-machine.sh clean "$HOST_DIR"
```

## Start the host

```sh
export AGENT_BUS_STATE_DIR="$HOME/.local/state/agent-bus-mcp"
export AGENT_BUS_CONFIG="$HOME/.config/agent-bus-mcp/endpoints.json"
export AGENT_BUS_ENROLLMENT="$HOME/.config/agent-bus-mcp/enrollment.json"
export AGENT_BUS_TLS_CERT=server.pem
export AGENT_BUS_TLS_KEY=server.key
export AGENT_BUS_TLS_CLIENT_CA=ca.pem
export AGENT_BUS_REMOTE_HOST=127.0.0.1
export AGENT_BUS_REMOTE_PORT=8443
agent-bus-remote
```

`AGENT_BUS_REMOTE_HOST` defaults to `127.0.0.1`. For an SSH local forward, use `127.0.0.1` so the certificate name and the client URL are the same address. `0.0.0.0` is refused. The process takes the directory lock before it writes `host.pid`. A second `host` leaves that file unchanged and exits. `sh examples/two-machine/two-machine.sh stop DIR` signals the recorded listener process, waits up to five seconds, and checks that the port no longer accepts a connection. It does not signal a process whose command is not this host. A second stop, after the port is closed, is a success. Put a firewall in front of any non-loopback address. Do not publish the port.

## Call from a client

```sh
export AGENT_BUS_REMOTE_URL=https://127.0.0.1:8443
export AGENT_BUS_REMOTE_CA=ca.pem
export AGENT_BUS_CLIENT_CERT=client.pem
export AGENT_BUS_CLIENT_KEY=client.key
agent-bus-remote-call --tool post_board --request-id POST-1 --board room --text "south owns the fix" --key talk
agent-bus-remote-call --tool poll_unread --request-id POLL-1 --board room
agent-bus-remote-call --tool ack_board --request-id ACK-1 --board room --posts POST_ID
agent-bus-remote-call --tool promote_board --request-id OWN-1 --board room --post POST_ID --key own
agent-bus-remote-call --tool claim_task --request-id CLAIM-1
agent-bus-remote-call --tool resume_leases --request-id RESUME-1
agent-bus-remote-call --tool complete_task --request-id DONE-1 --task-id TASK_ID --lease LEASE_ID --result done
agent-bus-remote-call --tool read_result --request-id READ-1 --task-id TASK_ID
```

The URL must be `https`. Repeat the same `--request-id` and the same arguments to recover a lost response. Use a new request id only for a new attempt. If a claim response is lost and the request id is also lost, `resume_leases` returns the lease that is still active for this certificate's endpoint. A repeated `read_result` request id returns the same stored read, so use a new request id to read again after the task changes.

## Connected wait

`wait_available` holds an already-open mutual-TLS call until this certificate's endpoint has something to read, claim, or resume, or until the deadline. It is a hint for a running client. It does not start a stopped agent, wake a sleeping computer, dial out, or accept a callback URL. A separate local process can wait with this certificate and start either an operator-approved Hermes `gateway run` or one documented `chat --oneshot --query-file` turn. See `docs/HERMES.md`. That file does not name a profile. The turn does not copy this hint into the query file.

```sh
agent-bus-remote-call --tool wait_available --request-id WAIT-1 --wait-seconds 25
agent-bus-remote-call --tool wait_available --request-id WAIT-2 --board room --wait-seconds 10 --timeout 20
```

`--wait-seconds` is an integer from 1 to 25 and defaults to 25. `--timeout` is the HTTP client deadline. For this tool it defaults to the wait plus 5 seconds, and it cannot exceed 30. `--board` limits `unread_boards` to that board. `claimable` and `holding_lease` stay endpoint-wide, because they are this member's own tasks, including a task that is not on that board. A non-member board refuses the call and returns no names or flags.

The result is `ready` or `timeout`. `unread_boards` lists at most 20 board names, oldest unread first, using the same rule as `poll_unread`: another member's post that this member has not acknowledged. Own posts are absent. The hint contains no post text, task id, goal, or lease id. `claimable` means a pending task, or a leased task whose lease has expired. `holding_lease` means an unexpired lease. The call does not acknowledge, claim, or store a `remote_calls` result, so the same request id can wait again.

This is level-triggered. While the unread post or the lease remains, every call returns `ready` immediately. Acknowledge, claim, or resume before waiting again, or the client will spin. A timeout writes nothing. Disconnect and host restart acknowledge nothing. After restart, a new call sees the durable unread posts and leases that are still there.

The host keeps one parked wait per endpoint and at most 32 for the process (`AGENT_BUS_WAIT_CAP` may lower that, not raise it). A call that is already `ready` does not take a slot, and a parked wait does not hold the queue lock, so post, poll, ack, and claim continue. A second parked wait for the same endpoint is refused. That refusal does not extend the parked call. Retry it with backoff; the parked call still ends at its own deadline.

The deadline is fixed when the call starts. The parked thread then sleeps for at most five seconds at a time, and for less when that would pass the deadline. Those sleeps are not restarted from the moment of a disconnect, and they do not move the deadline. At the end of each sleep the host checks enrollment and whether the TLS peer has closed. A detected close releases the slot and returns the generic refusal, with no board names or flags. The release runs before that response is written, including when the call fails. If the close is not visible on that look, the slot remains until a later look or the original deadline, whichever comes first. The hard bound is the original deadline. Five seconds is the maximum server sleep between looks, not a promise that another machine's clock will see the slot free within five seconds of the kill. Connecting again, and that new call's own `--wait-seconds`, are outside the sleep. A lease that expires during a sleep becomes `claimable` on the next look, without a background sweeper. Revocation is read on the same looks; a revoked caller gets the generic refusal and no hint.

## Before a real two-machine test

A same-machine test is not a two-computer proof. This checklist does not start a listener by being read.

1. Run the host on one computer and a client on a second computer, not two processes on one VM.
2. Use certificates you generated for that test. Do not reuse a lab CA.
3. Confirm a revoked certificate fails and the other endpoint still works.
4. Confirm a repeated claim request id does not create a second task.
5. Confirm the state directory is not mounted on the client.
6. Do not point a live worker at this host until that test is reviewed.

Failover and a second writer on another disk are not part of this slice. `wait_available` notifies a connected client. It does not start a Hermes process. `docs/HERMES.md` describes the separate local adapter. A gateway argv starts `hermes -p PROFILE gateway run`. That process staying up is not a model execution. A turn argv starts one `chat --oneshot` from an operator query file when the hint is a handoff. Neither form is the host. The Hermes adapter is one optional runtime, not the only endpoint.
