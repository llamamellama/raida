# ADR-0007: One source library shared by every session

- Status: accepted
- Date: 2026-09-26

## Context

Sources were rows of a session. A recording used in five sessions was five rows, each with its
own processing state. The processed-text cache and the notes (ADR-0005) made the later rows
cheap, but only once the first had finished: the cache is consulted when a pipeline starts, so a
file added to a second session while the first was still transcribing was transcribed again. A
file also lived only as long as the sessions holding it; deleting the last one deleted the
upload, and the transcription had to be done again the next time. A "From library" dialog listed
the files of all sessions, but a new session showed none of them until it was opened, and the
user did not find it.

The user asked for two levels: a master library in which every transcribed or processed file
stays available to all sessions, and sessions that start empty and let the user choose from the
library, on top of new uploads, without forcing every earlier file on them.

## Decision

1. **A source is a library entry.** One row per file content (SHA-256, unique) holds the file
   and its processing state: status, progress, processed text, token estimate, notes, language.
   Sessions use sources through `session_sources` (session, source, when it was added).
2. **Adding looks the file up first.** Uploading, adding by path and `raida process` look the
   content up in the library. A known file is not processed again: it is ready at once, or the
   new session shares the run in progress and its questions wait for that run. The new copy of
   an upload is discarded; the library keeps the one it has, unless that one is gone. A known
   file whose run failed or was cancelled runs again, with the language given now.
3. **Sessions choose.** A new session has no sources. The Sources tab lists the session's
   sources, then every other library file with an Add button. Remove takes a file out of the
   session only; the file keeps processing if it was, and stays in the library.
4. **Deleting.** Deleting a session keeps its files. Deleting a file from the library removes it
   from every session and deletes what raida stored for it: the uploaded copy (a file added by
   path is the user's and stays), the decoded audio, the processed text, the notes and cached
   condensations.
5. **Language belongs to the file.** Changing it re-processes the file for every session that
   uses it; the cache keeps the earlier result, so switching back is immediate.
6. **Events.** Changes to a file go to every open tab, since any session may list it;
   `session.sources` tells a session's tabs that its list changed. When a file finishes, only the
   sessions open in a tab are read ahead (ADR-0005); the others are read when they are opened,
   so a file used in many sessions does not queue a long background read for each of them.
7. **Migration (schema v5).** The rows of one file become one entry carrying the state of its
   ready row updated last (else its row updated last); every session keeps the files it had, and
   job records follow the entry.

## Consequences

- A recording is transcribed once however many sessions use it, including when two sessions
  add it at the same time.
- Transcription work is never lost by deleting a session or removing a file from one. Disk use
  grows until files are deleted from the library; each entry shows its size and how many
  sessions use it.
- `DELETE /api/sources/{id}` deletes a file from the library. `DELETE
  /api/sessions/{sid}/sources/{id}` removes it from one session, and `PUT` on the same path
  adds a library file to a session (replacing `POST .../sources/from-library`).
- A session's source list is its choice of library files; asking "which sessions use this
  file" is one query, so the library can show it.
- Not done, and possible later: a language per session for the same file, folders or tags in
  the library, a disk quota.
