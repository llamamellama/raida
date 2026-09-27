# ADR-0008: Nuke resets raida to factory settings; built-in skills can be deleted

- Status: accepted
- Date: 2026-09-26
- Amends: ADR-0006 (built-in skills could only be changed and reset, not deleted)

## Context

The user asked for two things. First, the built-in skills should be deletable like their own,
so the Skills tab and the `@` menu show only the skills they use. Second, a master switch that
deletes everything they made and returns raida to its factory settings, called "Nuke", placed
where it is not hit by accident, and asking for confirmation first.

Built-in skills ship inside the package, which raida must not modify: an update replaces those
files. What a factory reset may delete needs care too. The data directory also holds the
downloaded speech-to-text models, which on a network that blocks Hugging Face cannot be
downloaded again. Files added by path are the user's own files, not raida's. `paths.data_dir`
is configurable and could point at a folder that holds other things.

## Decision

1. **Deleting a built-in skill** records its name in `<data_dir>/skills/.deleted-builtins`, one
   name per line. It then disappears from the list, the `@` menu and `@name`. A changed
   built-in skill is deleted with its changes. The name is free for a skill of the user's own.
   Reset (discard changes to a built-in skill) and Delete are separate actions:
   `POST /api/skills/{name}/reset` and `DELETE /api/skills/{name}`. "Restore" at the bottom of
   the Skills tab (`POST /api/skills/restore-builtins`) brings every deleted built-in skill
   back. Answers already written keep what their skill said (ADR-0006).
2. **Nuke** (`POST /api/reset` with `{"confirm": "NUKE"}`):
   1. Stops all work: every source pipeline, answer, read-ahead and background call. While it
      runs, nothing new starts, and requests to add files or ask questions get 409.
   2. Deletes every row of every table and compacts the database file, so deleted text does
      not linger in free pages or the write-ahead log.
   3. Deletes six named folders of the data directory: `uploads`, `media`, `processed`,
      `artifacts`, `skills` (the user's skills and the deleted built-in list) and `backups`
      (database copies made before upgrades).
   4. Keeps `models/`, `raida.toml`, the model server with its models, and files added by path.
   5. Every open tab receives `app.reset`, clears raida's keys in browser storage and reloads
      into a new, empty session.
3. **Placement and confirmation.** Nuke is in the Settings dialog (the top bar's last button),
   inside a "Danger zone" box below the version and data folder. It opens a dialog that lists
   what is deleted and what is kept. The Nuke button stays disabled until the user types NUKE,
   and the server refuses the request without that word. That is three deliberate steps, and
   no single click or Enter deletes anything.

## Consequences

- A factory reset cannot be undone; the dialog says so. It also deletes the backups, because
  the user asked for all of their data to go. Someone who wants a copy must make it first.
- Deleting only named folders means a data directory pointed at a shared folder loses nothing
  else. A new kind of user data needs adding to `ingest.user_data_dirs`.
- A transcription or OCR already running in a worker thread or process cannot be interrupted.
  It finishes in the background and its result is discarded.
- The deleted built-in list survives upgrades. A built-in skill added in a later version is not
  on it, so it appears.
