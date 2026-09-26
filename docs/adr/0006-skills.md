# ADR-0006: Skills are Agent Skills folders the user runs with @name

- Status: accepted
- Date: 2026-09-26

## Context

The same operation comes back again and again ("organize the key points and give practical
steps", "minutes of this meeting"): the same instructions, the same output format, the same
names the speech recognizer gets wrong. Retyping them is slow and the results drift.

A survey of how the major products solve this (25 September 2026; sources below) found that
they have converged on one unit, the skill: a folder whose `SKILL.md` holds YAML frontmatter
(`name`, `description`) and instructions in Markdown, with optional `scripts/`, `references/`
and `assets/` directories. It is an open specification (agentskills.io) that Claude (apps,
API, Claude Code), ChatGPT and Codex, Gemini CLI and Gemini Enterprise, Microsoft 365 Copilot
(declarative agents, preview) and Cursor load. The older mechanisms are being retired in its
favour: Claude Code merged slash commands into skills, Codex deprecated custom prompts,
OpenAI's stored API prompts end on 30 November 2026 and custom GPTs migrate to plugins built
from skills.

| Product | Unit | How it is run | Arguments |
| --- | --- | --- | --- |
| Claude Code | SKILL.md plus extra fields | `/name`, or chosen by the model from its description | `$ARGUMENTS`, `$N`; appended when there is no placeholder |
| claude.ai, Claude API | SKILL.md (zip) | chosen by the model | none |
| ChatGPT, Codex | SKILL.md | `@name`, `$name`, `/skills`, or chosen by the model | free text |
| Gemini CLI | TOML custom command; also SKILL.md | `/name` | `{{args}}`; appended when absent |
| Gemini Gems | instructions plus knowledge files | picking the Gem for a chat | none |
| Microsoft 365 Copilot | saved prompts; declarative agents with skills | picker; @mention | none |
| Model Context Protocol | prompts (name, title, description, arguments) | user-controlled, shown as slash commands | named strings |

Constraints of this app shaped the choice among the variants:

- The model is a local 30B model. Letting it choose skills from their descriptions puts a
  catalog into every prompt and adds a decision the model may get wrong; the user asked for
  a shortcut they call themselves.
- raida reads a session's sources ahead of the question into the server's prompt cache
  (ADR-0005). Whatever varies per question must come after that prefix.
- raida runs no code, and a skill copied from elsewhere should not change that.

## Decision

1. **Format.** A skill is an Agent Skills folder: `SKILL.md` with `name` (1-64 lowercase letters,
   digits and single hyphens, equal to the folder name) and `description` (at most 1,024
   characters), then the instructions. The output example is `assets/example.md` and the
   reference (names, terms, conventions) is `references/reference.md`, the directories the
   specification names. raida's options go in the specification's string-to-string `metadata`
   map under `raida-` keys (title, argument hint, language, full text, think), so an exported
   folder stays valid elsewhere. The SKILL.md raida writes ends with a marked block pointing to
   the two files, so tools that load referenced files on demand find them; raida drops the
   block when reading. Frontmatter raida does not use (license, other metadata) is kept.
2. **Where they live.** Built-in skills ship in the package (`summary`, `key-takeaways`,
   `meeting-minutes`, `article`, `questions-answers`); the user's are in
   `<data_dir>/skills/<name>/`, read on every use, so editing a file by hand works. A user skill
   with a built-in's name replaces it; deleting it restores the original.
3. **Running.** Explicitly only: `@name` at the start of an instruction, from a menu that opens
   when `@` is typed, or with Use in the Skills tab of the sidebar. Text after the command
   replaces `$ARGUMENTS` in the instructions, or is added at the end, as in Claude Code and
   Gemini CLI. `@@` sends a literal at sign; an unknown `@name` is an error listing the skills,
   so a mistyped name does not go to the model as a question. The model never picks a skill by
   itself. The first version used `/name`, as Claude Code and Gemini CLI do; the user asked for
   `@`, which ChatGPT and Codex use for skills and Microsoft 365 Copilot for agents, and which
   leaves the slash to the paths (`/Users/...`) people paste at the start of an instruction.
   Skills belong to the app, not to a session: the sidebar's Skills tab lists, creates, edits,
   imports and exports them, and every session's `@` menu offers all of them.
4. **Prompt placement.** The rendered skill (instructions, then the reference and the example,
   each marked for what it is) is the final user turn, after the cached prefix, as OpenAI also
   inserts skills as user input. A skill of about 1,000 tokens adds about 3 s at 25k tokens of
   context on the M2 Max; the editor shows the estimate and saving refuses more than 8,000.
5. **One message, recorded.** A skill applies to the message that invokes it. Both messages
   store what ran: name, title, description, the arguments, the instructions as filled in, the
   options and a revision hash; the user message also stores the full instruction sent. Later
   edits do not change past runs. In the conversation history an earlier run appears as its
   command and a one-line description, not its full prompt.
6. **Language.** A skill answers in the language of its instructions (as a normal message
   does), the main language of the session's sources (the built-in skills, written in English,
   use this), or a language set on the skill. Chinese output is held to its script as in
   ADR-0005. Example headings are written as descriptions in angle brackets (`## <Key points>`),
   because the model copied literal English headings into Chinese answers.
7. **Nothing runs.** Import accepts a `SKILL.md`, or a `.zip` or `.skill` archive read in memory
   (never extracted), with size limits. Only Markdown is used; scripts and other files are
   listed as ignored.
8. **Options per skill.** "Read full text" (the ADR-0005 full-text path) and "think before
   answering" (off makes the answer start at once) are skill settings, since they belong to the
   operation.

## Consequences

- Skills from the Claude, ChatGPT, Codex or Gemini ecosystems import as long as they are
  instructions; those that depend on scripts or tools lose that part, and the import says so.
  raida's exports load in those tools.
- Measured on the M2 Max over four two-hour recordings, with the session read ahead: a built-in
  skill's answer starts in about 29 s, about 25 s of which is the model reasoning, and follows
  its example's structure in Traditional Chinese.
- Running a skill right after the previous answer used to cancel the background read of that
  answer and land on another server slot, re-reading 30k tokens (134 s). A question now waits
  for its own session's read-ahead, and the planner drops old conversation turns before it
  takes notes out of the prompt; both changes help every question, not only skills.
- Not done, and possible later: skills the model may suggest, a skill pinned to a session
  (placed in the cached prefix), named arguments with a form (MCP style), version history.

## Sources

Agent Skills specification https://agentskills.io/specification and client guide
https://agentskills.io/client-implementation/adding-skills-support; Claude Code
https://code.claude.com/docs/en/skills; Claude API
https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview; claude.ai
https://support.claude.com/en/articles/12512198-how-to-create-custom-skills; ChatGPT skills
https://web.archive.org/web/20260725170509/https://help.openai.com/en/articles/20001066-skills-in-chatgpt;
GPT migration https://web.archive.org/web/20260917204309/https://help.openai.com/en/articles/20001519;
Codex https://web.archive.org/web/20260719143805/https://learn.chatgpt.com/docs/build-skills.md;
OpenAI API skills
https://web.archive.org/web/20260611042545/https://developers.openai.com/api/docs/guides/tools-skills.md
and prompts https://web.archive.org/web/20260920184040/https://developers.openai.com/api/docs/guides/prompting;
Gemini Gems https://support.google.com/gemini/answer/15146780; Gemini CLI
https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/custom-commands.md; Gemini
Enterprise https://docs.cloud.google.com/gemini/enterprise/docs/skills; Microsoft 365
https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/declarative-agent-skills;
MCP prompts https://modelcontextprotocol.io/specification/2026-07-28/server/prompts; Cursor
https://cursor.com/docs/skills. OpenAI pages were read from Web Archive snapshots (dates in the
URLs) because the live sites do not open from this network.
