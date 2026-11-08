# Hermes process adapter

This is an opt-in local process for one runtime. It is not part of the bus host, and it is not the only way an endpoint can use the bus. The host still does not dial out, and it still does not start an agent. No product name is required.

The adapter is the process that stays running. It calls `wait_available` with this endpoint's client certificate while the Hermes process it started is stopped. A hint does not acknowledge or claim. Board names and task text are not a command, and they are not copied into a prompt. When the operator's argv is a gateway, any ready hint starts that process. When the argv is a turn, only a claimable handoff or a lease this endpoint already holds starts it. Unread chat alone does not. The adapter then stops waiting, so it does not hold this endpoint's wait slot at the same time as that process. When a gateway exits, the adapter waits again. A turn is one foreground process that exits; the default is one turn, then the adapter exits too.

Stopping the adapter does not signal the process it started, and it never signals a Hermes process it did not start. Run one adapter per certificate. A second adapter started while a turn is still in flight can start a second turn. This process cannot see a Hermes the operator started some other way.

## What Hermes calls a wake

The Hermes command reference is the source for this boundary: [CLI commands](https://hermes-agent.nousresearch.com/docs/reference/cli-commands).

- `hermes gateway run` is a foreground gateway. It does not by itself run an agent turn. On Hermes v0.23.0 (`2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`) a named profile's `gateway run` exits 78 and does not stay up. The same command with `--force` stays in the foreground and serves only that profile. `hermes gateway run` with no profile is the host gateway: it stays in the foreground and serves every profile in that Hermes home. Do not pass `--force` when a service manager already supervises that profile. Starting this process is not proof that a model claimed work.
- One agent turn is `hermes chat --oneshot --query-file PATH`. The file is read as the prompt. Nothing in it is shell-interpreted. `--oneshot` answers and exits. `--source tool` marks the session as a third-party integration so it stays out of the user session list. This is the turn this adapter can start. It is not a persistent gateway, and it is not a cron job.
- `hermes chat -q` puts the prompt on the command line. `hermes -z` returns only a final answer and is the wrong tool when the agent must call tools. Both are refused. A webhook would be a second HTTP client from this process to Hermes. This adapter does not open one.
- `hermes gateway start` starts an installed service manager and can return while that service keeps running. `gateway stop`, `restart`, and `--all` can stop or interrupt other profiles, including a shared gateway. This adapter refuses them. See [many gateways](https://hermes-agent.nousresearch.com/docs/user-guide/multi-profile-gateways).

The binary is `hermes`, or an absolute path whose last component is `hermes`. The argv is a list in the operator's JSON file. It is not a shell string, and nothing from a hint is appended.

```json
{
  "argv": ["hermes", "-p", "PROFILE", "gateway", "run", "--force"],
  "wait_seconds": 25,
  "backoff_seconds": 5,
  "max_spawn_failures": 3
}
```

A turn uses a different argv. `PATH` is an absolute file of letters, digits, `/`, `.`, `_`, and `-`. The adapter checks that it is a regular file and not a symlink. It does not check the file mode, and it does not decide whether the operator trusts the text. It does not read the file into the hint, and it does not rewrite it. `turn_timeout_seconds` and `max_turns` belong only on this form. A gateway file that sets them is refused.

```json
{
  "argv": ["hermes", "-p", "PROFILE", "chat", "--oneshot", "--query-file", "/PATH/turn.txt", "--source", "tool"],
  "wait_seconds": 25,
  "backoff_seconds": 5,
  "max_spawn_failures": 3,
  "turn_timeout_seconds": 120,
  "max_turns": 1
}
```

The operator writes `turn.txt` before starting the adapter. A usable instruction tells the profile to claim one handoff with its own certificate, read the referenced board post when the claim names one, do that work, and complete with the lease. It does not contain the hint, and it does not grant a blanket approval. The adapter will not fill this file in.

`wait_seconds` is 1 to 25. `backoff_seconds` is 1 to 60. After a ready hint, a refused start, a finished turn, or while the child is still alive, the adapter waits at least that long before another `wait_available`. An unread-only ready hint on the turn form does not start a process and does not acknowledge. It still waits out that backoff, because the hint stays ready. A timeout already spent its wait inside `wait_available` and does not add this one. Stopping the adapter during the backoff returns without starting a process. A claimable handoff or a held lease still starts the turn on that hint, without an extra wait in front of the start. `max_spawn_failures` is 1 to 5. A start that cannot execute, a missing query file, a non-zero turn, or a gateway child that is already gone after the backoff, counts. The process then exits instead of looping. `turn_timeout_seconds` is 1 to 300 and defaults to 120. It is how long this adapter watches one turn. When that time passes and the process is still alive, the adapter does not kill it and does not start another. `max_turns` is 1 to 8 and defaults to 1. A clean exit counts. The default is one turn and then this process exits.

While a turn is alive, a second spawn does not happen in this process. `wait_available` is level-triggered, so a turn that exits without claiming leaves the hint ready. The next allowed turn can claim that same handoff. The bus lease still refuses a second completion of a task that already has a live lease. Two adapters at once are not protected.

Optional `board` limits the wait's unread names, as in `wait_available`. It does not change the argv. Leave it out to watch every board this certificate belongs to.

The same certificate must not be used by another parked `wait_available` while this adapter is waiting. The agent, once running, claims and acknowledges for itself. This adapter does neither.

A revoked certificate ends the adapter. It does not start the process.

## Profile

The Hermes profile docs ([profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)) describe a name the operator chooses. This repository does not record a profile name, a binary path, or whether a service manager already owns that profile. Do not put a guessed name in `argv`.

Before the adapter is aimed at a running installation, the operator needs:

1. The profile name, as `hermes profile list` would show it.
2. Whether that profile should be a standalone `hermes -p NAME gateway run --force`, the host `hermes gateway run` that serves every profile in the home, or a service manager that already owns it. A service-managed gateway should not also be started here. On Hermes v0.23.0, `hermes -p NAME gateway run` without `--force` exits 78.
3. The absolute path of the `hermes` binary, if it is not the `hermes` found on `PATH`.
4. The client certificate directory for that endpoint, created for that endpoint and not shared with a second waiter.

The tests use a synthetic program named `hermes` and the profile token `synthetic`. They do not call a model.

## Gap matrix

Synthetic tests are not a model acceptance. Starting `gateway run` is not a model turn.

| Step | Where it stands |
| --- | --- |
| Ready hint on an open mTLS call | Implemented. The hint has board names and two flags. It has no post text, task id, or lease id. |
| Start a stopped gateway process | Optional argv, `gateway run`. The process stays up. It does not receive the hint as a prompt. That start is not a model execution. |
| Start one Hermes turn from a handoff hint | Optional argv, the documented `chat --oneshot --query-file` form. Unread chat does not start it. The query file is the operator's. |
| Already-running gateway | This adapter does not inject a turn into a gateway it did not start. `chat --oneshot` is a separate foreground process, not a second `gateway run`. |
| Claim, read the referenced post, do the work, complete | The adapter does none of these. The tests in this package do not call a model. |
| Cron or a webhook | Not used. Cron would be a scheduled prompt. A webhook would be another HTTP client. |
| Model | Not chosen by this package. A local model file under 64000 tokens is rejected by Hermes Agent for tool use. This package does not ship a model or a credential. |

Three limits stay in force. A second adapter process can start a second turn. A turn that is still alive when `turn_timeout_seconds` passes is left running. The adapter does not judge whether the operator trusts the query file.
