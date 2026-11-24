# Architecture

## Purpose

This is a local, single-Page Facebook Messenger archive. It stores conversations, messages, webhook events, and available attachment files in a local SQLite archive so the owner can later export it to another system. It does not send Messenger messages and does not include an AI integration.

## Data flow

```text
Manual / periodic reconciliation
  Local app ──Graph API──> Meta Page conversations + thread messages + attachment URLs
       │
       └──> SQLite + attachment files

Real-time wake-up (optional)
  Meta Page ──HTTPS webhook──> Cloudflare Tunnel ──> local webhook listener
       │                                               │
       └───────────────────────────────────────────────┘
                         wakes the next Graph API sync

Export
  SQLite + local files ──> timestamped portable snapshot for an external tool
```

Cloudflare is **not** needed for historical or periodic Graph API import. It is only needed for Meta to initiate a real-time webhook call to the running PC.

## Main modules

| Module | Responsibility |
| --- | --- |
| `config.py` | `.env` parsing and settings validation |
| `database.py` | SQLite schema, page binding, checkpoints, archive locking, status |
| `graph.py` | authenticated Graph API requests, trusted pagination links, retries |
| `sync.py` | conversation discovery, message pagination, checkpoint/resume logic |
| `media.py` | safe streamed attachment download and retry |
| `webhook.py` | signed Meta event validation and durable event/message ingestion |
| `server.py` | loopback-only webhook listener |
| `export.py` | consistent SQLite backup, readable JSONL/text, copied attachment snapshot |
| `desktop*.py` / `desktop_entry.py` | native Tk desktop UI, worker lifecycle and portable configuration |

## Archive layout

```text
<DATA_DIR>/
  inbox.sqlite3        SQLite archive
  attachments/         hashed actual downloaded files
  .sync.lock           exclusive writer lock

exports/<timestamp>/
  inbox.sqlite3        safe SQLite snapshot
  conversations/       JSONL and readable text exports
  attachments/         copied files
  webhook-events.jsonl
  manifest.json
  EXPORT_COMPLETE      created only after a successful export
```

## Correctness invariants

- A data directory is permanently bound to exactly one Meta Page ID.
- Conversations are discovered through every returned `paging.next` page before thread message imports begin.
- Each message page and its next cursor commit together. An interruption resumes without skipping a committed page.
- `complete=1` means that the API pagination returned no next cursor for that thread; it does not prove Meta exposed all historic data.
- Original API pagination URLs are scrubbed of credentials before persistence.
- Attachment URLs must be HTTPS Meta CDN URLs and downloads are written atomically.
- Webhook POSTs require Meta's HMAC signature. The public listener exposes only `/webhook` and `/health`.
