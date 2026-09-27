# Skills

A skill is an instruction you save once and run in any session: what to do with the sources,
an example of the format the answer should take, and reference notes such as the correct
spelling of names. Type `@` in the instruction box, pick a skill, add a few words if you like,
and press Run. Skills belong to the app, not to a session: the **Skills** tab in the sidebar
lists every skill and is where you create, edit, import and export them.

The design, and how Claude, ChatGPT, Gemini and others do the same, is in
`adr/0006-skills.md`.

## Running a skill

- Type `@` at the start of the instruction box. A menu lists the skills; keep typing to filter
  by command, title or description, then press Enter or Tab (or click). The line under the box
  shows the skill and what it expects after the command.
- Anything after the command adds to the skill's instructions for this run:
  `@summary focus on the budget`, `@key-takeaways 著重在家庭關係`.
- Or click **Use** on a skill in the **Skills** tab. Text already in the box becomes the
  addition.
- The skill has to come first; `@summary` in the middle of a sentence is plain text. To send
  an instruction that really starts with `@`, begin it with `@@`. A slash at the start runs
  nothing (it did in the first version of skills).
- The command line works too: `raida ask <session-id> "@summary for the team"`.

The question shows a "skill" badge and, folded, the full instruction the model was given. The
answer is labelled with the skill. Editing the skill later does not change past answers.

## Built-in skills

| Command | What it writes |
| --- | --- |
| `@summary` | Overview, key points by theme with anchors, notable quotes, open questions |
| `@key-takeaways` | Core ideas, then concrete steps and practices, then reminders |
| `@meeting-minutes` | Decisions, action items (owner, due date), discussion by topic, open issues |
| `@article` | An article for a general reader: title, lead, sections, conclusion |
| `@questions-answers` | Questions a reader would ask, each answered from the sources |

They answer in the main language of the sources, so a Traditional Chinese recording gets a
Traditional Chinese answer. Edit one to make it yours (the list then says "built-in, changed");
**Reset** brings the original back. **Delete** removes any skill you do not use, built-in ones
included (a changed one goes with your changes); **Restore**, at the bottom of the Skills tab,
brings the deleted built-in skills back.

## Making a skill

In the **Skills** tab: **New skill**, or **Duplicate** an existing one. The fields:

| Field | Notes |
| --- | --- |
| Title | Any language; shown in the menu |
| Command | Lowercase letters, digits and hyphens, for example `meeting-minutes` |
| Description | What the skill produces, in one sentence; under 200 characters if you will also use it in Claude's apps |
| Instructions | What to do with the sources. `$ARGUMENTS` is replaced by the text typed after the command; without it, that text is added at the end |
| Output example | The format to follow: headings, lists, length, tone. Its content is never copied. Write headings as descriptions in angle brackets (`## <Key points>`) when the answer may be in another language than the example |
| Reference | Terms, names and conventions to apply, for example `賽斯心法 (not 賽事心法)`. It is not treated as a source |
| Answer language | Same as the instructions (default for new skills), same as the sources, or a fixed language |
| Read full text | Reads every source word for word (the slow path) instead of the notes of long sources |
| Think before answering | On: better structure, the answer starts about half a minute later over long sources. Off: text starts at once |

The editor shows about how many tokens the skill adds to each run; each 1,000 add about 3 s
before the answer starts over long sources on an M2 Max. The limit is 8,000.

## Where skills live, and the file format

Your skills are folders in `~/Library/Application Support/raida/skills/`, one per skill, in
the Agent Skills format (agentskills.io) that Claude, ChatGPT, Codex, Gemini and Cursor also
read:

```
meeting-minutes/
  SKILL.md                  # frontmatter and instructions
  assets/example.md         # output example (optional)
  references/reference.md   # reference notes (optional)
```

```markdown
---
name: meeting-minutes
description: Minutes of a meeting recording - decisions, action items and open issues.
metadata:
  raida-title: Meeting minutes
  raida-language: sources            # instructions | sources | a code such as en, zh-TW, fr
  raida-full-text: 'false'
  raida-think: 'true'
  raida-argument-hint: meeting name (optional)
---

Write the minutes of the meeting in the sources. ...

<!-- raida: files -->
Follow the output format in [assets/example.md](assets/example.md).
```

- `name` must equal the folder name. `metadata` values are strings, as the format requires.
- The block after `<!-- raida: files -->` is written by raida so other tools find the two
  files; raida ignores it when reading.
- You can edit these files in any text editor; raida reads them on every use. A folder it
  cannot read is listed under Skills with the reason, instead of breaking the list. A broken
  copy of a built-in skill is listed as "built-in, changed", so Reset and Delete repair it.
- Deleted built-in skills are named in `.deleted-builtins` in the same folder, one per line.
  Removing a name, or the file, brings that skill back.

## Sharing

- **Export** downloads `<name>.zip` holding the folder. It imports into raida on another Mac, and
  into the tools that load Agent Skills (Claude's apps take a skill as a zip upload; Claude
  Code, Codex, Gemini CLI and Cursor read skill folders).
- **Import** takes a `SKILL.md`, or a `.zip` or `.skill` archive of a skill folder. From other
  tools' skills raida uses the instructions, `assets/example.md` (or `example.md`,
  `assets/template.md`) and Markdown under `references/`. Scripts and other files are listed as
  not used; raida never runs anything from a skill. Importing over an existing name asks first.
- A skill from someone else is a set of instructions for the model: read it before you use it.

## Command line

```bash
uv run raida skills                    # list skills
uv run raida skills show summary       # print one as SKILL.md, with its example and reference
uv run raida ask <session-id> "@summary for the team"
```
