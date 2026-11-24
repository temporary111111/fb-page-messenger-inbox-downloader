# Operations Runbook

This guide deliberately uses placeholders. Do not put tokens, secrets, customer messages, or attachment paths in documentation.

## Normal everyday use

1. Open `Open Inbox Archive.cmd`.
2. Confirm the configured Page and archive folder in Settings.
3. Use **Start sync**. Minimize keeps it running; Close stops only the worker and tunnel that this desktop app owns.
4. Use **Export archive** when you need a portable snapshot.

The app can poll Meta without Cloudflare. Connect/update the temporary Cloudflare callback only when real-time webhook notification is desired.

## CLI equivalents

```powershell
# Check counts and last recorded errors without modifying the archive
python -m inbox_archive status

# One resumable import pass; do not run while serve or another sync is running
python -m inbox_archive sync

# Revisit all threads and restart message pagination when needed
python -m inbox_archive sync --full

# Continuous sync + local webhook listener
python -m inbox_archive serve

# Retry local attachment downloads
python -m inbox_archive download

# Make a portable snapshot
python -m inbox_archive export
```

## Page migration / changing test Page to real Page

Never point a new Page ID at an existing data directory. Change `DATA_DIR` to a new empty folder, for example `data-page-<new-page-id>`, then run a full sync. The old test archive remains untouched.

## Webhook setup

1. Start continuous collection or the desktop collector.
2. Start Cloudflare Quick Tunnel (or let the desktop collector start it).
3. Put `<temporary-https-url>/webhook` and the local verify token in Meta.
4. Subscribe only to the useful Page fields: `messages`, `message_echoes`, and `message_edits`.
5. Send an incoming and outgoing test message, then verify the archive status.

Quick Tunnel URLs change when restarted. Update the Meta callback after each change. The tunnel cannot receive events while the PC or collector is off.

## Recovery checklist

- **Archive says it belongs to another Page:** do not delete it. Set a new `DATA_DIR` matching the new Page.
- **A thread is incomplete:** run `sync` again. Use `sync --full` if URLs/cursors may be stale. Check the per-conversation `error` field only through local tools; do not publish it.
- **Attachment missing/failed:** run `download`, then `sync --full` if the signed CDN URL has expired.
- **Webhook not arriving:** confirm the app is running, tunnel URL is current in Meta, Page app subscription is installed, and the right fields are toggled.
- **Desktop cannot start while CLI is active:** stop the existing CLI process; the app intentionally refuses to kill processes it does not own.
- **Before trusting an export:** require the `EXPORT_COMPLETE` marker and compare a few old, long, recent, inbound, outbound, and image-bearing threads against the Page inbox.
