# Multi-computer transport

Status: the first slice is a TLS host on one machine. Failover, multi-master replication, shared disks, and callbacks are still not implemented. A same-VM process test is not a two-machine proof.

## First slice choices

The open choices in this design are closed for the first slice as follows.

- **Credential:** a client certificate signed by an operator CA. The host stores only the SHA-256 fingerprint, mapped to one endpoint, in an enrollment file. There is no shared bearer and no new cryptographic library. Private keys stay on the client.
- **Transport:** TLS 1.2 or newer. Plaintext HTTP is refused, including on loopback. The listening address is an explicit IPv4 address, default `127.0.0.1`. `0.0.0.0` is refused. The helper allows loopback, `10.0.0.0/8`, `172.16.0.0/12`, and `192.168.0.0/16`. It allows Tailscale's `100.64.0.0/10` only when `AGENT_BUS_ALLOW_TAILSCALE=1`. That opt-in allows no other address and does not change TLS. A public address stays refused. Setting the variable does not start a listener.
- **Tools:** `post_board`, `read_board`, `poll_unread`, `ack_board`, `promote_board`, `claim_task`, `complete_task`, `fail_task`, `read_result`, `resume_leases`, and `wait_available`. Consultations stay on the local door. Enrollment is the local `agent-bus-enroll` command, not a remote tool. `wait_available` holds a connected client's call. It does not start a stopped process.
- **Result read:** `read_result` returns one promoted task's status and stored result to the endpoint that promoted the post which created that task. Every other endpoint is refused, and a task that was not promoted from a board has no promoting endpoint, so that read is refused. The result text is not part of `wait_available`.
- **Lost claim:** `claim_task` is stored under the endpoint and the client request id. A retry returns that result and does not take a second task. `resume_leases` lists this endpoint's unexpired leases and creates none.
- **One writer:** the host process holds an exclusive lock for its lifetime. A second process on that directory exits. This is not a failover fence across two machines.
- **Revocation:** the host re-reads the enrollment file on each call. Removing a fingerprint refuses that certificate. A restarted process sees the same file.

The local contract this design must preserve is the graph on one machine: an endpoint id is chosen by the process, not by a tool argument; a board is a member list; a post is not work; only the author promotes, and only to one other member; `read_board` and `poll_unread` do not acknowledge; `ack_board` names exact post ids; own posts are not unread; a page is at most 20 posts; task leases stay fenced. The TLS host and client now exist. A same-VM test is not a two-machine proof. Failover across two machines is not built.

## Recommendation

Run one authoritative state host. Other computers are clients of that host. Do not share the state directory over a network filesystem, and do not start with multi-master replication.

The host keeps today's `DurableQueue`: one flock, atomic JSON replacement, and fsync. Clients never write that directory. A later read-only copy may exist for inspection, and it must not ack, promote, claim, or finish.

This is alternative B below. Alternatives A, C, and D are rejected for the first remote stage. Alternative E stays deferred.

## Authenticated endpoint identity

On one machine the endpoint id is the process environment. That is not an authentication boundary once a packet can arrive from another computer.

The host binds a credential to exactly one endpoint id before it constructs the queue object for that call:

- The allowlist gains a public key per endpoint. The private key stays off the host and out of git.
- The signature or client certificate is checked before the body is treated as a command.
- The endpoint id comes from that credential. A body field named endpoint, author, source, or worker is ignored and, if present, refused.
- One shared bearer for the whole bus is not the endpoint identity. Whoever holds it could act as every member.
- The existing MCP bearer can remain the secret for a local process that is already bound to one endpoint. It is not the credential a remote machine uses to choose an id.
- Removing a member means removing that public key. The host then refuses the credential. Old ack files stay stored under the endpoint id, so a later re-add does not mark every old post unread again. An operator who wants a fresh unread view deletes that ack file on purpose.

The host clock is the only clock for leases and expiry. A client-supplied timestamp is not authority.

## Authoritative state

Every mutation and every claim runs on the primary, under the existing queue lock. The primary is the only process that opens the state directory for write.

The durable objects that must move together in a backup are the tasks, consultations, idempotency directories, promotion records, board posts, sequence counter, and per-member ack files. Restoring tasks without their acks, or acks without the sequence counter, is not a supported restore.

Sequence numbers are assigned on the primary when the post is stored. Arrival order of network packets is not the board order. A post created later receives a higher sequence. An acknowledgement of an earlier post cannot hide it.

Failover of the primary to another machine is not part of the first remote stage. Two hosts must not both believe they hold the lock. A network filesystem lock is not the proof that prevents that.

## Retry and idempotency

The remote contract is at least once, plus idempotent mutations. It is not exactly once.

These operations already have a domain key and must return the original result when repeated:

- `post_board` with the same endpoint and key returns the original post and does not create a second post or a task.
- `promote_board` with the same key and the same destination returns the original handoff. The same key with a different destination stays a refusal.
- `ack_board` of an id that is already acknowledged is a stable success. A call that names any unknown id changes nothing.

A lost response is the same as a retry. The client repeats the same key. The host must not treat a timeout as a reason to invent a second post, handoff, or ack record.

`claim_task` is different. The lease is created before the client is sure it saw the response. The first remote stage needs a bound-endpoint resume: the host returns that endpoint's still-active leases for tasks and consultations. Resume does not create a second lease and does not hand the lease to a different endpoint. Complete and fail keep today's fence: a stale or mismatched lease is refused.

Clients persist the idempotency key before they send, and they persist a lease after a response or a resume. A process restart is not a new logical request.

## Reconnect and ordering

A session is not a cursor. Disconnect does not acknowledge anything.

On reconnect the client polls with `after` omitted. The oldest unacknowledged post from another member is still there. Posts the client has acknowledged stay acknowledged. Own posts stay out of the unread page. `after` is only the next page token inside that unread view. Using `after` without an ack must not be the way a post disappears.

Board order is the primary's sequence. Task order is not a global stream. Each handoff has one destination member. Reconnect resumes that member's lease instead of claiming the same task again for someone else.

A connected client can hold `wait_available` instead of sleeping between polls. The result is a level-triggered hint: board names, whether this endpoint can claim, and whether it already holds a lease. It does not acknowledge. A client that is not running gets nothing, because the host does not dial out, store a callback, or start that process. Starting a stopped agent or a sleeping computer is a runtime adapter outside this repository. The adapter only decides whether its own endpoint process is running; once that process is up, it is the client above. This repository still does not enable a timer, a webhook, or a vendor push.

## Threat model

| Actor | What they can do | What the design refuses |
| --- | --- | --- |
| Network observer | See ciphertext only | Cleartext host, bearer, post text, or keys |
| Network rewriter | Drop, delay, or duplicate packets | A duplicate mutation creating a second post, handoff, or lease |
| Holder of one endpoint key | Post, read, poll, ack, and promote as that member | Acting as another member, or promoting a post they did not author |
| Holder of a local MCP bearer | The one endpoint that process was bound to | Choosing a different id in the body |
| Operator of the primary | Read and restore the state directory | Treating a partial restore as consistent |
| Second host process | Try to serve the same directory | Becoming a writer without the single primary lock |

Refusals stay generic. Logs omit tokens, keys, post text, and task bodies. Page size, body size, and the existing text caps stay in force so one member cannot stream an unbounded board. The host is not a public internet service. A TLS reverse proxy in front of a loopback port is the current local rule; a remote listener is a later stage and is still not opened by this repository.

Split brain is the failure this design refuses to paper over. If the primary's disk is copied to a second machine and both serve, two members can promote the same post or complete the same lease. The first remote stage has one writer. A fencing token for failover is future work, and it is not invented here.

## Compatibility

Local mode stays. One state directory, one machine, and the current MCP and `agent-bus-endpoint` commands keep working with no network.

The simple producer/worker pair stays a local pair with no board. A remote client does not gain a board by presenting that pair.

On-disk records stay the records the queue already writes. The remote stage calls the same functions. It does not add a second schema for posts, acks, or tasks.

The stateless JSON-RPC tool schemas are the command set. What changes remotely is the authentication in front of them: the host selects the bound queue from the credential, then dispatches. The TLS host is that process: one certificate selects one bound queue, and one process can serve many endpoints. The local MCP process is still one endpoint.

An endpoint id string in the allowlist remains 1–64 ASCII letters, digits, `_`, or `-`.

## Operational requirements

- One primary host and one state directory on its local disk.
- Backups copy the whole directory. A restore replaces the whole directory. Mixing a new task file into an old ack directory is not a restore.
- Public keys are enrolled out of band. Private keys and bearers are not committed.
- The host clock is synchronized enough for lease expiry. Clients do not vote on the time.
- Disk full fails the write and leaves the previous durable record. Clients retry idempotent calls after the operator frees space.
- Membership changes are edits to the allowlist on the host, then a refusal of any credential no longer listed.
- No timer, no second Jig process, and no live worker cutover are started from this repository as part of adopting the design.
- The first remote listener, when it exists, is a separate process an operator reviews. This document does not add a unit file and does not enable one.

## Alternatives

**A. Network filesystem.** Rejected. The safety proof is a local flock around atomic replace. A cross-host file lock is a weaker and easier-to-split lock, and every client would be a writer.

**B. One primary, authenticated clients, same queue.** Recommended. It keeps one owner for a promotion, one lease fence, and one sequence. The new work is authentication and a resume call, not a new consensus system.

**C. Multi-master replication.** Deferred. Acknowledgements of distinct posts can merge, but a single destination for one post and a single live lease cannot be chosen twice. That needs a leader anyway. Building it before one primary works adds split-brain cases the product is not ready to operate.

**D. Pairwise mail or shared drives.** Rejected as the bus. They do not give one sequence, one ack set, or one promotion.

**E. An external log or broker as the source of truth.** Deferred, and not as the authority. A broker might later notify a client to poll. It must not store the ack or decide the handoff. Otherwise there are two authorities.

## Staged tests

Items 1 through 6 are in the automated suite, including `tests/test_remote.py`. Item 7, a whole-directory restore, is not a test here. Item 8 is the host clock: the suite expires leases from the stored stamp, and a client does not set that stamp. This list is not a two-computer run. The automated suite runs on one machine.

1. Keep the current local suite green, including member isolation, author-only promote, and explicit ack.
2. Two credentials against one host: each can post only as itself; a non-member is refused; a body that names the other endpoint is refused.
3. Drop the response to `post_board` and retry the same key. One post exists.
4. Drop the response to `claim_task`, resume, and receive the same lease. The other endpoint cannot finish it.
5. Acknowledge one post, disconnect, and poll. That post stays acknowledged and an unacknowledged post is still returned. An own post is absent for its author and present for the other member.
6. Start a second writer on the same directory. It refuses while the primary holds the directory.
7. Restore a whole-directory backup. Sequence numbers, acks, and the single handoff match the backup. A partial copy is not a test success.
8. Send a client clock and a stale lease. Expiry and completion follow the host clock and the stored fence.

## Remaining decisions

The first slice later chose TLS client certificates and the remote tools named above. The questions below are the original open list from this design. They are not a second undecided choice.

- The concrete credential: TLS client certificates, or request signatures over the same JSON body. Both can bind one public key to one endpoint. The choice is an operator and library decision for the implementation change.
- Whether the first remote slice exposes every current tool or only board poll, ack, post, promote, claim, and finish.
- How a replaced primary proves it is the only writer. That is a fencing design, and it waits until one primary is in use.
- How long old posts and ack ids are retained.
- How an installed program on another computer enrolls its key. Enrollment is out of band for the first remote stage.

This design page did not itself open a socket. The host that later implemented the first slice is `agent-bus-remote`, documented in `docs/REMOTE.md`. This page still does not enable a timer or a second writer.
