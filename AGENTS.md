# Working Agreement for Future AI Assistants

Read `docs/AI-HANDOFF.md` before changing code or operating the live archive. It is the current handoff record. Then read `docs/ARCHITECTURE.md` for system boundaries and `docs/OPERATIONS.md` before running live commands.

## Non-negotiable safety rules

- Never read, print, copy, commit, upload, or ask the user to paste `.env`, databases, attachments, exports, webhook payloads, access tokens, app secrets, or verify tokens.
- The real Page archive is in an ignored `data-page-<page-id>/` directory. Treat it as private customer data. Do not delete, move, reset, or replace it.
- One data directory belongs to one Page. If `.env` changes Page ID, set a new `DATA_DIR`; do not reuse an archive made for another Page.
- Do not run two writers (`serve`, `sync`, or `download`) against the same archive concurrently. Read-only `status` and `export` are safe alongside a writer.
- Never change Meta configuration, webhook subscriptions, Cloudflare configuration, or Windows system settings without the user's explicit instruction.
- Do not run a desktop build or launch the desktop app against the real archive just to test UI. Use a synthetic/isolated app directory.

## Repository conventions

- This folder is its own Git repository. Do not use a parent repository.
- Runtime data is ignored by Git. Before every commit, confirm no `.env`, `data*`, `exports`, SQLite file, or attachment is staged.
- Preserve the existing commit history and use small, descriptive commits.
- Add a regression test for every bug fixed in pagination, checkpoints, exports, or security-sensitive behavior.
- Keep `docs/AI-HANDOFF.md` current when the live state, a blocker, or a recommended next action changes.

## Definition of done for archive changes

1. Run focused tests, then `python -m unittest discover -s tests -q` when the local Tk runtime permits UI tests.
2. Run `python -m compileall -q inbox_archive desktop_entry.py tests`.
3. Run `git diff --check`.
4. Report whether a live operation was performed and its observed counts/errors without exposing customer content.
