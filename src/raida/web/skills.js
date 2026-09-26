// Skills: saved instructions any session runs with @name. The @ menu in the instruction box,
// the Skills tab of the sidebar and the editor.
import { api } from "./api.js";
import { LANGUAGES, toast } from "./util.js";

const ORIGIN_LABELS = { builtin: "built-in", override: "built-in, changed", user: "yours" };
const COMMAND = /^@([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)/;

export function initSkills(store, els) {
  const box = els.instruction;
  let active = 0;
  let matches = [];
  let editing = null; // { original: name or null, origin }
  let nameTouched = false;

  const skills = () => store.state.skills || [];
  const find = (name) => skills().find((s) => s.name === name);

  async function refresh() {
    try {
      const listing = await api.listSkills();
      store.apply("skills.updated", listing);
    } catch (err) { toast(`Could not load skills: ${err.message}`, true); }
  }

  // -- @ menu ----------------------------------------------------------------------------

  function menuQuery() {
    const v = box.value;
    if (!v.startsWith("@") || v.startsWith("@@") || /\s/.test(v)) return null;
    return v.slice(1).toLowerCase();
  }

  function rank(skill, q) {
    if (!q) return 1;
    if (skill.name.startsWith(q)) return 3;
    if (skill.name.includes(q) || skill.title.toLowerCase().includes(q)) return 2;
    return skill.description.toLowerCase().includes(q) ? 1 : 0;
  }

  function renderMenu() {
    const q = menuQuery();
    matches = q === null ? [] : skills().map((s) => [rank(s, q), s]).filter(([r]) => r > 0)
      .sort((a, b) => b[0] - a[0] || a[1].name.localeCompare(b[1].name)).map(([, s]) => s);
    if (!matches.length) {
      els.skillMenu.hidden = true;
      if (q !== null && skills().length) {
        els.skillMenu.hidden = false;
        els.skillMenu.innerHTML = `<li class="skill-menu-empty">No skill matches "@${escapeHtml(q)}". Type @@ to send a literal @.</li>`;
      }
      return;
    }
    active = Math.min(active, matches.length - 1);
    els.skillMenu.hidden = false;
    els.skillMenu.innerHTML = matches.map((s, i) => `
      <li role="option" class="skill-menu-item ${i === active ? "active" : ""}" data-name="${escapeAttr(s.name)}" aria-selected="${i === active}">
        <span class="skill-menu-name">@${escapeHtml(s.name)}</span>
        <span class="skill-menu-title">${escapeHtml(s.title)}</span>
        <span class="skill-menu-desc">${escapeHtml(s.description)}</span>
      </li>`).join("");
  }

  function hideMenu() { els.skillMenu.hidden = true; matches = []; }

  function accept(name) {
    const rest = box.value.replace(/^@\S*/, "").trimStart();
    box.value = `@${name} ${rest}`;
    hideMenu();
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
    renderChip();
  }

  function renderChip() {
    const m = COMMAND.exec(box.value);
    const skill = m && find(m[1]);
    if (!skill) { els.skillChip.hidden = true; return; }
    const hint = skill.argument_hint ? ` · after the command: ${escapeHtml(skill.argument_hint)}` : "";
    els.skillChip.hidden = false;
    els.skillChip.innerHTML = `<span class="pill skill-pill">skill: ${escapeHtml(skill.title)}</span> <span class="muted">${escapeHtml(skill.description)}${hint}</span>`;
  }

  box.addEventListener("input", () => { active = 0; renderMenu(); renderChip(); });
  box.addEventListener("blur", () => setTimeout(hideMenu, 150));
  // Registered before the chat composer's handler, so Enter picks a skill instead of sending.
  box.addEventListener("keydown", (e) => {
    if (els.skillMenu.hidden || !matches.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      active = (active + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length;
      renderMenu();
    } else if (e.key === "Enter" || e.key === "Tab") {
      accept(matches[active].name);
    } else if (e.key === "Escape") {
      hideMenu();
    } else return;
    e.preventDefault();
    e.stopImmediatePropagation();
  });
  els.skillMenu.addEventListener("mousedown", (e) => {
    const item = e.target.closest("[data-name]");
    if (item) { e.preventDefault(); accept(item.dataset.name); }
  });

  // -- the Skills tab --------------------------------------------------------------------

  function renderManager(state) {
    const list = state.skills || [];
    els.tabSkillsCount.textContent = list.length ? String(list.length) : "";
    els.skillsList.innerHTML = list.map((s) => {
      const flags = [
        s.language === "instructions" ? "answers in the instructions' language" : s.language === "sources" ? "answers in the sources' language" : `answers in ${langLabel(s.language)}`,
        s.full_text ? "reads full text" : null,
        s.think ? null : "no thinking first",
      ].filter(Boolean).join(" · ");
      const remove = s.origin === "user" ? `<button class="btn btn-sm btn-quiet btn-danger push" type="button" data-act="delete">Delete</button>`
        : s.origin === "override" ? `<button class="btn btn-sm btn-quiet push" type="button" data-act="reset">Reset</button>` : "";
      return `<div class="skill-item" data-name="${escapeAttr(s.name)}">
        <div class="skill-item-head"><span class="skill-item-title">${escapeHtml(s.title)}</span>
          <span class="pill">${ORIGIN_LABELS[s.origin] || s.origin}</span></div>
        <div class="skill-item-command"><code>@${escapeHtml(s.name)}</code>${s.argument_hint ? ` <span class="muted">${escapeHtml(s.argument_hint)}</span>` : ""}</div>
        <div class="skill-item-desc">${escapeHtml(s.description)}</div>
        <div class="skill-item-meta muted">${escapeHtml(flags)}</div>
        <div class="source-actions">
          <button class="btn btn-sm" type="button" data-act="use" title="Put @${escapeAttr(s.name)} at the start of the instruction box">Use</button>
          <button class="btn btn-sm btn-quiet" type="button" data-act="edit">Edit</button>
          <button class="btn btn-sm btn-quiet" type="button" data-act="duplicate">Duplicate</button>
          <button class="btn btn-sm btn-quiet" type="button" data-act="export">Export</button>
          ${remove}
        </div></div>`;
    }).join("") || '<p class="section-empty">No skills yet. New skill makes one; Import reads a SKILL.md or a .zip.</p>';
    const problems = state.skillProblems || [];
    els.skillsProblems.hidden = !problems.length;
    els.skillsProblems.textContent = problems.map((p) => `Could not read ${p.path}: ${p.error}`).join("\n");
  }

  els.skillsList.addEventListener("click", async (e) => {
    const button = e.target.closest("[data-act]");
    const item = e.target.closest("[data-name]");
    if (!button || !item) return;
    const name = item.dataset.name;
    const act = button.dataset.act;
    try {
      if (act === "use") {
        accept(name); // text already in the box becomes what is typed after the command
      } else if (act === "edit") {
        openEditor(await api.getSkill(name), false);
      } else if (act === "duplicate") {
        openEditor(await api.getSkill(name), true);
      } else if (act === "export") {
        window.location.assign(`/api/skills/${encodeURIComponent(name)}/export`);
      } else if (act === "delete") {
        if (!confirm(`Delete the skill @${name}? Answers it already wrote stay.`)) return;
        await api.deleteSkill(name);
        toast(`Deleted @${name}`);
      } else if (act === "reset") {
        if (!confirm(`Discard your changes to @${name} and bring back the built-in version?`)) return;
        await api.deleteSkill(name);
        toast(`@${name} is back to the built-in version`);
      }
    } catch (err) { toast(err.message, true); }
  });

  els.skillNew.addEventListener("click", () => openEditor(null, false));
  els.skillImport.addEventListener("click", () => els.skillImportFile.click());
  els.skillImportFile.addEventListener("change", async () => {
    const file = els.skillImportFile.files[0];
    els.skillImportFile.value = "";
    if (!file) return;
    try {
      let result;
      try {
        result = await api.importSkill(file, false);
      } catch (err) {
        if (!/already exists/.test(err.message) || !confirm(`${err.message}. Replace it?`)) throw err;
        result = await api.importSkill(file, true);
      }
      const ignored = result.ignored.length ? ` (not used: ${result.ignored.join(", ")})` : "";
      toast(`Imported @${result.skill.name}${ignored}`);
    } catch (err) { toast(`Import failed: ${err.message}`, true); }
  });

  // -- editor ----------------------------------------------------------------------------

  const form = els.skillForm;
  const languageSelect = form.elements.language;
  languageSelect.innerHTML = [["instructions", "Same as the instructions"], ["sources", "Same as the sources"],
    ...LANGUAGES.filter(([c]) => c !== "auto")].map(([c, l]) => `<option value="${c}">${escapeHtml(l)}</option>`).join("");

  function openEditor(skill, duplicate) {
    form.reset();
    els.skillFormError.hidden = true;
    const creating = !skill || duplicate;
    editing = creating ? { original: null, origin: "user" } : { original: skill.name, origin: skill.origin };
    nameTouched = !!skill;
    const values = skill ? { ...skill } : { language: "instructions", think: true, full_text: false };
    if (duplicate) {
      values.name = uniqueName(`${skill.name}-copy`);
      values.title = `${skill.title} (copy)`;
    }
    for (const field of ["title", "name", "description", "instructions", "example", "reference", "argument_hint", "language"]) {
      form.elements[field].value = values[field] ?? "";
    }
    form.elements.full_text.checked = !!values.full_text;
    form.elements.think.checked = values.think !== false;
    const builtin = !creating && skill.origin !== "user";
    form.elements.name.disabled = builtin;
    els.skillFormTitle.textContent = creating ? (duplicate ? "Duplicate skill" : "New skill") : `Edit @${skill.name}`;
    els.skillFormNote.hidden = !builtin;
    showSize();
    els.skillEditor.showModal();
    form.elements[creating ? "title" : "instructions"].focus();
  }

  function uniqueName(base) {
    let name = base.slice(0, 64).replace(/-+$/, "");
    for (let i = 2; find(name); i++) name = `${base.slice(0, 60)}-${i}`;
    return name;
  }

  form.elements.title.addEventListener("input", () => {
    if (nameTouched || form.elements.name.disabled) return;
    form.elements.name.value = form.elements.title.value.normalize("NFKD").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
  });
  form.elements.name.addEventListener("input", () => { nameTouched = true; });

  // Every run reads the instructions, example and reference: show what that costs.
  function showSize() {
    const text = ["instructions", "example", "reference"].map((f) => form.elements[f].value).join("");
    const cjk = (text.match(/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/g) || []).length;
    const tokens = Math.ceil((cjk * 0.8 + (text.length - cjk) / 3.7) * 1.15);
    els.skillFormSize.textContent = `About ${tokens.toLocaleString()} tokens are read with every run (limit 8,000; on an M2 Max each 1,000 add about 3 s before the answer starts).`;
    els.skillFormSize.classList.toggle("over", tokens > 8000);
  }
  for (const field of ["instructions", "example", "reference"]) form.elements[field].addEventListener("input", showSize);
  els.skillCancel.addEventListener("click", () => els.skillEditor.close());

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {};
    for (const field of ["title", "name", "description", "instructions", "example", "reference", "argument_hint", "language"]) {
      body[field] = form.elements[field].value;
    }
    body.full_text = form.elements.full_text.checked;
    body.think = form.elements.think.checked;
    try {
      const saved = editing.original ? await api.updateSkill(editing.original, body) : await api.createSkill(body);
      els.skillEditor.close();
      toast(`Saved @${saved.name}`);
      await refresh();
    } catch (err) {
      els.skillFormError.textContent = err.message;
      els.skillFormError.hidden = false;
    }
  });

  // The store notifies on every streamed word; redraw only when the skills themselves change,
  // so buttons are not replaced under the pointer.
  let shown = null;
  store.subscribe((state) => {
    if (state.skills === shown) return;
    shown = state.skills;
    renderManager(state);
    if (!els.skillMenu.hidden) renderMenu();
    renderChip();
  });
  return { refresh };
}

function langLabel(code) { return LANGUAGES.find(([c]) => c === code)?.[1] || code; }
function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }
