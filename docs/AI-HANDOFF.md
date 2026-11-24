# AI Handoff — Current Project State

Update this document after material changes. It contains no credentials, customer data, or full conversation identifiers.

## Goal

Provide a zero-hosting-cost, local Windows archive for one Facebook business Page inbox: complete available conversation history, message timestamps, actual attachment files, automatic catch-up, webhook-assisted updates, and portable exports for a future external AI.

## User decisions already made

- Scope is archive/sync/export only; do not build the external AI integration.
- Primary deployment is a portable Windows desktop app. Minimize continues collection; closing the UI stops app-owned collection and tunnel processes.
- PC is the server. Cloudflare Quick Tunnel is optional for real-time webhooks; no custom domain has been chosen.
- User will perform UI testing to conserve AI usage. Do not use UI automation unless specifically asked.
- The Page in scope is configured locally. Do not reproduce the Page ID, token, or customer content in future replies unless the user explicitly supplies it again.

## Completed implementation

- Core local archive: SQLite, conversation/message/attachment storage, signed webhooks, export, CLI, resume checkpoints, writer lock, attachment safety checks.
- Native desktop UI: settings, start/stop, tunnel status, export, portable launcher/build scripts.
- Conversation discovery now follows every available Page-conversation pagination page **before** importing message history. This avoids a slow thread preventing discovery of later conversations.
- Real Page discovery was checked read-only and returned 329 conversations across 4 API pages. A separate real-Page data directory was created; the earlier temporary Page archive was preserved.
- A 5-message history page size is used because continuation pages with full attachment metadata can stall at larger sizes. On Windows, Graph API calls use the built-in `curl.exe` through private stdin (not command-line credentials) so an OS-level time limit can recover from Python SSL read stalls. Pagination still continues until Meta provides no next cursor.
- Commits already present: `ad85a6e`, `079a752`, `32d7d56`, `ad4166f`, `f102f61`, `eb84a4c`, `c58670f`.

## Current live/archive state

- Real Page archive directory is local and Git-ignored. Do not name it or inspect customer rows in chat output.
- All 329 conversation records were discovered and persisted.
- Initial message import began successfully. The real archive has all conversation records and some committed historical message/file pages; remaining threads are resumable. A few historic cursors can be slow at Meta, so controlled 5-message pages and bounded Windows requests are used.
- No token/permission rejection was observed in the successful discovery and small message probes.
- A diagnostic sync with a delayed local stack dump was started just before a previous interruption. Treat any prior process as unknown; first inspect writer lock/status safely before starting another sync.

## Uncommitted work at handoff

The working tree may contain two changes not yet committed:

1. Graph API Windows transport, small controlled continuation pages, and related unit tests need a final commit.

Focused archive, server, settings, runtime tests passed before that change; archive tests passed after the 25-message page change. Re-run the full suite after deciding the final stall-handling design.

## Known desktop packaging blocker

- The local Python Tk/Tcl installation currently fails to initialize `tkinter.Tcl()` even though its files exist.
- PyInstaller therefore warned that tkinter was excluded. Do **not** publish a build made in this condition.
- An older portable build exists but does not include the newest archive reliability changes. Do not claim it is the current release.
- The next fix should repair/reinstall the normal Windows Python Tcl/Tk component or use a known-good Python distribution, then rerun the packaged app smoke test in an isolated synthetic app directory.

## Recommended next steps

1. Run a long real-archive sync and verify counts progress across multiple threads.
2. Repair Tk/Tcl locally, rebuild the desktop app, and have the user test the UI.
3. Commit any further reliability changes only after tests and a data-safe staging check.

## Commands safe to start with

```powershell
git status --short
python -m inbox_archive status
python -m unittest discover -s tests -q
python -m compileall -q inbox_archive desktop_entry.py tests
```

Read `AGENTS.md` and `docs/OPERATIONS.md` before running a live sync, export, tunnel, build, or any destructive action.
