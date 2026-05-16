# clerk

Unified mail + calendar CLI and MCP server. One interface for Microsoft 365 / Outlook.com and Gmail / Google Calendar accounts.

## Features

- **Cross-account search** across Outlook + Gmail in one query, results merged by date.
- **Mail**: search, read, send, reply, delete (trash). Attachments on both send and receive.
- **Calendar**: list, get, create, update, cancel. All-day events. Recurring events via RFC 5545 RRULE.
- **Capability tokens** with per-resource scopes (`mail:read`, `mail:write`, `calendar:read`, `calendar:write`) and a master `global_write_enabled` kill switch.
- **MCP stdio server** exposing every operation as an MCP tool, for use by agent clients (Claude Code, etc.).
- **Direct OAuth** to Microsoft Graph and Gmail/Google Calendar APIs — no dependency on the Outlook desktop app, no TCC re-approval on Outlook updates.

## Install (development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
clerk --version
```

## OAuth setup (one-time)

Both providers require an app registration before the first `auth add`. Quick version:

**Microsoft** — portal.azure.com → Microsoft Entra ID → App registrations → New. Multi-tenant + personal accounts; redirect URI `http://localhost` (Public client/native); enable "Allow public client flows". Add delegated Graph permissions: `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `User.Read`, `offline_access`. Then `clerk config set oauth.microsoft.client_id <id>`.

**Google** — console.cloud.google.com → new project → enable Gmail API + Google Calendar API → OAuth consent screen (External, your account as Test user) → OAuth client ID (Desktop app). Then `clerk config set oauth.google.client_id <id>` and `clerk config set oauth.google.client_secret <secret>`.

## Quick start

```bash
clerk auth add --provider microsoft        # browser OAuth, one per account
clerk auth add --provider google
clerk auth list

# Read
clerk mail search "from:alice subject:budget"          # fans out across all accounts
clerk mail search "from:alice" --include-body          # bodies inline — one call, not search+N reads
clerk mail search "clerk-test" --include-trash         # opt into trashed messages (Gmail)
clerk mail read <id1> <id2> <id3> --account <email>    # batch read, fetched in parallel
clerk mail attachment <message-id> <attachment-id> --account <email> --save ~/Downloads/
clerk calendar list --from today --to +7d              # times shown in local TZ
clerk calendar list --from today --to +7d --utc        # opt into UTC display

# Write — requires `mail:write` / `calendar:write` token and global_write_enabled
clerk mail send --account <email> --to <addr> --subject hi --body "see attached" --attach ~/report.pdf
clerk calendar create --account <email> --title Standup --start 2026-06-01T15:00 --end 2026-06-01T15:30
clerk calendar create --account <email> --title Vacation --start 2026-07-01 --end 2026-07-08 --all-day
clerk calendar create --account <email> --title Weekly --start 2026-06-01T15:00 --end 2026-06-01T15:30 \
    --recurrence "FREQ=WEEKLY;BYDAY=MO;COUNT=10"

# Tokens + safety
clerk token create --scopes mail:read,calendar:read
clerk config set security.global_write_enabled false   # master write kill-switch

# MCP
clerk mcp serve                             # stdio server for an agent client to spawn
```

## Configuration

- Config file (macOS): `~/Library/Application Support/clerk/config.toml`
- Accounts registry: `~/Library/Application Support/clerk/accounts.json`
- OAuth refresh tokens: macOS Keychain (one entry per account, service `clerk`)
- Override the config directory with `$CLERK_CONFIG_DIR`

## Permissions

Every operation passes through three gates:

1. If the op is a **write** and `security.global_write_enabled = false`, the op is denied. Master kill-switch.
2. If `security.require_token = true`, a capability token must be supplied (`--token`, `CLERK_TOKEN` env, or stdin).
3. The token's scopes must cover the requested op (e.g. `mail:write`, `calendar:read`).

Set `security.require_token = false` to disable the token gate entirely — anyone with shell access to this user account gains full permissions. The CLI warns when this is set.

## MCP server

`clerk mcp serve` runs an MCP stdio server with one tool per operation: `accounts_list`, `mail_search`, `mail_read`, `mail_send`, `mail_reply`, `mail_delete`, `mail_get_attachment`, `calendar_list`, `calendar_get`, `calendar_create`, `calendar_update`, `calendar_cancel`.

The capability token comes from `CLERK_TOKEN` in the server's environment. The MCP client never sees it.

Example Claude Code config (`~/.claude/mcp.json` or equivalent):
```json
{
  "mcpServers": {
    "clerk": {
      "command": "/absolute/path/to/.venv/bin/clerk",
      "args": ["mcp", "serve"],
      "env": { "CLERK_TOKEN": "clk_xxx..." }
    }
  }
}
```

If `security.require_token=true` and `CLERK_TOKEN` is unset, the server exits with a clear error before accepting any protocol messages.

## Tests

Three suites, increasing real-world exposure:

```bash
# 1. Unit tests (offline, mocked HTTP via respx). Fast, no side effects.
pytest

# 2. Read-path integration (hits real Graph + Gmail APIs read-only).
CLERK_INTEGRATION=1 pytest tests/integration -v

# 3. Write-path integration (real APIs, mutating). Safety rules: every send
#    targets the configured account's own email; calendar events have no
#    external attendees. A static safety_guard test forbids email literals
#    in the integration test sources to catch future regressions.
CLERK_INTEGRATION=1 CLERK_INTEGRATION_WRITE=1 pytest tests/integration -v
```

Optional coverage report:
```bash
pip install pytest-cov
pytest --cov=src/clerk --cov-report=term-missing
```

## Safety model for writes

- `security.global_write_enabled` is a master kill switch. With it off, every write op denies.
- `mail send` requires a token with `mail:write` scope.
- `calendar create/update/cancel` with no attendees never generates invitation emails (`sendUpdates=none` is passed to Google; Graph events without attendees do not invite anyone). Updates always pass `sendUpdates=none` to avoid spamming attendees.
- `mail delete` moves to Trash / Deleted Items (not permanent). Permanent delete would require broadening Gmail scope to `https://mail.google.com/` — intentionally not requested.
- Mail attachments via `--attach` are sent inline. Microsoft Graph caps inline attachments at 3 MB; larger files raise a clear error rather than silently truncate (no upload-session support in v1).

## Performance: fetching bodies fast

The naive way to read a batch of emails is "search (metadata only), then one read per result" — for an MCP/CLI agent that's 1 + N calls, each paying process spawn + auth + an API round-trip. Two ways to collapse that:

- **`mail search --include-body`** (CLI) / **`mail_search(include_body=True)`** (MCP): returns full message bodies in the search call itself. For Microsoft Graph this is *free* — the list response already carries the body, so there are zero extra API round-trips. For Gmail it still needs one GET per message (API constraint), but those are fanned out in parallel and it's still a single CLI/MCP call instead of N. Attachment metadata is omitted on this fast path; use `mail read` when you need it.
- **Batch `mail read id1 id2 id3`** (CLI) / **`mail_read_batch(message_ids, account)`** (MCP): when you already have ids, fetch them all concurrently in one call.

Rule of thumb: if you're about to read several search hits, pass `--include-body` to the search instead.

## Notes / limitations

- Microsoft Graph KQL search wraps the whole `$search` expression in double quotes and does not support nested quotes; multi-word `subject:` values are sent with KQL parenthesis grouping (`subject:(foo bar)`).
- Gmail's native `attachmentId` is ephemeral — it changes on every message re-fetch. clerk exposes a synthetic stable index (`"0"`, `"1"`, ...) and resolves it to the current native id on download.
- All Graph requests carry `Prefer: IdType="ImmutableId"` so message and event IDs survive folder moves (sent → archive → trash).
- All-day calendar `--end` is exclusive (end date = the day after the last all-day day), matching iCal / Graph / Google semantics.

