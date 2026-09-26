# ADR-0005: Prepare sources when they are added; answer from notes with a warm prompt cache

- Status: accepted
- Date: 2026-09-25
- Amends: ADR-0002 (llama-server becomes the recommended server on Apple Silicon)

## Context

The first real use was four two-hour Mandarin recordings and a Traditional Chinese question.
Transcription took about a minute per recording, but the answer never arrived: the four
transcripts were about 100k tokens, and a prompt that size did not finish reading in 15
minutes. Measured on the target M2 Max with `qwen3:30b-a3b` (llama.cpp build 11146, Metal):

| Prompt already in context | Reading speed for the next 2k tokens |
| --- | --- |
| 0 | about 1,100 tok/s |
| 16k | about 300 tok/s |

Attention cost grows with the context already read, so reading N tokens takes about
0.76 ms x N + 0.075 us x N^2: 30 s for 16k tokens, 100 s for 32k and 14 minutes for 100k.
A faster engine does not change the shape: Ollama 0.30.10 (which runs an older llama.cpp)
read half as fast, and by our estimate attention already runs at about 40% of the GPU's peak. The usage
wanted is "add files at any time; once ready, any of them can be in any session, and several
together, without a long wait". That rules out reading full transcripts for every question.

Two other facts shaped the design:

- llama-server keeps each slot's processed prompt and, with `--cache-ram`, the prompts of idle
  slots. A request whose prompt starts like a cached one reads only the rest (measured: a
  9.9k-token prefix read once, then a question answered with 17 new tokens read and the first
  token after 0.2 s). A request with `max_tokens: 0` reads a prompt without answering, and an
  interrupted read keeps the part already read.
- Ollama's `qwen3:30b-a3b` weights are the thinking-only "Thinking 2507" model. It reasons for
  hundreds to thousands of tokens before answering, even for extraction. Closing its reasoning
  block in a raw prompt makes it answer at once with good notes (a 4.4k-token excerpt became
  850 tokens of anchored notes in 23 s).

## Decision

1. **Notes at ingest.** When a source longer than `notes.min_source_tokens` is ready, it is
   split into sections of `notes.section_tokens` (about 25 minutes of speech) and each section
   becomes anchored notes of about `notes.ratio` of its length, then an overview is written.
   The source is "ready" only after that. Notes depend on the file and the model, never on a
   question; they are stored per processed document and shared by every session. Each section
   is cached as it finishes, so an interrupted run resumes.
2. **Answers from notes.** A normal answer gives short sources in full and long sources as
   their notes, plus verbatim passages found for the question with BM25 (character bigrams for
   Chinese, Japanese and Korean; no model, no index files). The prompt must fit
   `llm.interactive_budget_tokens`; when notes of many sources do not, the least relevant
   sources fall back to their overviews. "Read full text" keeps the previous behaviour for the
   questions that need every word.
3. **Stable prefix, read ahead.** The prompt starts with the system prompt, the sources and the
   earlier turns; retrieved passages and the question come last. That prefix is read into the
   server's cache in the background when a session is opened, when its sources settle, and
   after each answer.
4. **Priority.** Answers hold an interactive slot; background calls (notes, read-ahead, titles)
   run only while no answer is being written and are cancelled and retried when one starts.
5. **llama-server is the recommended backend on Apple Silicon**, started by
   `scripts/llama-server.sh` with 3 slots, a unified f16 KV cache, 8 GB of prompt cache in RAM,
   a 1024-token reasoning budget and unload after an hour idle. Ollama remains supported and is
   how model weights are downloaded; the script reads Ollama's GGUF in place.
6. **Chinese script.** Machine transcripts for `zh-TW`, `zh-HK` and `zh-CN` are normalized with
   OpenCC (through Simplified, then to the regional standard), because Apple's zh-TW recognizer
   converts character by character and wrote 麵 for every 面 in the test recordings. Answers
   follow the instruction's language; for Chinese, answers, titles and notes are also held to
   a target script (the instruction's, or the sources' after an instruction in another
   language) by a reminder and by converting the model's output, because Qwen3 answered
   Traditional Chinese questions in Simplified in two runs out of four.

## Consequences

- A two-hour recording is fully ready about 3.5 minutes after it is added on the M2 Max (50 s
  transcription, 2.6 minutes of notes), in the background, while other work continues.
- Notes of four two-hour recordings are about 19k tokens instead of about 100k. Read cold they
  take about 40 s; read ahead, the question starts answering after reading only itself.
- Answers from notes lose verbatim detail that neither the notes nor the retrieved passages
  carry. "Read full text" exists for that, at the old cost.
- Notes are taken with the model configured at the time; changing `llm.model` takes them again.
- The GPU is busy in the background after files are added. Answers pre-empt it, one background
  call runs at a time by default (`workers.llm_background`), and llama-server unloads the model
  after an hour idle, so memory is returned when the app is not in use.
