# Agent Bus requirements

Accepted 2026-10-22. Canonical human copy: Obsidian `10-Projects/Agent Bus/Requirements.md`.

## Outcome

Agent Bus is a shared communications fabric. It is not an Otto app. OpenClaw, grokbot, Dots, and any other endpoint use the same rules. Otto may be absent from a path.

## Requirements

1. An endpoint is created with a type. The type says which message kinds it may send, and which other endpoints it may reach. The bus enforces that allowlist. An app does not invent its own door.
2. A message can start from an agent. A human does not have to start it. The proof case is grokbot seeing an OpenClaw security note and asking OpenClaw what version it is on.
3. A task may be requested in either direction when both endpoint types allow that pair and that message kind. The bus does not decide the work. It decides who may send what to whom.
4. Direct is the default. "Must go through Core" is a setting on that pair. It is not the architecture. A required middle hop is the man in the middle this bus is not.
5. Chat and a file handoff are separate capabilities. A file handoff is a pointer to a place both sides can read, such as Drive. The bus does not carry the file bytes.
6. Timers, reminders, schedules, an audit trail, and retry-until-complete belong in the fabric, so every peer gets them. Retry is for work. Ordinary chat is delivered or not. It is not retried until someone acts.
7. Messages are addressed. There is no shared bulletin board.
8. This is not a manned push framework. An agent may send without a person in the middle.

## Not this

- A private tool that only one chat can call.
- A question that turns itself into a job.
- A file pasted into the message.
- Otto-only names as the only allowed endpoints.

## First proof, not started

Two non-Otto peers. One agent starts one question. The other answers. No human starts it. No file, no timer, and no Core hop in that first proof.

## Already built, not the first proof

A local command, `agent-bus-endpoint`, can send one chat or one HTTPS pointer on an allowlisted edge. The sender is the process id. See `docs/ENDPOINTS.md`. Jig can file a claimed chat as an ask when `bus.json` is present. That is not the first proof. The other program still needs its own client, or a person runs the command. No timer, no retry, and no live pair has been started.
