"use strict";
// Plain JS, no build step. User data is always rendered as text (textContent), never HTML.

const view = document.getElementById("view");
let people = [];  // cache for names/autocomplete

// --- helpers ---------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c != null && c !== false) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const msg = (await res.json().catch(() => ({}))).detail || res.statusText;
    toast(`Erro: ${msg}`);
    throw new Error(msg);
  }
  return res.json();
}

function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2200);
}

const crop = (faceId) => `/api/faces/${faceId}/crop`;
const label = (p) => p.name || `Sem nome #${p.id}`;

async function refreshStatus() {
  const s = await api("/api/status");
  document.getElementById("root").textContent = s.root;
  document.getElementById("n-suggested").textContent = s.suggested || "";
  document.getElementById("n-unassigned").textContent = s.unassigned || "";
}

async function refreshPeople() {
  people = await api("/api/people?hidden=true");
  const list = document.getElementById("people-names");
  list.replaceChildren(...people.filter((p) => p.name).map((p) => h("option", { value: p.name })));
}

// "Maria" -> existing person by name; "#12" -> person 12; else a new name.
function resolvePerson(text) {
  const t = text.trim();
  const byId = t.match(/^#?(\d+)$/);
  if (byId) return { person_id: Number(byId[1]) };
  const found = people.find((p) => p.name && p.name.toLowerCase() === t.toLowerCase());
  return found ? { person_id: found.id } : { name: t };
}

function openFile(fileId) {
  api(`/api/files/${fileId}/open`, {}).catch(() => {});
}

// Face grid with click-to-select; double click opens the photo.
function faceGrid(faces, selected, onChange) {
  return h("div", { class: "grid faces" }, faces.map((f) => {
    const el = h("div", {
      class: "face" + (selected.has(f.id) ? " selected" : ""),
      title: "Clique para selecionar; duplo clique abre a foto",
      onclick: () => {
        selected.has(f.id) ? selected.delete(f.id) : selected.add(f.id);
        el.classList.toggle("selected");
        onChange();
      },
      ondblclick: () => openFile(f.file),
    }, h("img", { src: crop(f.id), loading: "lazy", alt: "" }),
      f.source === "suggested" ? h("span", { class: "tag" }, "sugerido") : null);
    return el;
  }));
}

// --- screens ---------------------------------------------------------------

async function screenPeople() {
  await refreshPeople();
  let filter = "";
  let showHidden = false;
  const grid = h("div", { class: "grid" });
  const draw = () => {
    const f = filter.toLowerCase();
    const shown = people.filter((p) => (showHidden || !p.hidden) && label(p).toLowerCase().includes(f));
    grid.replaceChildren(...shown.map((p) =>
      h("a", { class: "card", href: `#/pessoa/${p.id}` },
        h("img", { src: crop(p.cover), loading: "lazy", alt: "" }),
        h("div", { class: "name" + (p.name ? "" : " unnamed") }, label(p)),
        h("div", { class: "muted" }, `${p.faces} rostos · ${p.files} fotos${p.hidden ? " · oculta" : ""}`))));
    if (!shown.length) grid.replaceChildren(h("div", { class: "empty" }, "Nenhuma pessoa."));
  };
  view.replaceChildren(
    h("h1", {}, "Pessoas"),
    h("div", { class: "toolbar" },
      h("input", { type: "search", placeholder: "Filtrar por nome…", oninput: (e) => { filter = e.target.value; draw(); } }),
      h("label", {}, h("input", { type: "checkbox", onchange: (e) => { showHidden = e.target.checked; draw(); } }), " mostrar ocultas"),
      h("span", { class: "muted" }, `${people.filter((p) => p.name).length} com nome, ${people.filter((p) => !p.name).length} sem nome`)),
    grid);
  draw();
}

async function screenPerson(id) {
  await refreshPeople();
  const p = await api(`/api/people/${id}`);
  const selected = new Set();
  const nameInput = h("input", { type: "text", class: "name-field", value: p.name || "", placeholder: "Nome desta pessoa", list: "people-names" });
  const moveInput = h("input", { type: "text", placeholder: "Nome ou #id de destino", list: "people-names" });
  const mergeInput = h("input", { type: "text", placeholder: "Mesclar com (nome ou #id)", list: "people-names" });
  const selInfo = h("span", { class: "muted" });
  const selBar = h("div", { class: "toolbar sticky" });

  // Next unnamed, visible group (the API lists them largest first): fast naming.
  const nextUnnamed = () => people.find((q) => !q.name && !q.hidden && q.id !== p.id);
  const save = async (goNext = false) => {
    const name = nameInput.value.trim();
    const other = name && people.find((q) => q.id !== p.id && q.name && q.name.toLowerCase() === name.toLowerCase());
    if (other && confirm(`Já existe "${other.name}". Juntar as duas pessoas?`)) {
      await api(`/api/people/${other.id}/merge`, { other_id: p.id });
      toast("Pessoas mescladas");
      location.hash = `#/pessoa/${other.id}`;
      return;
    }
    await api(`/api/people/${p.id}`, { name });
    toast(name ? `Salvo: ${name}` : "Nome removido");
    refreshStatus();
    const next = goNext && nextUnnamed();
    if (next) location.hash = `#/pessoa/${next.id}`;
  };
  // Enter = save and jump to the next unnamed group.
  nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") save(true); });

  const onSelection = () => {
    selInfo.textContent = selected.size ? `${selected.size} selecionado(s)` : "Clique nos rostos que não são desta pessoa";
    for (const b of selBar.querySelectorAll("button.needs-sel")) b.disabled = !selected.size;
  };
  const removeSelected = async () => {
    for (const face of selected) await api(`/api/faces/${face}/remove`, {});
    toast(`${selected.size} rosto(s) removido(s) desta pessoa`);
    route();
  };
  const moveSelected = async () => {
    if (!moveInput.value.trim()) return toast("Informe o destino");
    const target = resolvePerson(moveInput.value);
    for (const face of selected) await api(`/api/faces/${face}/assign`, target);
    toast(`${selected.size} rosto(s) movido(s)`);
    route();
  };
  const merge = async () => {
    const target = resolvePerson(mergeInput.value);
    if (!target.person_id) return toast("Pessoa não encontrada");
    await api(`/api/people/${p.id}/merge`, { other_id: target.person_id });
    toast("Pessoas mescladas");
    route();
  };

  selBar.append(selInfo,
    h("button", { class: "danger needs-sel", onclick: removeSelected }, "Não é esta pessoa"),
    moveInput, h("button", { class: "needs-sel", onclick: moveSelected }, "Mover para"),
    h("button", { class: "needs-sel", onclick: () => { selected.clear(); route(); } }, "Limpar seleção"),
    h("button", { onclick: () => { p.faces.forEach((f) => selected.add(f.id)); draw(); } }, "Selecionar todos"));

  const gridBox = h("div");
  const draw = () => { gridBox.replaceChildren(faceGrid(p.faces, selected, onSelection)); onSelection(); };

  view.replaceChildren(
    h("p", {}, h("a", { href: "#/pessoas" }, "← Pessoas")),
    h("div", { class: "toolbar" },
      nameInput,
      h("button", { class: "primary", onclick: () => save(true), title: "Enter" }, "Salvar e ir para o próximo sem nome"),
      h("button", { onclick: () => save(false) }, "Salvar"),
      nextUnnamed() ? h("button", { onclick: () => { location.hash = `#/pessoa/${nextUnnamed().id}`; } }, "Pular") : null,
      h("button", { onclick: async () => { await api(`/api/people/${p.id}`, { hidden: !p.hidden }); toast(p.hidden ? "Pessoa visível" : "Pessoa oculta"); route(); } },
        p.hidden ? "Mostrar pessoa" : "Ocultar pessoa"),
      mergeInput, h("button", { onclick: merge }, "Mesclar aqui")),
    h("p", { class: "muted" }, `${p.faces.length} rostos. Digite o nome e tecle Enter para ir ao próximo grupo sem nome. Clique nos rostos que não são desta pessoa para corrigir; duplo clique abre a foto.`),
    selBar, gridBox);
  draw();
  // After the view is in the DOM and painted, so the field really gets the cursor.
  requestAnimationFrame(() => { nameInput.focus(); nameInput.select(); });
}

async function screenSuggestions() {
  await refreshPeople();
  const items = await api("/api/suggestions");
  const list = h("div");
  const row = (s) => {
    const el = h("div", { class: "suggestion" },
      h("img", { src: crop(s.face), alt: "", title: "Duplo clique abre a foto", ondblclick: () => openFile(s.file) }),
      h("div", { class: "question" }, "É ", h("strong", {}, s.name), "?",
        h("div", { class: "muted" }, `semelhança ${(s.score * 100).toFixed(0)}%`)),
      h("img", { src: crop(s.cover), alt: "" }),
      h("button", { class: "ok", onclick: async () => { await api(`/api/faces/${s.face}/assign`, { person_id: s.person }); el.remove(); refreshStatus(); } }, "Sim"),
      h("button", { class: "danger", onclick: async () => { await api(`/api/faces/${s.face}/remove`, {}); el.remove(); refreshStatus(); } }, "Não"),
      h("button", { onclick: async () => {
        const who = prompt("Quem é? (nome ou #id)");
        if (!who) return;
        await api(`/api/faces/${s.face}/assign`, resolvePerson(who));
        el.remove(); refreshPeople(); refreshStatus();
      } }, "Outra pessoa…"));
    return el;
  };
  list.append(...items.map(row));
  view.replaceChildren(h("h1", {}, "É esta pessoa?"),
    items.length ? list : h("div", { class: "empty" }, "Nenhuma sugestão pendente. Elas aparecem depois de você dar nome às pessoas e indexar fotos novas."));
}

async function screenUnassigned() {
  await refreshPeople();
  const selected = new Set();
  let faces = [];
  const target = h("input", { type: "text", placeholder: "Nome ou #id", list: "people-names" });
  const info = h("span", { class: "muted" });
  const gridBox = h("div");
  const more = h("button", { onclick: () => load() }, "Carregar mais");
  const onSelection = () => { info.textContent = `${selected.size} selecionado(s)`; };
  const load = async () => {
    const page = await api(`/api/faces/unassigned?offset=${faces.length}&limit=300`);
    faces = faces.concat(page);
    more.hidden = page.length < 300;
    gridBox.replaceChildren(faces.length ? faceGrid(faces, selected, onSelection) : h("div", { class: "empty" }, "Nenhum rosto sem grupo."));
  };
  const assign = async () => {
    if (!selected.size || !target.value.trim()) return toast("Selecione rostos e informe a pessoa");
    const who = resolvePerson(target.value);
    for (const face of selected) {
      const r = await api(`/api/faces/${face}/assign`, who);
      who.person_id = r.person_id;  // a new name is created once, then reused
      delete who.name;
    }
    toast(`${selected.size} rosto(s) atribuído(s)`);
    route();
  };
  view.replaceChildren(h("h1", {}, "Rostos sem grupo"),
    h("p", { class: "muted" }, "Rostos que não se parecem o bastante com nenhum grupo. Selecione e atribua a uma pessoa."),
    h("div", { class: "toolbar sticky" }, info, target, h("button", { class: "primary", onclick: assign }, "Atribuir")),
    gridBox, h("div", { class: "toolbar" }, more));
  onSelection();
  await load();
}

// --- router ----------------------------------------------------------------

async function route() {
  const hash = location.hash || "#/pessoas";
  const [, page, arg] = hash.split("/");
  for (const a of document.querySelectorAll("nav a")) {
    a.classList.toggle("active", a.dataset.nav === page || (page === "pessoa" && a.dataset.nav === "pessoas"));
  }
  refreshStatus();
  if (page === "pessoa" && arg) return screenPerson(Number(arg));
  if (page === "sugestoes") return screenSuggestions();
  if (page === "sem-grupo") return screenUnassigned();
  return screenPeople();
}

window.addEventListener("hashchange", route);
route();
