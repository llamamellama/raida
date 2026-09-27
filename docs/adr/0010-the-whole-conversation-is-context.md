# ADR-0010: Every question and answer in a session is context for the next one

- Status: accepted
- Date: 2026-09-27
- Amends: ADR-0006 (the planner dropped old conversation turns before taking notes out of the
  prompt)

## Context

A follow-up such as "make it shorter" or "explain the second step" only works if the model is
given the answer it follows. Two rules kept earlier turns out of the prompt:

- `llm.history_budget_tokens` (6,000) offered only the most recent turns that fit it. Replayed
  on the user's database on 2026-09-26, a session of 12 questions sent the model its last 6.
- When the notes of a session's sources nearly filled `llm.interactive_budget_tokens`, the
  planner dropped turns, oldest first, until the prompt fit, and could drop all of them. Five
  two-hour recordings (29,318 tokens of notes) left no room at all, so every follow-up in that
  session reached the model with no conversation, while the screen showed the whole of it.

The user asked that within a session all of the conversation be kept and used as context.

## Decision

1. **The whole conversation.** Every finished question and answer of the session goes into the
   prompt, word for word, oldest first. A question whose answer failed, was stopped or is
   still being written is left out, so the model never sees a question without its answer.
   An earlier skill run appears as its command and what the skill does (ADR-0006).
   `llm.history_budget_tokens` is removed.
2. **The sources' share is chosen without the conversation.** On the fast path the sources take
   at most `llm.interactive_budget_tokens` (notes of long sources, short sources in full),
   decided as if there were no conversation. They then stay the same all session, so each
   answer only adds to a prompt the server has cached (ADR-0005).
3. **The conversation comes on top**, up to `llm.synthesis_budget_tokens`, the largest prompt
   raida sends. Past that, the sources least related to the question are shortened to their
   overviews. The conversation is never cut or summarized. When even overviews leave no room,
   the question fails with a message to start a new session, or to raise
   `llm.synthesis_budget_tokens` together with the model server's context.
4. **"Read full text"** sends the whole conversation too, and condenses sources into what it
   leaves. It fails with the same message before condensing when the conversation alone
   leaves too little room.

## Consequences

- Follow-ups see everything said before. On the user's sessions, the one with five recordings
  now sends its conversation, and the 12-question session sends all 12.
- The prompt grows with the conversation. With llama-server the session is read ahead after
  each answer and only the new turn is read, so a follow-up starts as quickly as before. A cold
  read (after the server unloads the model, or with Ollama, which keeps no prompt cache) reads
  the whole conversation again.
- A session has as much room for conversation as `llm.synthesis_budget_tokens` leaves after
  its sources. The sources use at most `llm.interactive_budget_tokens`, so with the defaults
  (64,000 and 32,000) that is at least 32,000 tokens: about 30 answers of typical length (about
  1,000 tokens each). With a 44,000-token largest prompt it is about 11. After that the
  session must continue in a new one. Nothing is summarized behind the user's back to go
  further; that remains possible later, as an explicit choice.
