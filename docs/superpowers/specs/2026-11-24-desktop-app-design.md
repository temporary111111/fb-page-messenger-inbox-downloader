# Inbox Archive desktop app

Status: layout approved; implementation design ready for user review.

## Purpose

Make the existing local Facebook Page archive comfortable to operate every day without editing `.env` or keeping two terminals open. The owner uses Windows, wants no paid hosting or domain, and has approved a compact desktop utility layout. The current CLI archive is checkpointed in this directory's own repository at `ad85a6e`.

Success means the owner double-clicks an executable, sees whether collection is working, edits configuration through a form, and exports an archive through a button. Minimize keeps it running; closing its window stops its owned background work.

## Agreed requirements

- Windows desktop app distributed as a portable folder containing `InboxArchive.exe`.
- Reuse the existing SQLite archive, actual attachment storage, Meta API client, signed webhook handler and portable export format.
- Start configured sync automatically on opening. Start a temporary Cloudflare Tunnel automatically when `cloudflared` is available.
- Close stops the sync worker and tunnel started by this app. Minimize leaves them running. No system tray mode or Windows service.
- A clean utility interface: few controls, short labels, no article-like instructions or busy dashboard.
- Settings, connection validation, current status, recent activity, export and access to export folders.
- Keep the current terminal commands working.
- No AI integration, customer message sending or paid services in this change.

## Approved visual reference

`design-previews/inbox-archive-desktop.html` is the approved interactive layout reference. It contains sample data only. Implement its hierarchy, spacing, neutral surfaces, restrained blue accent and small status indicators in a native Tkinter/ttk window; it is not the production frontend. Use Segoe UI, ordinary Windows window controls and resizable layouts. Support keyboard operation and normal Windows display scaling.

The main window contains Overview, one sync status area, three archive counts, Page/tunnel status, a collapsed callback address, Export archive, Start/Stop sync, collapsed recent activity and a quiet footer. Settings is a separate view in the same window with Back/Cancel/Save. Advanced fields stay collapsed by default. Use native file/folder dialogs.

Keep exports and status usable during a connection failure. Show concise errors and an actionable recovery control. Technical details belong in collapsed recent activity or a local log, never raw tracebacks in a dialog. Display local time with a clear timezone where useful; retain stored/exported timestamps unchanged.

## Approaches considered

1. **Native desktop GUI and portable executable — selected.** Fits Windows minimize/close behavior, reuses Python and avoids a separate browser lifecycle. Tkinter/ttk adds no runtime Python package requirement to the source app. PyInstaller bundles the runtime for the executable.
2. **Local web UI and launcher.** More flexible web styling, but closing a browser tab does not naturally stop the server and tunnel. Requires additional lifecycle controls. Rejected for this owner's intended operation.

Start with a one-folder PyInstaller build. A single-file executable or installer is not needed to deliver double-click operation and would add packaging work. Packaging and GUI dependencies are build-time concerns; ordinary operation has no Python installation requirement after packaging.

## Startup and ownership

1. Resolve the application directory: executable directory in a packaged build, repository directory in source mode. Never select archives based on the shell's working directory.
2. Load configuration and show the GUI immediately. Missing or invalid configuration opens Settings; the user can correct it without a terminal.
3. With valid local configuration, start one owned archive worker. That process owns the existing archive writer lock, loopback webhook listener and sync/download work. Network failures become visible retry states rather than preventing the GUI from opening.
4. Discover `cloudflared` from a bundled `bin/cloudflared.exe` or its installed PATH location. Start it as a hidden owned child process using the configured loopback port. Capture its temporary HTTPS URL. If unavailable, offer a native executable-file picker and a short installation-help link. Periodic API sync must still work without a tunnel.
5. Show the callback URL only after it is known. A printed URL means allocated, not connected; distinguish Starting/Connected/Disconnected from tunnel logs, and show webhook receipt separately. A functioning tunnel alone does not prove Meta is subscribed correctly.

Do not adopt or terminate an existing CLI service, external tunnel or unrelated Python process. If an archive writer lock or listener port is already in use, show which conflict occurred and ask the owner to stop the earlier terminal app or choose a different port. No scanning Task Manager and killing processes by name.

Stop sync in this release stops both owned collection work and the owned tunnel. Start sync starts them again. Stopping does not delete the archive. Repeated starts cannot spawn duplicate workers. Export remains available while collection is stopped.

## Shutdown

Use a supervised subprocess for network/archive work so an unresponsive network call cannot strand the GUI. The main GUI requests cooperative stop, stops new collection work and cancels pending retries. It keeps an unobtrusive Stopping state visible for up to five seconds; if its worker has not exited, it terminates only that owned process. Its incomplete SQLite transaction rolls back, committed checkpoints remain, and incomplete file downloads are retried on restart.

Terminate the owned `cloudflared` process and wait for it to exit as well. On Windows, put owned worker/tunnel processes in a Job Object with kill-on-close so a GUI crash also cleans up its children. Do not alter global Cloudflare services or other processes. Close must not leave owned child processes or the webhook listener running. There is no confirmation dialog on ordinary close.

Export runs off the GUI thread and supports cancellation between records/files. On close, request cancellation before destroying the window; incomplete exports must not receive `EXPORT_COMPLETE`. The GUI must remain responsive during network checks, sync, file copies and export.

## Configuration

Reuse the portable `.env` beside the executable/source root and the existing variable names. Read current values into the form, including existing user credentials; never ship real `.env` or data inside the build. Default archive/export locations are `data` and `exports` beside the app. Relative paths resolve against the application directory. If that directory is not writable, show a folder selection error before starting; do not silently switch archives.

Main settings:

- Page ID, Page access token, app secret.
- Archive folder via native folder picker.
- Periodic check interval (default five minutes).

Advanced settings:

- Generated/reused webhook verify token, with show/hide and copy.
- API version, listener port, full history check interval and attachment size limit.
- Optional explicit `cloudflared.exe` path when it cannot be found automatically.

Preserve unedited settings, validate before saving, and write atomically. Existing process environment overrides are explicitly surfaced and cannot silently cause saved form values to be ignored. The GUI reloads saved settings from disk rather than retaining values previously inserted into `os.environ` by `load_env`. Source CLI environment-variable precedence remains supported.

Secrets are masked by default, with an explicit reveal action. Never place them in command-line arguments, logs, UI error details, screenshots, exported manifests or test fixtures. The portable configuration remains local plaintext with the same access assumptions as the current app; no claim of encrypted secret storage.

Saving settings applies them through an orderly worker/tunnel restart if collection was running. If stopped, it stays stopped. A port conflict or failed restart is visible and does not undo the saved archive. Changing Page ID or archive location must validate existing Page binding first. Never merge Page histories or silently move/delete an existing archive; selecting another directory selects that archive, with its counts refreshed.

Test connection is a read-only Page identity and conversation-access probe using the form's unsaved values. It reports success, invalid token/permissions or network failure. It does not subscribe the Page or change any Meta configuration. It runs off the GUI thread and does not persist unsaved credentials.

## Temporary tunnel and Meta setup

Keep Quick Tunnel operation with no domain/account requirement. The callback address is shown with Copy URL. When the address changes, show a concise notice that Meta's callback must be updated. Surface Copy verify token where needed without permanently revealing it.

Do not automate Meta login or callback registration in this release. Existing Page subscriptions and permissions still apply. A tunnel failure leaves periodic polling operational; show a limited-connectivity state rather than saying the archive has entirely stopped. The webhook listener remains on loopback and serves only its existing signed webhook and health routes, with no exposed GUI/configuration/file endpoints.

## Status and export behavior

- Distinguish Starting, Syncing, Up to date, Needs attention, Stopping and Stopped.
- Up to date requires a successful API pass without known incomplete conversations or failed/pending attachments. Do not represent API traversal as proof of all historical Facebook data.
- Display separate Page/API and tunnel state. Keep the last successful sync time visible when a later pass fails.
- Show archive counts from the existing database, including failed file counts when nonzero. No fabricated progress percentages.
- Refresh status without polling/writing the database on every render or blocking the UI. Worker status and safe activity events travel through a local process channel, never the public webhook route.
- Recent activity is a bounded list of short, credential-free events. No unbounded in-memory message/event history.
- Export creates the existing consistent SQLite/JSONL/text/media snapshot in a new timestamped directory. Show Exporting, completion or failure and an Open export folder action. Repeated clicks do not start overlapping exports.
- The file system remains the source of truth; opening a folder uses Windows Explorer. No mock Open Folder success messages in the real GUI.

## Structure and limits

Keep domain logic in the existing modules. Extract reusable service lifecycle from the current `run_service` as needed, with a small desktop controller and GUI module. Use Tk's main thread for widgets and an event/queue mechanism to apply background results. Process supervision, settings persistence and packaging should be independently testable; do not introduce a web framework or general plugin architecture.

The single-Page model and SQLite writer lock remain. Existing source CLI operation and export schema remain compatible. Do not change inbox collection rules as part of the GUI work unless a regression test establishes a necessary correctness fix.

The builder chooses widget composition, IPC details, module/file names, build script implementation and small layout adjustments needed for native controls. Match the approved reference rather than promising browser CSS rendering inside Tkinter.

## Validation and deliverables

- Keep all 22 existing archive/webhook regression tests passing.
- Add focused tests for configuration save/reload and secret redaction, archive Page binding, duplicate starts, owned-process cleanup, tunnel URL/connection states, and export cancellation/completion markers.
- Exercise unconfigured startup, invalid credentials, network outage, missing `cloudflared`, existing CLI conflict, Stop/Start, minimize, close during collection and close during export.
- Verify existing real `.env` and archive can be opened without moving or modifying their content unexpectedly. Do not change Meta configuration or terminate the owner's live CLI as part of tests without first explaining the handover.
- Visually inspect the actual Windows GUI against the approved preview and at common Windows display scaling. Build a Windows one-folder distribution and smoke-test that executable, including close/child cleanup.
- Provide source, reproducible build instructions/script, portable build output and a short daily-use guide. Keep build outputs, real credentials, archives and exports ignored by Git.

Implementation starts after approval of this written design. No production GUI or packaging code has been added in this design step.
