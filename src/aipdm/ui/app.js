"use strict";
// Plain JS, no build step. User data is always rendered as text (textContent), never HTML.

const view = document.getElementById("view");
let people = [];  // cache for names/autocomplete

// --- helpers ---------------------------------------------------------------

// Children may be nested arrays, strings or null/false (skipped), same rules as h().
function kids(children) {
  return children.flat(Infinity).filter((c) => c != null && c !== false)
    .map((c) => (c instanceof Node ? c : document.createTextNode(String(c))));
}

// Always use this instead of el.replaceChildren(): the native one turns arrays and
// null into text ("[object HTMLDetailsElement]", "null").
function put(el, ...children) {
  el.replaceChildren(...kids(children));
  return el;
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  el.append(...kids(children));
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
  document.getElementById("root").textContent = s.root || "Nenhuma pasta aberta";
  document.getElementById("n-suggested").textContent = s.suggested || "";
  document.getElementById("n-unassigned").textContent = s.unassigned || "";
}

async function refreshPeople() {
  people = await api("/api/people?hidden=true");
  const list = document.getElementById("people-names");
  put(list, ...people.filter((p) => p.name).map((p) => h("option", { value: p.name })));
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
    put(grid, ...shown.map((p) =>
      h("a", { class: "card", href: `#/pessoa/${p.id}` },
        h("img", { src: crop(p.cover), loading: "lazy", alt: "" }),
        h("div", { class: "name" + (p.name ? "" : " unnamed") }, label(p)),
        h("div", { class: "muted" }, `${p.faces} rostos · ${p.files} fotos${p.hidden ? " · oculta" : ""}`))));
    if (!shown.length) put(grid, h("div", { class: "empty" }, "Nenhuma pessoa."));
  };
  put(view, 
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
  const draw = () => { put(gridBox, faceGrid(p.faces, selected, onSelection)); onSelection(); };

  put(view, 
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
  put(view, h("h1", {}, "É esta pessoa?"),
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
    put(gridBox, faces.length ? faceGrid(faces, selected, onSelection) : h("div", { class: "empty" }, "Nenhum rosto sem grupo."));
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
  put(view, h("h1", {}, "Rostos sem grupo"),
    h("p", { class: "muted" }, "Rostos que não se parecem o bastante com nenhum grupo. Selecione e atribua a uma pessoa."),
    h("div", { class: "toolbar sticky" }, info, target, h("button", { class: "primary", onclick: assign }, "Atribuir")),
    gridBox, h("div", { class: "toolbar" }, more));
  onSelection();
  await load();
}

// Snippets come as "…text [match] text…": render the brackets as <mark>, safely.
function snippet(text) {
  const out = h("div", { class: "snippet" });
  text.split(/(\[[^\]]*\])/).forEach((part) => {
    if (part.startsWith("[") && part.endsWith("]")) out.append(h("mark", {}, part.slice(1, -1)));
    else out.append(part);
  });
  return out;
}

function fmtDate(iso) {
  if (!iso) return "sem data";
  const [d, t] = iso.split("T");
  const [y, m, day] = d.split("-");
  return `${day}/${m}/${y}${t ? " " + t.slice(0, 5) : ""}`;
}

const PAGE = 120;

function photoCard(x) {
  return h("div", { class: "card photo", title: `${x.rel_path}\nDuplo clique abre a foto`, ondblclick: () => openFile(x.file_id) },
    h("img", { src: `/api/files/${x.file_id}/thumb`, loading: "lazy", alt: "" }),
    h("div", { class: "muted small" }, fmtDate(x.taken_at)),
    x.place ? h("div", { class: "muted small place", title: x.place }, `📍 ${x.place.split(", ")[0]}`) : null,
    x.snippet && !(x.place && x.snippet.includes(x.place.split(", ")[0])) ? snippet(x.snippet) : null);
}

function docRow(x) {
  return h("div", { class: "doc", title: "Duplo clique abre o documento", ondblclick: () => openFile(x.file_id) },
    h("div", {}, h("strong", {}, x.rel_path.split("/").pop()),
      h("span", { class: "muted small" }, `  ${x.kind.toUpperCase()}${x.page ? " · p. " + x.page : ""} · ${fmtDate(x.taken_at)}`)),
    x.snippet ? snippet(x.snippet) : null);
}

// Infinite scroll over /api/search: loads PAGE results at a time as the end comes into view.
// onFirst(response, ms) runs once with the first page (for totals and headers).
function pagedResults(params, onFirst) {
  const photos = h("div", { class: "grid photos" });
  const docs = h("div", { class: "docs" });
  const sentinel = h("div", { class: "muted sentinel" });
  const box = h("div", {}, photos, docs, sentinel);
  let offset = 0;
  let total = Infinity;
  let busy = false;
  const load = async () => {
    if (busy || offset >= total) return;
    busy = true;
    sentinel.textContent = "Carregando…";
    const q = new URLSearchParams(params);
    q.set("limit", PAGE);
    q.set("offset", offset);
    const t0 = performance.now();
    try {
      const r = await api(`/api/search?${q}`);
      if (offset === 0) onFirst(r, Math.round(performance.now() - t0));
      total = r.total;
      offset += r.hits.length;
      photos.append(...r.hits.filter((x) => x.kind === "image").map(photoCard));
      docs.append(...r.hits.filter((x) => x.kind !== "image").map(docRow));
      if (!r.hits.length) total = offset;
      sentinel.textContent = offset < total ? "" : (total ? `Fim — ${total} resultado(s).` : "Nenhum resultado.");
    } finally {
      busy = false;
    }
    // Page did not fill the screen yet: keep loading.
    if (offset < total && sentinel.getBoundingClientRect().top < innerHeight + 400) load();
  };
  new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) load(); },
    { rootMargin: "600px" }).observe(sentinel);
  load();
  return box;
}

function searchParams(st) {
  const q = new URLSearchParams({ text: st.text || "", order: st.order || "date", stickers: !!st.stickers });
  if (st.from) q.set("date_from", st.from);
  if (st.to) q.set("date_to", st.to);
  (st.people || []).forEach((p) => q.append("person", p.id));
  (st.kinds || []).forEach((k) => q.append("kind", k));
  for (const key of ["country", "state", "city"]) if (st[key] != null) q.set(key, st[key]);
  return q;
}

const searchState = { text: "", people: [], from: "", to: "", kinds: [], stickers: false, order: "relevance" };

async function screenSearch() {
  await refreshPeople();
  const st = searchState;
  const out = h("div");
  const chips = h("span", { class: "chips" });
  const run = async () => {
    if (!st.text.trim() && !st.people.length && !st.from && !st.to && !st.kinds.length) {
      put(out, h("div", { class: "empty" }, "Digite o que procura ou escolha pessoas, período ou tipo. Para navegar por ano ou pessoa, use a aba Fotos."));
      return;
    }
    const header = h("div");
    const list = pagedResults(searchParams(st), (r, ms) => {
      const parts = [h("p", { class: "muted" }, `${r.total} resultado(s) em ${ms} ms`)];
      if (r.without_text != null && r.total < r.without_text) {
        parts.push(h("div", { class: "notice" },
          `${r.total} combinam com "${st.text}", entre ${r.without_text} arquivos com os filtros escolhidos. `,
          h("a", { href: "#", onclick: (e) => { e.preventDefault(); st.text = ""; route(); } }, `Ver todos os ${r.without_text}`)));
      }
      if (r.person) {
        parts.push(h("h2", {}, `Fotos de ${r.person}`));
        if (r.mentions.length) {
          parts.unshift(h("details", { class: "mentions" },
            h("summary", {}, `Documentos e textos que citam ${r.person} (${r.mentions.length})`),
            h("div", { class: "docs" }, r.mentions.map(docRow))));
        }
      }
      put(header, ...parts);
    });
    put(out, header, list);
  };
  const drawChips = () => put(chips, ...st.people.map((p) =>
    h("span", { class: "chip" }, p.name, h("button", { title: "Remover", onclick: () => { st.people = st.people.filter((q) => q.id !== p.id); drawChips(); run(); } }, "×"))));
  const personInput = h("input", { type: "text", placeholder: "+ pessoa", list: "people-names", class: "small-input" });
  personInput.addEventListener("change", () => {
    const found = people.find((p) => p.name && p.name.toLowerCase() === personInput.value.trim().toLowerCase());
    if (found && !st.people.some((p) => p.id === found.id)) { st.people.push(found); drawChips(); run(); }
    else if (!found && personInput.value.trim()) toast("Pessoa não encontrada");
    personInput.value = "";
  });
  const textInput = h("input", { type: "search", class: "search-field", value: st.text, placeholder: "O que procura? Ex.: praia, bolo, carro vermelho, um nome, um CPF…" });
  textInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { st.text = textInput.value; run(); } });
  const check = (kind, text) => h("label", {}, h("input", { type: "checkbox", checked: st.kinds.includes(kind), onchange: (e) => {
    st.kinds = e.target.checked ? [...st.kinds, kind] : st.kinds.filter((k) => k !== kind); run(); } }), ` ${text}`);
  put(view, 
    h("div", { class: "toolbar" }, textInput, h("button", { class: "primary", onclick: () => { st.text = textInput.value; run(); } }, "Buscar")),
    h("div", { class: "toolbar" },
      chips, personInput,
      h("label", {}, "de ", h("input", { type: "date", value: st.from, onchange: (e) => { st.from = e.target.value; run(); } })),
      h("label", {}, "até ", h("input", { type: "date", value: st.to, onchange: (e) => { st.to = e.target.value; run(); } })),
      check("image", "Fotos"), check("pdf", "PDF"), check("docx", "Word"),
      h("label", {}, h("input", { type: "checkbox", checked: st.stickers, onchange: (e) => { st.stickers = e.target.checked; run(); } }), " figurinhas"),
      h("select", { onchange: (e) => { st.order = e.target.value; run(); } },
        h("option", { value: "relevance", selected: st.order === "relevance" }, "Mais relevantes"),
        h("option", { value: "date", selected: st.order === "date" }, "Mais recentes"))),
    out);
  drawChips();
  run();
  requestAnimationFrame(() => textInput.focus());
}

const MONTHS = Array.from({ length: 12 }, (_, i) =>
  new Intl.DateTimeFormat("pt-BR", { month: "long" }).format(new Date(2000, i, 1)));
const monthName = (ym) => MONTHS[Number(ym.slice(5, 7)) - 1];
const lastDay = (ym) => new Date(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)), 0).getDate();

// Browsing state survives switching tabs.
const browseState = { title: "Todas as fotos", people: [], from: "", to: "", stickers: false, country: null, state: null, city: null };

async function screenBrowse() {
  await refreshPeople();
  const st = browseState;
  const main = h("div", { class: "browse-main" });
  const side = h("aside", { class: "tree" });

  const show = (title, filters) => {
    Object.assign(st, { title, people: [], from: "", to: "", country: null, state: null, city: null }, filters);
    for (const el of side.querySelectorAll(".node.active")) el.classList.remove("active");
    draw();
  };
  const node = (text, count, filters, extraClass = "") => {
    // Not a link: inside <summary> the same click also expands/collapses the branch.
    const el = h("span", { role: "button", tabindex: "0", class: `node ${extraClass}`, onclick: () => {
      show(filters.title || text, filters);
      el.classList.add("active");
    } }, h("span", {}, text), h("span", { class: "count" }, count));
    return el;
  };
  const yearNodes = (tree, people, prefix) => tree.years.map((y) =>
    h("details", {},
      h("summary", {}, node(y.year, y.count, { title: `${prefix}${y.year}`, people, from: `${y.year}-01-01`, to: `${y.year}-12-31` })),
      y.months.map((m) => node(monthName(m.month), m.count,
        { title: `${prefix}${monthName(m.month)} de ${y.year}`, people, from: `${m.month}-01`, to: `${m.month}-${lastDay(m.month)}` }, "month"))));

  const placeNodes = (countries) => countries.map((c) =>
    h("details", {},
      h("summary", {}, node(c.country, c.count, { title: c.country, country: c.country })),
      c.states.map((s) => h("details", {},
        h("summary", {}, node(s.state || "(sem estado)", s.count, { title: `${s.state}, ${c.country}`, country: c.country, state: s.state })),
        s.cities.map((x) => node(x.city, x.count,
          { title: `${x.city}, ${s.state}`, country: c.country, state: s.state, city: x.city }, "city"))))));

  const drawTree = async () => {
    const [tree, places] = await Promise.all([
      api(`/api/tree?stickers=${st.stickers}`), api(`/api/places?stickers=${st.stickers}`)]);
    const named = people.filter((p) => p.name && !p.hidden).sort((a, b) => a.name.localeCompare(b.name, "pt-BR"));
    const personNodes = named.map((p) => {
      const d = h("details", {}, h("summary", {}, node(p.name, p.files, { title: p.name, people: [p] })));
      d.addEventListener("toggle", async () => {  // years of this person, fetched on first open
        if (!d.open || d.dataset.loaded) return;
        d.dataset.loaded = "1";
        const t = await api(`/api/tree?person=${p.id}&stickers=${st.stickers}`);
        d.append(...yearNodes(t, [p], `${p.name} · `));
      });
      return d;
    });
    put(side, 
      node("Todas as fotos", tree.total, { title: "Todas as fotos" }, "all"),
      h("h3", {}, "Por ano"), ...yearNodes(tree, [], ""),
      tree.undated ? h("div", { class: "muted small" }, `${tree.undated} sem data`) : null,
      h("h3", {}, "Por local"),
      places.length ? placeNodes(places) : h("div", { class: "muted small" }, "Nenhuma foto com localização (GPS) nesta pasta."),
      h("h3", {}, "Por pessoa"),
      personNodes.length ? personNodes : h("div", { class: "muted small" }, "Dê nome às pessoas na aba Pessoas."),
      h("label", { class: "small" }, h("input", { type: "checkbox", checked: st.stickers,
        onchange: (e) => { st.stickers = e.target.checked; drawTree(); draw(); } }), " incluir figurinhas"));
  };

  const draw = () => {
    const header = h("div", { class: "toolbar" }, h("h1", {}, st.title));
    const q = searchParams({ ...st, kinds: ["image"], order: "date" });
    put(main, header, pagedResults(q, (r) => header.append(h("span", { class: "muted" }, `${r.total} foto(s)`))));
  };

  put(view, h("div", { class: "browse" }, side, main));
  await drawTree();
  draw();
}

let indexPoll = null;

function stageTable(seconds) {
  const names = { hash: "hash", decode: "leitura de imagem", thumb: "thumbnail", text: "texto PDF/DOCX", faces: "rostos", clip: "CLIP", ocr: "OCR", grouping: "agrupamento de rostos" };
  return h("table", { class: "stages" }, Object.entries(seconds).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
    h("tr", {}, h("td", {}, names[k] || k), h("td", { class: "num" }, `${v.toFixed(1)} s`))));
}

function indexPanel(job) {
  if (!job || (!job.running && !job.summary && !job.error)) return h("div");
  const pct = job.total ? Math.round((100 * job.done) / job.total) : 0;
  if (job.running) {
    return h("div", { class: "panel" },
      h("strong", {}, job.total ? `Processando ${job.done} de ${job.total} arquivos (${pct}%)` : "Varrendo a pasta…"),
      h("progress", { max: job.total || 1, value: job.done }),
      h("div", { class: "muted" }, `${Math.round(job.seconds)} s. A busca já funciona com o que estiver pronto.`),
      h("button", { onclick: () => api("/api/index/cancel", {}) }, "Parar (termina os arquivos em andamento)"));
  }
  if (job.error) return h("div", { class: "panel" }, h("strong", { class: "danger" }, "Erro na indexação: "), job.error);
  const s = job.summary, sc = s.scan, g = s.grouping;
  return h("div", { class: "panel" },
    h("strong", {}, s.interrupted ? "Indexação interrompida — reabra para continuar de onde parou." : "Indexação concluída."),
    h("div", {}, `Varredura: ${sc.new} novos, ${sc.changed} alterados, ${sc.missing} ausentes, ${sc.reappeared} reapareceram, ${sc.unchanged} sem mudança.`),
    h("div", {}, `Processados: ${s.processed} de ${s.pending} (erros: ${s.errors}) em ${Math.round(job.seconds)} s.`),
    g ? h("div", {}, `Rostos: ${g.auto} atribuídos automaticamente, ${g.suggested} sugeridos, ${g.clustered} agrupados em ${g.new_people} novas pessoas.`) : null,
    Object.keys(s.stage_seconds).length ? stageTable(s.stage_seconds) : null);
}

function ago(ts) {
  if (!ts) return "ainda não verificada";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 60 ? `há ${s} s` : `há ${Math.round(s / 60)} min`;
}

function monitorPanel(m) {
  const toggle = h("input", { type: "checkbox", checked: m.enabled, onchange: async (e) => {
    await api("/api/monitor", { enabled: e.target.checked });
    toast(e.target.checked ? "Monitoramento ligado" : "Monitoramento desligado");
    route();
  } });
  const detail = !m.enabled ? "Desligado: use Re-escanear quando quiser atualizar."
    : m.waiting ? `${m.changes} mudança(s) encontrada(s); aguardando a cópia terminar para indexar.`
    : `Verifica a pasta a cada ${Math.round(m.interval || 60)} s enquanto o app está aberto. Última verificação: ${ago(m.last_check)}.`;
  return h("div", { class: "toolbar" }, h("label", {}, toggle, " Monitorar esta pasta"), h("span", { class: "muted" }, detail));
}

async function screenFolders() {
  const [status, folders, job] = await Promise.all([api("/api/status"), api("/api/folders"), api("/api/index")]);
  const monitor = status.root ? await api("/api/monitor") : null;
  const panel = h("div");
  const drawJob = (j) => put(panel, indexPanel(j));
  const poll = () => {
    clearInterval(indexPoll);
    indexPoll = setInterval(async () => {
      if (!location.hash.startsWith("#/pastas")) return clearInterval(indexPoll);
      const j = await api("/api/index");
      drawJob(j);
      if (!j.running) { clearInterval(indexPoll); refreshStatus(); }
    }, 1000);
  };
  const reindex = async (force, retryErrors = false) => {
    if (force && !confirm("Reprocessar todos os arquivos? Pode levar bastante tempo. Nomes e correções de pessoas são mantidos.")) return;
    drawJob(await api("/api/index", { force, retry_errors: retryErrors }));
    poll();
  };
  const openPath = async (path) => {
    if (!path) return;
    const r = await api("/api/folders/open", { path });
    toast(r.new ? "Nova pasta: indexação iniciada" : "Pasta aberta");
    route();
  };
  const pathInput = h("input", { type: "text", placeholder: "/caminho/da/pasta", class: "search-field" });
  const chooser = window.pywebview && window.pywebview.api
    ? h("button", { class: "primary", onclick: async () => openPath(await window.pywebview.api.choose_folder()) }, "Escolher pasta…")
    : h("span", {}, pathInput, h("button", { class: "primary", onclick: () => openPath(pathInput.value.trim()) }, "Abrir e indexar"));

  put(view, 
    status.root ? h("div", {},
      h("h1", {}, "Pasta atual"),
      h("p", {}, h("strong", {}, status.root), h("span", { class: "muted" }, ` · ${status.files} fotos e documentos`)),
      h("div", { class: "toolbar" },
        h("button", { class: "primary", onclick: () => reindex(false), disabled: job.running }, "Re-escanear pasta"),
        h("button", { onclick: () => reindex(true), disabled: job.running }, "Reprocessar tudo"),
        status.errors ? h("button", { onclick: () => reindex(false, true), disabled: job.running,
          title: "Arquivos que falharam antes (por exemplo, antes de uma atualização do app)" },
          `Tentar de novo ${status.errors} com erro`) : null),
      h("p", { class: "muted" }, "Re-escanear processa só o que foi adicionado ou mudou. A pasta nunca é alterada."),
      monitorPanel(monitor),
      panel)
      : h("div", {}, h("h1", {}, "Bem-vindo"),
        h("p", {}, "Escolha a pasta com suas fotos e documentos (por exemplo, o backup do WhatsApp). Ela é só lida, nunca alterada, e tudo roda neste computador, sem internet.")),
    h("h2", {}, status.root ? "Abrir outra pasta" : "Escolher pasta"),
    h("div", { class: "toolbar" }, chooser),
    folders.length ? h("h2", {}, "Pastas recentes") : null,
    folders.length ? h("div", { class: "docs" }, folders.map((f) =>
      h("div", { class: "doc" },
        h("strong", {}, f.root), f.current ? h("span", { class: "badge" }, "atual") : null,
        h("div", { class: "muted small" }, `${f.files} arquivos · última indexação ${f.last_index ? fmtDate(f.last_index) : "—"}`),
        f.current ? null : h("button", { onclick: async () => { await api("/api/folders/open", { id: f.id }); toast("Pasta aberta"); route(); } }, "Abrir")))) : null);
  drawJob(job);
  if (job.running) poll();
}

// --- router ----------------------------------------------------------------

async function route() {
  const status = await api("/api/status");
  const hash = location.hash || (status.root ? "#/busca" : "#/pastas");
  const [, page, arg] = hash.split("/");
  for (const a of document.querySelectorAll("nav a")) {
    a.classList.toggle("active", a.dataset.nav === page || (page === "pessoa" && a.dataset.nav === "pessoas"));
  }
  refreshStatus();
  if (!status.root && page !== "pastas") { location.hash = "#/pastas"; return; }
  if (page === "busca") return screenSearch();
  if (page === "fotos") return screenBrowse();
  if (page === "pastas") return screenFolders();
  if (page === "pessoa" && arg) return screenPerson(Number(arg));
  if (page === "sugestoes") return screenSuggestions();
  if (page === "sem-grupo") return screenUnassigned();
  if (page === "pessoas") return screenPeople();
  location.hash = status.root ? "#/busca" : "#/pastas";
}

// Indexing can start by itself (folder monitoring): show it in the header on every screen.
setInterval(async () => {
  const job = await api("/api/index").catch(() => null);
  const el = document.getElementById("indexing");
  if (job && job.running) {
    el.textContent = job.total ? `⟳ Indexando ${job.done}/${job.total}` : "⟳ Verificando a pasta…";
    el.dataset.running = "1";
  } else if (el.dataset.running) {
    delete el.dataset.running;
    el.textContent = "";
    toast("Indexação concluída");
    refreshStatus();
  }
}, 3000);

window.addEventListener("hashchange", route);
route();
