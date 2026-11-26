# Agent Bus requirements and specification

Lines accepted after the 2026-10-22 list are dated below. This file has two sections. The business section is what must be true. The specification section is how this revision meets it. Code follows the specification. A specification line that does not cite a requirement is not a requirement. A requirement with no specification line is not built.

## Business requirements

Accepted 2026-10-22. Later lines record a later acceptance. They do not rewrite the 2026-10-22 list.

Public wording, 2026-11-20. The house endpoint names are not used in this file. The accepted rules are unchanged. Hermes remains only where it names the optional starter binary.

### Accepted 2026-10-22

BR-1. Agent Bus is a shared communications fabric. It is not one product's app. Any endpoint type uses the same rules. One product may be absent from a path.

BR-2. An endpoint is created with a type. The type says which message kinds it may send, and which other endpoints it may reach. The bus enforces that allowlist. An app does not invent its own door.

BR-3. A message can start from an agent. A human does not have to start it. The proof case is one agent seeing another's security note and asking that agent what version it is on.

BR-4. A task may be requested in either direction when both endpoint types allow that pair and that message kind. The bus does not decide the work. It decides who may send what to whom.

BR-5. Direct is the default. "Must go through a named middle endpoint" is a setting on that pair. It is not the architecture. A required middle hop is the man in the middle this bus is not.

BR-6. Chat and a file handoff are separate capabilities. A file handoff is a pointer to a place both sides can read, such as Drive. The bus does not carry the file bytes.

BR-7. Timers, reminders, schedules, an audit trail, and retry-until-complete belong in the fabric, so every peer gets them. Retry is for work. Ordinary chat is delivered or not. It is not retried until someone acts.

BR-8. Messages are addressed. There is no public bulletin board.

BR-9. This is not a manned push framework. An agent may send without a person in the middle.

Not this: a private tool that only one chat can call. A question that turns itself into a job. A file pasted into the message. One product's names as the only allowed endpoints.

### Later accepted

BR-10. Accepted 2026-11-05. The addressed endpoint owns that handoff. There is no install-wide owner. Zero allowed handoff targets, or more than one when the sender omits the destination, is a refusal. The product does not guess a hidden middle endpoint.

BR-11. Accepted 2026-11-06. A board is a named list of two or more installer-chosen endpoints. A pair is a board of two. A non-member cannot read or post. A post does not own work. This amends BR-8: a member board is allowed. A public room is not.

BR-12. Accepted 2026-11-06. The product is two installer-chosen endpoints, not a house pair. The claim of a promoted post includes the board and the post id. The claimer does not parse the goal and does not open a side file.

BR-13. Accepted 2026-11-06. Only the author can promote a post, and only to one other member who is an allowlisted handoff target. A second owner, a second promote key, a non-author, or a destination outside the board is refused.

BR-14. Accepted 2026-11-06. Each member has an unread view. Reading does not acknowledge. Only an explicit acknowledgement of exact post ids clears a post. Own posts are not unread.

BR-15. Accepted 2026-11-06. A graph-bound member may post, read, and promote through its own door. The door cannot choose another author. A board post does not create work. Promotion is the only step that creates the handoff.

BR-16. Accepted 2026-11-07. One process owns the state directory. Another process may call that host over mutual TLS. The client certificate is one endpoint. The request body cannot choose a different endpoint. A same-machine process test is not a two-computer proof.

BR-17. Accepted 2026-11-07. A connected client may hold a call until this endpoint has something to read, claim, or resume. That hold is a hint. It does not acknowledge, claim, dial out, or start a stopped agent.

BR-18. Accepted 2026-11-07. A separate local process may wait as one endpoint and start only an operator-approved Hermes gateway run. Bus text is not a prompt. This repository does not name a house product.

BR-19. Accepted 2026-11-08. That same process may instead start one documented Hermes foreground turn when the hint says a handoff can be claimed or this endpoint already holds a lease. The turn is `hermes -p PROFILE chat --oneshot --query-file PATH --source tool`. The query file is the operator's. The hint is not copied into it. Unread chat alone does not start the turn. The process still does not claim, acknowledge, or send anywhere but the bus host it was configured to call. A model finishing the handoff is not this requirement.

BR-20. Accepted 2026-11-08. The endpoint that promoted a post may read that task's stored result. Every other endpoint is refused. A task that was not promoted from a board has no promoting endpoint, so that read is refused. A hint that something is ready does not carry the result.

BR-21. Accepted 2026-11-08. A direct connection between two computers may use the carrier-grade range only when the operator explicitly allows that range. That allowance adds no other range. Public addresses stay refused. This repository does not open that listener.

BR-22. Accepted 2026-11-13. An optional local MCP call may ask one question over NATS request/reply. The operator supplies the server. This package does not install that server and does not start a listener. The call does not create a board post, a task, or a consultation. Nobody listening is a result, not a retry.

BR-23. Accepted 2026-11-20. When the operator sets the NATS server, a board post is offered on `board.<board>` before a file is written. The offer carries the text. An answer leaves no post file. Nobody listening, a timeout, or a missing server writes the post and does not offer again. The same key does not offer twice. A promotion still writes the task, then publishes `task.<destination>` without the post text. This package does not install that server and does not start a listener. BR-25 amends this: the post is written before the offer.

BR-24. Accepted 2026-11-24. A member graph endpoint may hold one open call on its own door until it has unread posts, a claimable task, or a lease. The result is a hint: at most 20 board names and two flags, claimable and holding_lease. The hint carries no post text and no post ids, does not read, acknowledge, claim, or answer, and does not count this endpoint's own posts. One parked wait per endpoint, at most 32 per process; a second parked wait for the same endpoint is refused. The deadline is fixed when the call starts; rechecks are at most five seconds and never move it. Disconnect and restart acknowledge nothing. Nothing wakes an endpoint that is not already waiting, and no wake starts a stopped agent.

BR-25. Accepted 2026-11-24. A board post is written before the live offer runs, so an answer no longer leaves the board unwritten; a post a live listener takes is still durable. The result records delivered `live` when a listener answered and `board` otherwise, always with a real post id and seq. The same key returns the saved result and does not offer again. Answering does not acknowledge the answering member; only an explicit acknowledgement of exact post ids clears a post. This amends BR-23.

BR-26. Accepted 2026-11-25. A board post may be 8192 bytes. This amends the 2048-byte post sentence for board posts only. A result, a question, and an answer stay at 2048 bytes. A large artifact still goes in Drive or the repo with a pointer.

BR-27. Accepted 2026-11-25. A refused tool call still uses JSON-RPC error code `-32600`, but the message names the check that failed. It may include a byte count, a limit, a character position, or a field or board name. It does not include secrets or the refused text. The board post cap stays 8192 bytes. A result, a question, and an answer stay at 2048 bytes. Authentication, revocation, a closed TLS wait, unreadable stored state, and unexpected errors stay a bare `request refused`.

BR-28. Accepted 2026-11-25. An endpoint that cannot hold a listener or a wait open may opt in to a webhook wake. The operator names, per endpoint, one HTTPS URL, a signing secret kept outside the allowlist, and the events that wake it: a new board post by another member of a board it belongs to, and a promotion addressed to it. The wake names the event, board, post, sequence or task, and author, and never carries post text. It is signed so the receiver can verify it. It is sent after the write, within seconds, and never delays or fails the write. Without that configuration nothing changes. This amends BR-24's sentence that nothing wakes an endpoint that is not already waiting, for an endpoint that opted in only. It is not a remote creation API: no tool argument can add, aim, or trigger a webhook.

## Technical specification

TS-10 through TS-18 specify the later accepted lines. Installer detail remains in `docs/ENDPOINTS.md`, `docs/REMOTE.md`, `docs/HERMES.md`, and `docs/MULTI_BOX.md`. Those files do not replace this section.

### Identity

TS-1. Cites BR-2 and BR-10. An endpoint id is 1–64 characters from ASCII letters, digits, underscore, and hyphen. On one machine the id is the process environment, `AGENT_BUS_ENDPOINT_ID`. A tool argument cannot name a different author, source, or worker. The allowlist file is the graph. A pair missing from it is refused. Omit the handoff destination only when that sender has exactly one handoff target.

### Local command

TS-2. Cites BR-4, BR-6, and BR-9. `agent-bus-endpoint` sends one chat or one HTTPS pointer on an allowlisted edge. The sender is the process id. The bus does not carry file bytes. The command does not start the other program. `examples/two-endpoints.json` is the generic two-name graph. `examples/alice-bob.json`, `examples/endpoints.json`, and `examples/four-clients.json` are examples, not the contract.

### Board and promote

TS-3. Cites BR-11, BR-12, and BR-13. Members post and read. A post is at most 8192 bytes (BR-26). A non-member is refused. Promotion names one other member. The task key is the board and the post, not the caller key. Two caller keys leave one handoff. The claim includes `board` and `post_id` before a worker can see the task. The claim does not include the post text. The same promote key with a different destination is refused.

TS-4. Cites BR-14. `read_board` and `poll_unread` do not acknowledge. `ack_board` names one to twenty post ids. An unknown id refuses the whole call and changes nothing. A page is at most 20 posts. Omit the page token to start at the oldest unread post. Acknowledging an earlier post cannot hide a later one.

### Doors

TS-5. Cites BR-15. The local MCP server, in graph mode, can `post_board`, `read_board`, `poll_unread`, `ack_board`, and `promote_board` as its bound endpoint. It can `claim_task`, `complete_task`, `fail_task`, `claim_question`, and `answer_question`. It cannot create a chat, a pointer, or an ordinary task. The result read is TS-10. The simple producer/worker pair has no board tools. A task lease is 900 seconds. Complete and fail refuse a stale or mismatched lease.

TS-6. Cites BR-16. `agent-bus-remote` listens on an explicit IPv4 address, default `127.0.0.1`. `0.0.0.0` is refused. Plaintext HTTP is refused. That host is the only writer of the state directory it serves. A client of that host does not write that directory. On one machine, `agent-bus-endpoint` and the MCP server also write the state directory named by `AGENT_BUS_STATE_DIR`. They are not a second writer of a directory that host already owns. Each client certificate fingerprint maps to one endpoint in `enrollment.json`. The host re-reads that file on each call. An invalid enrollment file is not repaired by dropping rows. A remote request id is atomic per endpoint. Repeating it returns the original result. `resume_leases` lists this endpoint's unexpired leases and creates none. Chat and questions are not on this door. The tests run the host and the clients as separate processes on one virtual machine.

TS-7. Cites BR-17. `wait_available` holds an already-open mutual-TLS call for 1 to 25 seconds. The result is `ready` or `timeout`. The hint lists at most 20 board names and two flags, `claimable` and `holding_lease`. It contains no post text, task id, goal, or lease id. The host keeps one parked wait per endpoint and at most 32 for the process. A second parked wait for the same endpoint is refused. Disconnect and restart acknowledge nothing.

TS-8. Cites BR-18. `hermes_adapter` is not part of the host. It waits with one endpoint's certificate and, on a ready hint, starts one argv from the operator's JSON file. The gateway forms are `hermes -p PROFILE gateway run --force` and `hermes gateway run` with no profile. On Hermes v0.23.0 a named profile's `gateway run` without `--force` exits 78. The adapter refuses `chat -q`, `-z`, `gateway start`, `gateway stop`, `gateway restart`, and `--all`. The one accepted turn is TS-9. It does not append hint text, acknowledge, claim, or signal a process it did not start. A revoked certificate does not start the process. No house profile name is stored.

TS-9. Cites BR-19. The turn argv is `hermes -p PROFILE chat --oneshot --query-file PATH --source tool`, with `--profile` or `--profile=PROFILE` in place of `-p PROFILE`. `PATH` is absolute, 4096 characters or fewer, and uses only letters, digits, `/`, `.`, `_`, and `-`. The adapter starts it only when `claimable` or `holding_lease` is true. It requires the path to be a regular file and not a symlink. It does not check the file mode or decide whether the operator trusts the text, and it does not read or write that file. A ready hint that does not start a turn still waits out `backoff_seconds` before the next `wait_available`. A timeout does not add that wait. `max_turns` is 1 to 8 and defaults to 1. `turn_timeout_seconds` is 1 to 300 and defaults to 120. Those two keys are refused on a gateway argv. A non-zero exit counts toward `max_spawn_failures`. A process that is still alive is left running, and a second turn is not started in this process. A second adapter process is outside that guard. Synthetic tests of this argv are not a model acceptance.

TS-10. Cites BR-20. `read_result` is on the local MCP door and the mutual-TLS door. It returns one task's status and stored result only when the caller is the endpoint that promoted the post which created that task. Every other endpoint is refused. A task with no board promotion is refused. `wait_available` still contains no result text. On the mutual-TLS door, repeating the same request id returns the same stored read. A mismatched replay is refused.

TS-11. Cites BR-21. The two-machine helper refuses `100.64.0.0/10` unless `AGENT_BUS_ALLOW_TAILSCALE` is exactly `1`. That opt-in adds no other range. Public addresses, `0.0.0.0`, and hostnames stay refused. TLS verification is unchanged. This specification does not bind that range.

TS-12. Cites BR-22. `ask` is on the local MCP door only. It needs `AGENT_BUS_NATS_URL`. `work` is one token of letters, digits, and hyphens. The subject is `ask.<work>`. One queue group, `agent-bus`, takes each ask. Nobody listening returns `no_responders` at once. A silent listener returns `timeout` at the deadline. An answer returns `answered`. The call writes no board post, task, or consultation. The mutual-TLS host does not expose this tool.

TS-13. Cites BR-23. When `AGENT_BUS_NATS_URL` is set, `post_board` offers the text on `board.<board>`. Nobody subscribed returns at once. The wait of up to 2 seconds applies only when a listener is subscribed and does not reply. An answer returns `delivered`, stores the key, and writes no post file. The same key returns that result and does not offer again. Nobody subscribed, a timeout, or a missing server writes the post and returns `posted`. A promotion writes the task first, then publishes one message on `task.<destination>` that names the task and omits the post text. A missing server does not fail that write. Leave the variable unset and the post is written, as before. The mutual-TLS host uses the same rule when that variable is set in its process. This package does not install the server and does not start a listener. TS-15 replaces the unwritten-on-answer part: the post is now written first.

TS-14. Cites BR-24. The graph MCP door gains `wait_unread`, which mirrors the mutual-TLS door's `wait_available`: timeout_seconds is an integer from 1 to 25 and defaults to 25, board is an optional name filter, and the result is `ready` or `timeout` with the `availability_hint` shape. While parked, the host subscribes to the member's `wake.board.<board>` subjects and to `task.<endpoint>` so a wake returns the call before the next recheck; a missing server, a refused connect, or any subscriber error leaves the call on the five-second recheck, and no NATS error reaches the tool result. A wake is never answered, so a notification listener cannot consume a post. The same write also feeds the same-door in-process bump, unchanged.

TS-15. Cites BR-25. The graph door's post path writes the post, the sequence file, and the key record first, then, when a NATS URL is configured, offers on `board.<board>` with the text and the post id and seq, and then publishes the non-consuming wake on `wake.board.<board>`; the wake watch never subscribes to `board.<board>`, so a parked wait cannot make an offer wait, and it never answers, so no message can consume a post. A live listener that also polls dedupes by post id. The offer deadline stays at most 2 seconds and applies only when somebody is subscribed. `post_board` returns `status=delivered` for a live answer and `status=posted` otherwise; both carry the post id. A saved result from before this change replays as it was stored. The live offer and the wake together hold the record for at most about 3 seconds; a replay of an offering key waits up to 4 seconds for that to settle, and an offering mark older than 4 seconds is what a crash left behind, resolved once to delivered `board`.

TS-16. Cites BR-26. `post_board` text is at most 8192 bytes. The tool description and schema name that same cap. A longer post is refused. Other 2048-byte caps are unchanged.

TS-17. Cites BR-27. `QueueRefused` may carry a reason. The MCP door returns that reason as the JSON-RPC error message and keeps code `-32600`. The TLS door returns the same text in `error` when the refusal came from a tool check, and the remote client prints it. Typical messages are `request refused: text is 9140 bytes, limit is 8192`, `request refused: contains a control character at position 312`, and `request refused: not a member of board pair`. The control-character position is the 0-based index in the Unicode string. A longer board post, a result, question, or answer over 2048 bytes, a control character in an accepted item, a non-member board call, and a bad argument each get their own reason. The caps themselves are unchanged. HTTP 401, HTTP 400 for an oversized body, JSON that cannot be parsed, a corrupt stored record, a revoked or unknown client, and a closed TLS wait stay a bare `request refused`.

TS-18. Cites BR-28. The allowlist may carry a top-level `webhooks` list; an absent key starts no thread and sends nothing. An entry has `endpoint` (a configured endpoint, at most one entry each), `url`, `events` (a non-empty subset of `board_post` and `handoff`), exactly one of `secret_file` or `secret_env`, at most one of `bearer_file` or `bearer_env`, optional boolean `allow_http`, optional `signature_format` (`agent-bus` or `stripe`), and optional `signature_header` (letters, digits, hyphens, at most 64, not `Authorization`, `Content-Type`, `Content-Length`, `Host`, `User-Agent`, or another `X-Agent-Bus-*` header); any other key is refused. The URL is `https`, or `http` on `localhost` or a loopback address, or `http` with `allow_http` true; a URL with a user or password or a fragment is refused. The MCP door and the TLS host refuse to start on a bad block or an unreadable secret, and `agent-bus doctor` reports the block without the URL path or the secret. After a fresh post is written, every `board_post` entry for another member of that board is queued; after a fresh promotion, the destination's `handoff` entry is queued. The same post key or promote key queues nothing. One background thread per target sends the queue; wakes that arrive while it is busy are coalesced into the newest one with `coalesced` set to the count. The body is compact JSON with `event`, `endpoint`, `board`, `post_id`, `author`, `seq` for a post or `task_id` for a handoff, `coalesced`, `timestamp` (UTC, milliseconds), and `delivery_id`, and never the post text. Headers: the signature header (default `X-Agent-Bus-Signature`) carrying, for `agent-bus`, `sha256=` plus the hex HMAC-SHA256 of the body keyed with the secret, or, for `stripe`, `t=<unix seconds>,v1=` plus the hex HMAC-SHA256 of the bytes of t, a `.`, and the body, `X-Agent-Bus-Timestamp`, `X-Agent-Bus-Event`, `X-Agent-Bus-Delivery`, and `Authorization: Bearer` only when a bearer source is set. Each try has a 5-second timeout and does not follow redirects. A connection error, timeout, 429, or 5xx is retried three times after 1, 2, and 4 seconds with the same `delivery_id`; another non-2xx answer is not retried. A failure is logged with the scheme and host only. `agent-bus-endpoint` waits at most 20 seconds for queued wakes before it exits. `uvicorn` and `nats-py` stay the only runtime dependencies.

### Not built

These business lines have no specification line that the code satisfies.

- BR-3 is not done. The first proof is still two peers that are not this package, one agent starting one question, the other answering, with no person, no file, no timer, and no required middle hop. A local command that a person runs is not that proof. This package does not include a client for a particular agent.
- BR-6 is only an HTTPS pointer on the local command. Drive pointers, and any pointer on the remote door, are not built.
- BR-7 is not built. No timer is enabled from this repository. Ordinary chat is not retried. Retry-until-complete for work is not built. The remote contract is at least once, plus idempotent mutations, not exactly once.
- BR-16's same-machine tests are not a two-computer run. The carrier-range allowance is TS-11. Failover, multi-master, a shared disk, and a callback are not built.
- A human has not been shown to onboard from the documentation alone. A passing suite does not prove that.
- BR-18 does not store a profile name. The operator chooses the runtime and the profile. This package does not start that runtime by itself.
- BR-19 starts one documented foreground turn from a handoff hint. The requirement is the argv, not a model. The tests do not call a model. Starting a gateway process is not that turn.
