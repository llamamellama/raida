# Benchmarks

Machine-specific measurements appended by `scripts/bench_llm.py` and `scripts/bench_stt.py`
(`make bench`). Record the model tag, server and macOS version with each run. Use the prefill
rate at 32k and 64k tokens to decide `llm.synthesis_budget_tokens`, and compare Ollama's MLX
and GGUF tags, or llama-server, on the same prompt sizes before settling on one.

Reference points from public sources (not this machine), September 2026:

| Setup | Measurement | Source |
| --- | --- | --- |
| llama.cpp, Llama-7B F16, M4 Max 40-core | prefill 923 tok/s, decode 31.6 tok/s | ggml-org/llama.cpp discussion 4167 |
| llama.cpp, Llama-7B F16, M5 Max 40-core | prefill 3158 tok/s, decode 37.1 tok/s | same |
| gpt-oss-120b MXFP4 on M5 Max 128 GB via MLX | ~88 tok/s decode, ~2.7k tok/s prefill at 16k ctx | siliconscore.com (secondary) |
| Parakeet TDT 0.6B v3 CoreML, M4 Pro | 155x real time | FluidAudio benchmarks |
| mlx-whisper large-v3-turbo, M4 Pro | ~14-18x real time | secondary blog reports |

## 2026-09-25, M2 Max 96 GB, macOS 26.6.2, Ollama 0.30.10, `qwen3:30b-a3b` (Q4_K_M, thinking-only tag)

Settings: OLLAMA_FLASH_ATTENTION=1, OLLAMA_KV_CACHE_TYPE=q8_0, num_ctx 138432, model 100% GPU.
Prompt: one Traditional Chinese transcript of a 2h20m recording (31,259 characters).

| Measurement | Value |
| --- | --- |
| Tokens for that transcript | 22,842 to 25,431 (with and without anchors) |
| Characters per token | 1.37 (0.83 tokens per CJK character) |
| Prefill, default `num_batch` 512 | 213 tok/s |
| Prefill, `num_batch` 1024 | 222 tok/s |
| Prefill, `num_batch` 2048 | 216 tok/s |
| Prefill, four transcripts (~100k tokens) | did not finish in 900 s |
| Prefill, cached prompt prefix (same sources, new question) | 0.6 s |
| Decode, short English prompt | 62 to 74 tok/s |
| Decode, this model's reasoning before an answer | a few hundred to several thousand tokens per answer |

Conclusions: batch size is not the lever for prefill on this stack; prompt-prefix caching is, so
ask several questions in one session rather than one question per session. Reading 100k tokens
takes over 15 minutes and the first answer over four two-hour recordings costs 20 to 25 minutes;
one recording per session answers in about 5 minutes. Apple SpeechAnalyzer via yap transcribed
2h20m of Mandarin in about 60 s (about 140x real time).

## 2026-09-25, M2 Max 96 GB, llama.cpp build 11146 (Homebrew `llama.cpp` 0.5.0), same model

`qwen3:30b-a3b` GGUF read in place from Ollama's store, Metal, flash attention on.

`llama-bench`, prompt reading (pp) and writing (tg) at a given context already read (d):

| Test | ubatch 512 | ubatch 2048 |
| --- | --- | --- |
| pp2048 at d0 | 1,023 tok/s | 1,107 tok/s |
| pp2048 at d16384 | 294 tok/s | 310 tok/s (q8_0 KV: 312) |
| tg32 at d0 | 92 tok/s | 93 tok/s |
| tg32 at d16384 | 50 tok/s | 53 tok/s |
| tg64 at d4096, f16 / q8_0 KV | | 73 / 56 tok/s |

Reading N tokens takes about 0.76 ms x N + 0.075 us x N^2: 30 s for 16k, 100 s for 32k, 14
minutes for 100k. Ollama 0.30.10 (llama.cpp build 9672, ubatch 512, q8_0 KV) read about half as
fast (213 tok/s over a 23k-token prompt).

raida with `scripts/llama-server.sh` (3 slots, unified f16 KV, 8 GB prompt cache), four Mandarin
recordings (2h04m to 2h22m, 9h20m in total), Apple speech (zh-TW) plus OpenCC:

| Step | Measured |
| --- | --- |
| Transcription, per recording | 46 to 51 s |
| Notes, per 4k-token section (reasoning off) | 12 to 18 s: prompt at about 840 tok/s, notes at about 71 tok/s |
| Notes, per recording (6 to 8 sections plus overview) | about 2.6 minutes; notes are 21 to 27% of the transcript (4.6k to 7.7k tokens) |
| All four recordings, re-run to fully ready | about 9 minutes, in the background |
| Attaching the four from the library to a new session | 0.2 s |
| Reading the new session ahead (25,140 tokens) | 58 s at 436 tok/s, in the background |
| Question 1 (Traditional Chinese, synthesis), session read ahead | 1,779 new tokens read (24,770 cached); text after 29.7 s, of which about 24 s reasoning (952 tokens, 2048 budget); done at 54 s |
| Question 2 (a specific story), follow-up | text after 26.7 s (649 reasoning tokens); the passage search found the verbatim story |
| Question 1 with `llm.think = false`, prefix cached | text after 0.1 s, done at 34 s; more of a list of quotes, less synthesis |
| Worst case: three recordings in a new combination, asked at once | 18,645 tokens read cold; text after 62 s, done at 84 s |
| Writing speed at 25k context | about 40 tok/s |

A 16k-token prompt restored from the server's RAM prompt cache after four other prompts had
used its slot (0.1 s). At 25k tokens a session's cached prompt takes about 2.6 GB, so the
default 8 GB holds about three sessions beyond the three slots.

## 2026-09-26, skills, same machine and server

Four Mandarin recordings (9h20m) from the library in one session, `qwen3:30b-a3b` on
`scripts/llama-server.sh` (1024-token reasoning budget), session read ahead unless noted.

| Run | Measured |
| --- | --- |
| Reading a new session ahead (24,778 tokens) | about 60 s of prompt reading, in the background |
| `@key-takeaways` (built-in, English instructions, answers in the sources' language) | 25,517-token prompt, 24,770 cached; text after 29 s (1,028 reasoning tokens), done at 48 s; Traditional Chinese, headings in Chinese, 15 time codes |
| `@summary` sent the moment the previous answer finished | waited 0.7 s for the session's read-ahead, 24,770 cached; text after 29 s, done at 50 s |
| A user skill in Traditional Chinese with example and reference | follows the example's structure (headings 一、二、三 as in its example); measured only before the fixes below, uncached |
| Before two fixes in this change: the same question in a session with long history | one recording cut to its overview and nothing cached: 64 to 73 s of reading, text after about 90 s |
| Before the fixes: a question sent while its session was being read ahead | read-ahead cancelled, question on another slot, 30,519 tokens read from scratch: text after 134 s |

A skill adds its instructions, example and reference to the final turn: on this machine about
3 s per 1,000 tokens at 25k tokens of context.
