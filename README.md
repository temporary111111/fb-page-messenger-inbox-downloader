# Messenger Inbox Downloader

Local Facebook **Page** inbox archive. Native Windows desktop app and command-line tools, SQLite, actual attachment files, resumable history import, signed webhooks, automatic catch-up and portable exports. No paid hosting or AI integration.

## Desktop app — everyday use

Double-click **Open Inbox Archive.cmd** in this project folder. It opens the packaged executable when available and passes this directory as the app directory, so your existing `.env`, database and exports are reused. Otherwise it launches the Python source window. No terminals need to remain open.

The direct portable executable is `dist/InboxArchive-<build timestamp>/InboxArchive.exe`. By default it keeps its configuration, `data` and `exports` beside that executable. Copy the **whole build folder**, including `_internal`, to move the program. Never copy only the `.exe`. The root launcher selects the newest build and is the recommended way to use the existing project archive; it does not copy private data into the build.

- **Overview:** latest collection status, message/file counts and Page/tunnel state.
- **Settings:** edit credentials, storage and interval; Test connection checks Page identity and conversation-read access. Advanced holds webhook/API/port/file-limit settings and an optional cloudflared path.
- **Start/Stop sync:** controls collection and the tunnel started by this app.
- **Minimize:** keeps collection running. **Close:** stops owned collection/tunnel processes and exits; it never terminates another terminal app or Cloudflare service.
- **Export archive:** creates a fresh snapshot. **Open folder** opens the completed export in Explorer.
- **Callback URL:** copy the temporary HTTPS callback and verify token for Meta. A newly allocated URL is not yet a connected tunnel. Update Meta manually when the URL changes.

The desktop uses saved local configuration rather than terminal environment overrides. Saving restarts a running collector; an explicitly stopped collector stays stopped. Completing first-time setup starts collection. Secret fields are masked; show/copy are explicit actions. `.env` remains local plaintext, so protect the folder appropriately.

The desktop reuses installed `cloudflared`. If it is not found, choose its executable under Advanced settings or install it using the instructions below. Polling still works without a tunnel. If the CLI is already running, close its terminal app first: the GUI will report the archive/port conflict and will not kill it for you.

Run the source directly with `python desktop_entry.py`. For inspection without starting network work, use `python desktop_entry.py --no-autostart`. `--app-dir PATH` selects an explicit portable configuration root.

## Build the Windows executable

```powershell
.\build-desktop.ps1
```

The build script creates the ignored `.venv`, installs the pinned build-only PyInstaller requirement and produces a **new timestamped folder** under `dist`. It stages compilation in `build` and never replaces an existing portable installation or its credentials/data/exports. The result includes Python and Tcl/Tk; end users do not need Python. Run this build on Windows with a working Tk installation, outside restricted environments that cannot initialize Tcl. Actual credentials, archives and exports are never bundled. Keep the full output folder together.

Unsigned locally built executables may display a Windows publisher prompt; the app does not change Windows security settings or install a background service.

## Quick start on Windows

Open PowerShell in this folder:

```powershell
python -m inbox_archive init
```

Open `.env` in your editor and fill in:

```dotenv
META_PAGE_ID=your-page-id
META_PAGE_ACCESS_TOKEN=your-page-access-token
META_APP_SECRET=your-meta-app-secret
```

`init` generates `WEBHOOK_VERIFY_TOKEN` locally. Keep it private. `.env`, databases, media and exports are excluded from Git. Never paste credentials into chat.

Before configuring Meta, you can exercise storage and export with synthetic data:

```powershell
python -m inbox_archive demo
python -m inbox_archive --data-dir demo-data status
python -m inbox_archive --data-dir demo-data export
```

The demo is kept in `demo-data`, separate from the real `data` archive. A configured real Page ID must not be reused against a demo archive. Each data directory belongs to exactly one Page.

## Meta setup

1. Create or select an app in [Meta for Developers](https://developers.facebook.com/apps/), add the Messenger/Page messaging use case, and connect the business Page.
2. Obtain a **Page access token**, not a user token, from someone with the Page's messaging task. The documented permissions for listing Page conversations are `pages_messaging`, `pages_manage_metadata`, and `pages_read_engagement`.
3. Put the Page ID, Page token and app secret in `.env`. Use the same app for the token, app secret and webhooks.
4. Run one initial import:

   ```powershell
   python -m inbox_archive sync
   ```

5. Start continuous sync:

   ```powershell
   python -m inbox_archive serve
   ```

6. In a second terminal, start the temporary tunnel (instructions below).
7. In the app's Messenger/Page webhook configuration, enter `https://YOUR-TEMPORARY-HOST.trycloudflare.com/webhook` as the callback URL. Use the generated `WEBHOOK_VERIFY_TOKEN` from `.env` as the verify token.
8. Subscribe to `messages`, `message_echoes`, and `message_edits` where available, and **subscribe/install the app on the actual Page**. Configuring a callback on the app alone is insufficient.
9. Send a message to the Page from a test customer and reply through the Page's ordinary inbox. Check `status` for both messages and the webhook timestamp.

Access to real customer data can require App Review/advanced access. Development mode and app roles affect what data Meta permits. Connecting your own Page does not automatically guarantee customer-history access. The app does not bypass Meta permissions. Keep using the Page's normal inbox to reply; this app never sends customer messages or takes over conversation routing.

References: [Page conversations and permissions](https://developers.facebook.com/docs/graph-api/reference/page/conversations/), [Conversation messages](https://developers.facebook.com/docs/graph-api/reference/conversation/messages/), [Message fields and access](https://developers.facebook.com/docs/graph-api/reference/message/), [Page webhook subscriptions](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-pages/), [Page webhook fields](https://developers.facebook.com/docs/graph-api/webhooks/reference/page/).

## Temporary Cloudflare Tunnel

Install `cloudflared` using [Cloudflare's installation guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/). If you use Windows Package Manager:

```powershell
winget install --id Cloudflare.cloudflared --exact
```

Restart the terminal after installation if the command is not found. With `serve` running, open a second PowerShell terminal in this folder:

```powershell
.\tunnel.ps1
```

Or run the command directly:

```powershell
cloudflared tunnel --url http://127.0.0.1:8787
```

Copy the printed HTTPS URL, append `/webhook`, then configure it in Meta. A Quick Tunnel needs no domain or Cloudflare account. **After restarting the tunnel, update the callback URL in Meta.** These tunnels are temporary testing endpoints and have no uptime guarantee. If a pre-existing Cloudflare configuration prevents Quick Tunnel mode, consult the Quick Tunnels guide before changing it.

If `PORT` in `.env` is changed, pass the same port to `tunnel.ps1 -Port 9000` or to the direct command. The tunnel exposes only `/webhook` and `/health`; there is no public route for the database, `.env`, attachments, status or exports. Incoming POSTs require Meta's HMAC-SHA256 signature. `/health` only confirms the listener is alive, not that sync is current.

[Quick Tunnels documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## Everyday commands

```powershell
# Continuous startup catch-up + webhooks + periodic reconciliation
python -m inbox_archive serve

# Local status; also works while serve runs
python -m inbox_archive status

# One import + file download pass (stop serve before doing this)
python -m inbox_archive sync

# Restart pagination; useful after expired cursors, URLs, or suspected missing history
python -m inbox_archive sync --full

# Retry failed or pending file downloads
python -m inbox_archive download

# Snapshot for another tool; generates a new timestamped directory
python -m inbox_archive export

# Explicit destination must not already exist
python -m inbox_archive export --output exports/customer-archive
```

The app permits one sync/downloader process per data directory using an operating-system file lock. `serve` is the usual choice. Read-only status and snapshot exports can run alongside it. SQLite transactions protect committed rows and pagination checkpoints. An interrupted export has no `EXPORT_COMPLETE` marker and must not be treated as ready. An export fails if a file recorded as downloaded is missing; run `download` to recover it before exporting again.

When the PC is off, asleep, offline, or the app is stopped, webhooks cannot reach it. The app checks history again at startup and at `POLL_SECONDS` intervals. This can recover content **still available through Meta's API**; content deleted before it is collected cannot be reconstructed. Cloudflare is a connection to your running PC, not an always-on copy of the app or a message queue.

You can use `start.ps1` as the action in Windows Task Scheduler, with this folder as the working directory, to start the app at sign-in. Start the temporary tunnel separately and update Meta's callback each time its URL changes. The app does not modify sleep settings or install itself as a system service.

## Storage and export

```text
data/
  inbox.sqlite3              conversations, messages, attachments, sync state, raw events
  attachments/               actual downloaded files, named with safe hashes

exports/<timestamp>/
  manifest.json              counts, sync status and completeness caveat
  inbox.sqlite3              consistent database snapshot
  conversations/<hash>.jsonl structured conversation metadata and chronological messages
  conversations/<hash>.txt   readable conversations with attachment paths
  attachments/               copies of actual downloaded files
  webhook-events.jsonl       original received event records
  EXPORT_COMPLETE            only present after export finishes
```

The SQLite snapshot is made through the SQLite backup API; copying just the live `.sqlite3` file while it uses WAL is not a safe backup. An export also copies attachments. It can require roughly another archive's worth of free disk space. Protect backups with the same care as the inbox.

Original API timestamp strings are preserved. Webhook millisecond timestamps are stored as UTC ISO strings with milliseconds. Text export shows the timezone offset rather than silently assuming local time. Original API records and signed-event contents are retained, except credential-bearing pagination fields are scrubbed. Webhook edits retain their original events and update the current text; historical edit revisions before collection are not reconstructable.

Attachment downloads stream to `.part` files, verify response length when available, compute SHA-256 and rename atomically. Pending/failed files retry on later cycles. Missing local files are retried. Expired URLs are refreshed when the containing conversation is fetched again; `sync --full` forces that refresh. `MAX_ATTACHMENT_MB` defaults to 100; oversize or unsupported downloads are reported rather than silently omitted. Only HTTPS URLs on known Meta CDN domains are downloaded; other hosts remain failed for review. No access token is forwarded to attachment hosts. Previews are not silently substituted for original images.

## What “complete” means

`complete=1` means the app exhausted the message pagination returned by Meta for that conversation. It does **not** establish that Meta exposed every message ever visible in Facebook. `last_sync_success` describes the API/message pass, not successful download of every file. Check the separate attachment and unsupported-content counts.

Before relying on the archive, compare your oldest conversation, a long conversation, a recent conversation and image receipts against the Page inbox. Check both incoming and outgoing messages. Also compare done/archived/spam folders: this version discovers conversations through the default Page conversations endpoint, and coverage of those inbox views must be checked on the actual Page. It does not claim to reconstruct deleted/unsent messages or recover attachments that Meta no longer supplies. API-hidden conversations cannot be detected from the API response alone.

New webhook messages use temporary thread IDs until API discovery resolves their actual conversation. These appear as incomplete conversations in status. Raw events are durable before the webhook is acknowledged; media is downloaded by the background worker. API imports refresh changed conversations and periodically rescan old ones to catch changes without an updated timestamp. The app is an archive: it retains previously collected messages if they later disappear from the API.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `META_PAGE_ID` | required | Page owning this archive |
| `META_PAGE_ACCESS_TOKEN` | required | Authorized Page token |
| `META_APP_SECRET` | required | API proof and webhook verification |
| `WEBHOOK_VERIFY_TOKEN` | generated | Callback verification handshake |
| `META_API_VERSION` | `v27.0` | Explicit version; update with Meta's lifecycle |
| `DATA_DIR` | `data` | Database/media location |
| `PORT` | `8787` | Loopback webhook port |
| `POLL_SECONDS` | `300` | Reconciliation interval; webhooks can wake it sooner |
| `FULL_SCAN_HOURS` | `24` | Revisit unchanged conversations after this interval |
| `MAX_ATTACHMENT_MB` | `100` | Per-file limit, adjustable |

Environment variables override `.env`. `.env` is read from the current working directory. Global `--data-dir` goes before the subcommand. Use a separate directory and configuration for each Page. Keep Windows disk access restricted to your account; this app does not encrypt the database at rest.

The code uses indexed SQLite tables, bounded API pages, streamed file downloads, one background worker, and WAL for concurrent reads. It is sized for one local Page archive. Changed conversations scan newest messages until reaching a page already imported from the API; periodic full scans revisit older content. Initial and full imports download attachments after each message page, reducing the delay before collecting expiring media URLs. Very large inboxes still depend on Meta's API rate limits. Additional workers should be added only after measuring a bottleneck.

## Verification and development

```powershell
python -m unittest discover -s tests -v
python -m compileall -q inbox_archive tests
```

Tests use temporary SQLite databases, controlled API responses and a real local HTTP listener. They cover pagination/resume, duplicate inbound/outbound events, thread/media merging, exact timestamps, signature rejection, private routes, attachment retries/truncation, credential scrubbing and portable export. They do not establish live Meta access. No live Page import has been verified without your credentials and permissions.

Code is split by responsibility: `config.py`, `database.py`, `graph.py`, `sync.py`, `media.py`, `webhook.py`, `server.py`, `export.py`, and the CLI in `__main__.py`.
