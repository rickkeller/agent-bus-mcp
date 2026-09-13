# Agent Bus MCP

Agent Bus MCP is a vendor-agnostic, MIT-licensed reference implementation of a durable local queue behind an authenticated Streamable HTTP MCP bridge. Any MCP-capable assistant can claim one task and then complete or fail that exact lease. The bridge exposes exactly three remote tools: `claim_task`, `complete_task`, and `fail_task`.

Each deployment configures one producer identity and one worker identity. Both use a closed identifier format and are fixed at process startup; they are never accepted from an MCP request. This keeps routing explicit while leaving the project independent of any client or assistant vendor.

## Architecture and boundary

Tasks are local JSON records in an operator-selected state directory. A process lock serializes changes; each record is atomically replaced and directory-synced. An expiring lease identifier fences terminal writes, so an expired worker cannot change a task reclaimed by the configured worker route. Enqueue idempotency is durable.

The bridge accepts only authenticated `POST /mcp`, authenticates before reading the body, limits request size, and defaults to loopback binding. Put an authenticated HTTPS reverse proxy in front of it when remote access is needed. It deliberately has no enqueue, filesystem, shell, or outbound-network tool.

Inputs and returned results have fixed byte bounds. Task references, when used, must pass syntactic HTTPS-hostname validation; that validation is not a trust decision, and workers must still treat fetched content as untrusted. The stateless bridge accepts one JSON-RPC request per POST and explicitly rejects batches with a sanitized invalid-request response.

## Configure and run

Use Python 3.11+. Install the package, copy `.env.example` to a private local configuration file, replace every placeholder, and start `agent-bus-mcp`. The supplied user-systemd examples load that configuration, use a private runtime state directory, and keep the bridge loopback-only. Do not commit the configured file.

Regular assistant products that support remote Streamable HTTP MCP can connect to the proxy endpoint using the configured authorization secret.

## Example workload

`agent-bus-public-research --cadence weekly --state-dir STATE_DIRECTORY --producer-id PRODUCER_ID --worker-id WORKER_ID` is a deterministic example producer, not the identity or purpose of Agent Bus MCP. Its checked-in manifest contains reserved, non-live placeholder references. Replace it only with a reviewed local manifest appropriate to the deployment. Repeating the command in the same UTC bucket returns the same task identifiers.

## Development

Run `python -m pytest`, `python -m compileall -q src tests`, and `systemd-analyze verify systemd/*.service systemd/*.timer` where available. Read [SECURITY.md](SECURITY.md) before deployment.
