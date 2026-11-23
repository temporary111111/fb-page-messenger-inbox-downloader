# Desktop App Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. User authorized implementation in the current chat.

**Goal:** Deliver the approved compact Windows desktop UI and a working portable executable, reusing the existing archive.

**Architecture:** Tkinter/ttk main window supervises one archive subprocess and one cloudflared subprocess. Local JSON lines carry safe status/stop events. Existing archive code remains reusable by the CLI. The GUI owns cancellation, settings persistence and process cleanup.

**Tech Stack:** Python 3.11+, Tkinter/ttk, SQLite, Windows Job Objects, PyInstaller (build only).

**Spec:** `docs/superpowers/specs/2026-11-24-desktop-app-design.md`

## Global constraints

- Portable folder, no paid hosting, no public administration endpoints.
- Mask secrets; do not put credentials in logs, build output or command arguments.
- Minimize continues; Close stops owned processes within five seconds.
- Preserve the current `.env`, archive and CLI commands.
- Build in this app's separate repository. Work in place on `codex/desktop-app`; do not move the user's live archive.

## Review focus

- A saved setting must be reloaded without stale `os.environ` values.
- A Page change must never bind a different Page to the same archive.
- A conflict with the existing CLI must not kill or adopt that CLI.
- An allocated tunnel URL is not evidence that a tunnel or Meta webhook is connected.
- Closing during an API call or export must not strand child processes or mark a partial export complete.

### Task 1: Desktop configuration and cancellable export

**Files:** `inbox_archive/config.py`, new `inbox_archive/desktop_settings.py`, `inbox_archive/export.py`, `tests/test_desktop_settings.py`.

**Interfaces:** `Settings.from_values(values, base_dir)`; `ConfigStore(root).read()/save(values)/settings()`; `export_archive(db, destination, cancel=None)`.

- [ ] Write and run failing tests for save/reload, relative paths, comments/unknown keys, validation, Page binding, cancellation and incomplete markers.
- [ ] Implement atomic file saves without mutating process environment; validate existing DB Page identity read-only before save.
- [ ] Add cancellation between records and file-copy chunks while preserving existing snapshot semantics.
- [ ] Run all tests and checkpoint.

### Task 2: Owned worker and tunnel lifecycle

**Files:** new `inbox_archive/desktop_runtime.py`, `inbox_archive/desktop_worker.py`, `desktop_entry.py`, `tests/test_desktop_runtime.py`; minimal cancellation hooks in archive loops.

**Interfaces:** `DesktopController(root, events).start()/stop()/close()`; queue events with `kind`, safe payload; internal `desktop_entry.py --worker --app-dir ROOT` worker mode.

- [ ] Write and run failing tests for duplicate starts, exact owned-child cleanup, URL parsing vs connection state, and redacted activity.
- [ ] Build process supervision using a Windows kill-on-close Job Object, bounded status queue, hidden subprocesses and cooperative stdin stop with five-second fallback termination.
- [ ] Worker reuses existing DB lock, webhook server, sync and media code. CLI conflicts remain errors, not process-kill requests.
- [ ] Run all tests, source subprocess smoke checks and checkpoint.

### Task 3: Native GUI and portable build

**Files:** new `inbox_archive/desktop.py`, `build-desktop.ps1`, `Open Inbox Archive.cmd`, build requirements/spec as needed, README/ignore updates, `tests/test_desktop_ui.py`.

**Interfaces:** `run_desktop(root)`; Tk main-thread UI receives queued background events; build output `dist/InboxArchive/InboxArchive.exe`.

- [ ] Implement the approved Overview and Settings views with native controls, status messages, short activity, native dialogs, connection tests, secrets reveal, export/open folder and callback copy.
- [ ] Keep test/export/settings operations off the GUI thread; prevent overlapping actions and orderly restart after settings save.
- [ ] Add a source launcher and packaged entrypoint that resolve paths predictably; root launcher explicitly reuses the current app directory.
- [ ] Install build-only PyInstaller into the ignored project virtual environment and build one-folder Windows output with no credentials/data included.
- [ ] Test settings/start/stop/minimize/close on the actual Windows GUI and smoke-test the executable with an isolated test configuration.
- [ ] Run full tests and compilation, check clean diff, independent code review, fix important findings and checkpoint.

## Progress

- Planning: complete. Native execution selected to minimize coordination overhead; final independent review required.
- Baseline: existing 22 tests were green at the current-app checkpoint; re-run before edits.
