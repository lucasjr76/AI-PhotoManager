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
}

// --- person-name autocomplete ---------------------------------------------------------
// Any <input data-people> gets it (WebKitGTK's <datalist> popup is unreliable). One shared
// dropdown; matching ignores case and accents and finds any part of the name.
const foldText = (t) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function peopleMatches(query) {
  const q = foldText(query.trim());
  if (!q || q.startsWith("#")) return [];
  const words = q.split(/\s+/);
  const ranked = [];
  for (const p of people) {
    if (!p.name) continue;
    const name = foldText(p.name);
    const nameWords = name.split(/\s+/);
    // every typed word must start some word of the name (or appear inside it)
    if (!words.every((w) => nameWords.some((nw) => nw.startsWith(w)) || name.includes(w))) continue;
    const rank = name.startsWith(q) ? 0 : words.every((w) => nameWords.some((nw) => nw.startsWith(w))) ? 1 : 2;
    ranked.push([rank, -p.files, p]);
  }
  ranked.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2].name.localeCompare(b[2].name, "pt-BR"));
  return ranked.slice(0, 8).map((r) => r[2]);
}

const suggestBox = h("div", { class: "suggest-box", hidden: true, role: "listbox" });
document.body.append(suggestBox);
let suggestFor = null;
let suggestIndex = 0;
let suggestItems = [];
let suggestNavigated = false;  // the user moved through the list with the arrow keys

function closeSuggest() {
  suggestBox.hidden = true;
  suggestFor = null;
}

function pickSuggestion(input, person) {
  input.value = person.name;
  closeSuggest();
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function drawSuggest(input) {
  suggestItems = peopleMatches(input.value);
  if (!suggestItems.length) { closeSuggest(); return; }
  suggestFor = input;
  suggestIndex = Math.min(suggestIndex, suggestItems.length - 1);
  put(suggestBox, suggestItems.map((p, n) => h("div", {
    class: "suggest-item" + (n === suggestIndex ? " active" : ""), role: "option",
    onmousedown: (e) => { e.preventDefault(); pickSuggestion(input, p); },
  }, p.cover ? h("img", { src: crop(p.cover), alt: "" }) : null,
    h("span", {}, p.name), h("span", { class: "muted small" }, `${p.files} fotos`))));
  const r = input.getBoundingClientRect();
  Object.assign(suggestBox.style, { left: `${r.left}px`, top: `${r.bottom + 2}px`, minWidth: `${r.width}px` });
  suggestBox.hidden = false;
}

document.addEventListener("input", (e) => {
  if (e.target.matches && e.target.matches("input[data-people]")) {
    suggestIndex = 0;
    suggestNavigated = false;
    drawSuggest(e.target);
  }
});
document.addEventListener("focusout", (e) => { if (e.target === suggestFor) closeSuggest(); });
// Capture phase: runs before the field's own Enter handler, so Enter picks the
// highlighted name and that handler then sees the completed value.
document.addEventListener("keydown", (e) => {
  if (!suggestFor || e.target !== suggestFor || suggestBox.hidden) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    e.stopPropagation();
    const step = e.key === "ArrowDown" ? 1 : -1;
    suggestIndex = (suggestIndex + step + suggestItems.length) % suggestItems.length;
    suggestNavigated = true;
    drawSuggest(suggestFor);
  } else if (e.key === "Enter" || e.key === "Tab") {
    const exact = suggestItems.find((p) => foldText(p.name) === foldText(suggestFor.value.trim()));
    // data-people="free": a field where a brand-new name is normal (naming a group), so
    // Enter keeps what was typed unless the user picked from the list with the arrows.
    const free = suggestFor.dataset.people === "free" && !suggestNavigated;
    if (e.key === "Tab" || (!exact && !free)) {
      if (e.key === "Tab") e.preventDefault();
      pickSuggestion(suggestFor, suggestItems[suggestIndex]);
    } else {
      closeSuggest();
    }
  } else if (e.key === "Escape") {
    e.stopPropagation();
    closeSuggest();
  }
}, true);
window.addEventListener("resize", closeSuggest);

// "Maria" -> existing person by name; "#12" -> person 12; else a new name.
function resolvePerson(text) {
  const t = text.trim();
  const byId = t.match(/^#?(\d+)$/);
  if (byId) return { person_id: Number(byId[1]) };
  const found = people.find((p) => p.name && p.name.toLowerCase() === t.toLowerCase());
  return found ? { person_id: found.id } : { name: t };
}

function openFile(fileId) {
  api(`/api/files/${fileId}/open`, {}).catch(() => {});  // api() already shows the error
}

function fmtSize(bytes) {
  return bytes > 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

const DATE_SOURCES = {
  whatsapp_android: "nome do arquivo (WhatsApp)", whatsapp_desktop: "nome do arquivo (WhatsApp)",
  camera_name: "nome do arquivo (câmera)", exif: "EXIF da foto", document: "metadados do documento",
  mtime: "data de modificação do arquivo",
};

// Zoom and pan for the viewer: wheel zooms at the cursor, drag pans, double click toggles
// fit / 2.5x. `target` (image + face boxes) is laid out "fit to screen"; zoom is a CSS
// transform, so the boxes follow. canPan(e) lets face boxes and drawing take the mouse.
function zoomable(target, stage, canPan) {
  let scale = 1, x = 0, y = 0, drag = null;
  const apply = () => {
    target.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    stage.classList.toggle("zoomed", scale > 1);
  };
  const zoomAt = (factor, cx, cy) => {
    const next = Math.min(8, Math.max(1, scale * factor));
    const rect = target.getBoundingClientRect();
    // keep the point under the cursor fixed while scaling
    const ox = cx - (rect.left + rect.width / 2), oy = cy - (rect.top + rect.height / 2);
    x -= ox * (next / scale - 1);
    y -= oy * (next / scale - 1);
    scale = next;
    if (scale === 1) x = y = 0;
    apply();
  };
  stage.addEventListener("wheel", (e) => {
    e.preventDefault();
    zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX, e.clientY);
  }, { passive: false });
  target.addEventListener("dblclick", (e) => { if (canPan(e)) zoomAt(scale > 1 ? 1 / scale : 2.5, e.clientX, e.clientY); });
  target.addEventListener("mousedown", (e) => {
    if (scale === 1 || !canPan(e)) return;
    e.preventDefault();
    drag = { sx: e.clientX - x, sy: e.clientY - y };
  });
  const move = (e) => { if (drag) { x = e.clientX - drag.sx; y = e.clientY - drag.sy; apply(); } };
  const release = () => { drag = null; };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", release);
  const center = () => { const r = stage.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
  return {
    reset: () => { scale = 1; x = y = 0; apply(); },
    zoomIn: () => zoomAt(1.25, ...center()),
    zoomOut: () => zoomAt(1 / 1.25, ...center()),
    destroy: () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", release);
    },
  };
}

function faceLabel(f) {
  if (!f.person_id) return "?";
  return f.name || `Sem nome #${f.person_id}`;
}

function faceKind(f) {
  if (!f.person_id || !f.name) return "unknown";
  if (f.source === "auto") return "ai";
  if (f.source === "suggested") return "suggested";
  return "named";
}

// In-app viewer. items: [{file_id}], shows items[index].
// Keys: ←/→ other files, ↑/↓ PDF pages, + − 0 zoom, R show/hide faces, Esc closes.
function openViewer(items, index) {
  let i = index;
  let page = 1;
  let pages = 1;
  let seq = 0;  // ignore responses for files the user already moved past
  let lastInfo = null;
  let faceData = null;  // {width, height, faces} of the current photo
  let selected = null;  // a face id, or {draft: [x, y, w, h]} for a box being marked
  let drawing = false;
  let showFaces = true;
  const img = h("img", { class: "viewer-img", alt: "", draggable: "false" });
  const boxes = h("div", { class: "face-boxes" });
  const canvas = h("div", { class: "viewer-canvas" }, img, boxes);
  const text = h("pre", { class: "viewer-text", hidden: true });
  const failed = h("div", { class: "empty", hidden: true }, "Não foi possível abrir este arquivo.");
  const side = h("aside", { class: "viewer-info" });
  const stage = h("div", { class: "viewer-stage" }, canvas, text, failed);
  // "Apreciar" mode: photo only, no face boxes and no side panel. A switch fixed in the
  // corner (not a click on the photo, which is for faces); remembered between photos
  // and sessions. Key I toggles it too.
  const cleanSwitch = h("input", { type: "checkbox" });
  const cleanToggle = h("label", { class: "clean-toggle", title: "Mostrar ou esconder quadros dos rostos e informações (tecla I)" },
    cleanSwitch, " Marcações e informações");
  // Shown only in "apreciar" mode, where the side panel (with its buttons) is hidden.
  const floating = [
    h("button", { class: "float-nav prev", title: "Anterior (←)", onclick: () => go(-1) }, "‹"),
    h("button", { class: "float-nav next", title: "Próximo (→)", onclick: () => go(1) }, "›"),
    h("button", { class: "float-nav shut", title: "Fechar (Esc)", onclick: () => close() }, "✕"),
  ];
  const overlay = h("div", { class: "viewer", role: "dialog", "aria-modal": "true" }, stage, side, cleanToggle, floating);
  const setClean = (clean) => {
    overlay.classList.toggle("clean", clean);
    cleanSwitch.checked = !clean;
    try { localStorage.setItem("aipdm.viewerClean", clean ? "1" : "0"); } catch { /* private mode */ }
    if (clean) { selected = null; drawing = false; canvas.classList.remove("drawing"); }
    drawBoxes();
  };
  cleanSwitch.addEventListener("change", () => setClean(!cleanSwitch.checked));
  const zoom = zoomable(canvas, stage, (e) => !drawing && !e.target.closest(".face-box"));
  stage.addEventListener("click", (e) => { if (e.target === stage) close(); });
  img.addEventListener("error", () => { if (img.getAttribute("src")) { canvas.hidden = true; failed.hidden = false; } });

  const go = (delta) => {
    const next = i + delta;
    if (next < 0 || next >= items.length) return;
    i = next;
    page = 1;
    show();
  };
  const turn = (delta) => {
    const next = page + delta;
    if (next < 1 || next > pages) return;
    page = next;
    zoom.reset();
    img.src = `/api/files/${items[i].file_id}/view?page=${page}`;
    drawSide(lastInfo);
  };
  const row = (label, value) => value ? h("tr", {}, h("th", {}, label), h("td", {}, value)) : null;

  // --- face boxes --------------------------------------------------------------
  const pct = (v, total) => `${(100 * v) / total}%`;
  const placeBox = (el, [x, y, w, h]) => {
    Object.assign(el.style, { left: pct(x, faceData.width), top: pct(y, faceData.height),
      width: pct(w, faceData.width), height: pct(h, faceData.height) });
    return el;
  };
  const drawBoxes = () => {
    if (!faceData || !showFaces || overlay.classList.contains("clean")) { put(boxes); return; }
    put(boxes, faceData.faces.map((f) => placeBox(h("div", {
      class: `face-box ${faceKind(f)}${selected === f.id ? " selected" : ""}`,
      title: faceLabel(f),
      onclick: (e) => { e.stopPropagation(); selected = f.id; drawBoxes(); drawSide(lastInfo); },
    }, h("span", { class: "face-name" }, faceLabel(f),
      f.source === "auto" ? h("span", { class: "ai-badge" }, `IA ${Math.round((f.score || 0) * 100)}%`) : null)), f.bbox)),
    selected && selected.draft ? placeBox(h("div", { class: "face-box draft" }), selected.draft) : null);
  };
  const loadFaces = async (fileId, mine) => {
    const data = await api(`/api/files/${fileId}/faces`).catch(() => null);
    if (mine !== seq) return;
    faceData = data;
    drawBoxes();
    drawSide(lastInfo);
  };
  const refreshFaces = async () => {
    const mine = seq;
    await Promise.all([refreshPeople(), loadFaces(items[i].file_id, mine)]);
    const info = await api(`/api/files/${items[i].file_id}/info`);
    if (mine === seq) { lastInfo = info; drawSide(info); }
    refreshStatus();
  };

  // Drawing a new box: press, drag, release over the photo.
  canvas.addEventListener("mousedown", (e) => {
    if (!drawing || !faceData) return;
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const toPx = (cx, cy) => [
      Math.min(Math.max((cx - rect.left) / rect.width, 0), 1) * faceData.width,
      Math.min(Math.max((cy - rect.top) / rect.height, 0), 1) * faceData.height];
    const [x0, y0] = toPx(e.clientX, e.clientY);
    const drag = (ev) => {
      const [x1, y1] = toPx(ev.clientX, ev.clientY);
      selected = { draft: [Math.round(Math.min(x0, x1)), Math.round(Math.min(y0, y1)),
        Math.round(Math.abs(x1 - x0)), Math.round(Math.abs(y1 - y0))] };
      drawBoxes();
    };
    const done = () => {
      window.removeEventListener("mousemove", drag);
      window.removeEventListener("mouseup", done);
      drawing = false;
      canvas.classList.remove("drawing");
      if (!selected || !selected.draft || selected.draft[2] < 8 || selected.draft[3] < 8) {
        selected = null;
        toast("Arraste sobre o rosto para marcá-lo");
      }
      drawBoxes();
      drawSide(lastInfo);
    };
    window.addEventListener("mousemove", drag);
    window.addEventListener("mouseup", done);
  });

  const startDrawing = () => {
    if (!faceData) return;
    zoom.reset();
    drawing = true;
    selected = null;
    canvas.classList.add("drawing");
    showFaces = true;
    drawBoxes();
    toast("Arraste um retângulo sobre o rosto");
  };

  // Side panel section for the selected face (or the box being marked).
  const facePanel = () => {
    if (!selected || !faceData) return null;
    const who = h("input", { type: "text", placeholder: "Quem é? (nome ou #id)", "data-people": "1" });
    const fileId = items[i].file_id;
    if (selected.draft) {
      const save = async () => {
        if (!who.value.trim()) return toast("Digite quem é");
        const r = await api(`/api/files/${fileId}/faces`, { bbox: selected.draft, ...resolvePerson(who.value) });
        toast(r.signature ? "Rosto marcado" : "Rosto marcado, mas o modelo não o reconheceu: não servirá de referência para outras fotos");
        selected = r.face_id;
        refreshFaces();
      };
      who.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
      requestAnimationFrame(() => who.focus());
      return h("div", { class: "face-panel" },
        h("strong", {}, "Novo rosto marcado"),
        h("div", { class: "toolbar" }, who, h("button", { class: "primary", onclick: save }, "Salvar"),
          h("button", { onclick: () => { selected = null; drawBoxes(); drawSide(lastInfo); } }, "Cancelar")));
    }
    const f = faceData.faces.find((x) => x.id === selected);
    if (!f) return null;
    const act = async (fn, msg) => { await fn(); toast(msg); refreshFaces(); };
    const assign = () => {
      if (!who.value.trim()) return toast("Digite quem é");
      act(() => api(`/api/faces/${f.id}/assign`, resolvePerson(who.value)), "Rosto atribuído");
    };
    who.addEventListener("keydown", (e) => { if (e.key === "Enter") assign(); });
    requestAnimationFrame(() => who.focus());
    const status = !f.person_id ? "Rosto sem pessoa"
      : f.source === "auto" ? `${faceLabel(f)} (identificado pela IA, ${Math.round((f.score || 0) * 100)}%)`
      : f.source === "suggested" ? `Sugestão: ${faceLabel(f)}?`
      : faceLabel(f);
    return h("div", { class: "face-panel" },
      h("div", { class: "face-head" }, h("img", { src: crop(f.id), alt: "" }),
        h("div", {}, h("strong", {}, status),
          f.manual ? h("div", { class: "muted small" }, f.signature ? "marcado à mão" : "marcado à mão · sem assinatura (não serve de referência)") : null)),
      h("div", { class: "toolbar" }, who, h("button", { class: "primary", onclick: assign }, f.person_id ? "Trocar" : "Salvar")),
      h("div", { class: "toolbar" },
        f.person_id && f.source !== "user" && f.name ? h("button", { class: "ok",
          onclick: () => act(() => api(`/api/faces/${f.id}/assign`, { person_id: f.person_id }), "Confirmado") }, `Sim, é ${f.name}`) : null,
        f.person_id && f.name ? h("button", { class: "danger",
          onclick: () => act(() => api(`/api/faces/${f.id}/remove`, {}), "Removido desta pessoa") }, `Não é ${f.name}`) : null,
        f.manual ? h("button", { class: "danger",
          onclick: () => act(async () => { await api(`/api/faces/${f.id}/delete`, {}); selected = null; }, "Marcação excluída") }, "Excluir marcação") : null,
        f.person_id ? h("a", { class: "chip", href: `#/pessoa/${f.person_id}`, onclick: close }, "ver pessoa") : null));
  };

  function drawSide(info) {
    const faceList = faceData && faceData.faces.length ? h("div", { class: "face-list" },
      faceData.faces.map((f) => h("button", { class: `face-chip ${faceKind(f)}${selected === f.id ? " selected" : ""}`,
        onclick: () => { selected = f.id; showFaces = true; drawBoxes(); drawSide(lastInfo); } },
        h("img", { src: crop(f.id), alt: "" }), faceLabel(f)))) : null;
    put(side,
      h("div", { class: "toolbar" },
        h("button", { onclick: () => go(-1), disabled: i === 0, title: "Anterior (←)" }, "‹"),
        h("span", { class: "muted" }, `${i + 1} de ${items.length}`),
        h("button", { onclick: () => go(1), disabled: i === items.length - 1, title: "Próximo (→)" }, "›"),
        h("span", { class: "spacer" }),
        h("button", { onclick: zoom.zoomOut, title: "Diminuir (−)" }, "−"),
        h("button", { onclick: zoom.reset, title: "Ajustar à tela (0)" }, "⤢"),
        h("button", { onclick: zoom.zoomIn, title: "Ampliar (+)" }, "+"),
        h("button", { class: "close", onclick: close, title: "Fechar (Esc)" }, "✕")),
      info ? [
        h("h2", {}, info.rel_path.split("/").pop()),
        faceData ? h("div", { class: "toolbar" },
          h("strong", {}, `Rostos (${faceData.faces.length})`),
          h("button", { onclick: () => { showFaces = !showFaces; drawBoxes(); }, title: "Mostrar/ocultar quadros (R)" }, showFaces ? "Ocultar quadros" : "Mostrar quadros"),
          h("button", { class: drawing ? "primary" : "", onclick: startDrawing, title: "Para rostos que não foram detectados" }, "+ Marcar rosto")) : null,
        faceList,
        facePanel(),
        info.kind === "pdf" && pages > 1 ? h("div", { class: "toolbar" },
          h("button", { onclick: () => turn(-1), disabled: page === 1, title: "↑" }, "‹ Página"),
          h("span", {}, `${page} de ${pages}`),
          h("button", { onclick: () => turn(1), disabled: page === pages, title: "↓" }, "Página ›")) : null,
        h("table", { class: "exif" },
          row("Data", info.taken_at ? fmtDate(info.taken_at) : "sem data"),
          row("Origem da data", DATE_SOURCES[info.date_source]),
          row("Local", info.place),
          row("Coordenadas", info.lat != null ? `${info.lat.toFixed(5)}, ${info.lon.toFixed(5)}` : null),
          (info.exif || []).map(([label, value]) => row(label, value)),
          row("Dimensões", info.width ? `${info.width} × ${info.height} px (${(info.width * info.height / 1e6).toFixed(1)} MP)` : null),
          row("Formato", info.format || info.kind.toUpperCase()),
          row("Tamanho", fmtSize(info.size)),
          row("Páginas", info.pages)),
        h("div", { class: "muted small path" }, info.rel_path),
        h("button", { onclick: () => openFile(info.id) }, "Abrir no visualizador do sistema"),
        h("div", { class: "muted small" }, "Roda do mouse: zoom · arrastar: mover · duplo clique: 2,5× / ajustar · clique num quadro: quem é"),
      ] : h("div", { class: "muted" }, "Carregando…"));
  }

  const show = async () => {
    const mine = ++seq;
    const item = items[i];
    lastInfo = null;
    faceData = null;
    selected = null;
    drawing = false;
    canvas.classList.remove("drawing");
    failed.hidden = true;
    zoom.reset();
    put(boxes);
    drawSide(null);
    if (!people.length) refreshPeople();
    const info = await api(`/api/files/${item.file_id}/info`);
    if (mine !== seq) return;
    lastInfo = info;
    pages = info.pages || 1;
    if (info.kind === "docx") {
      canvas.hidden = true;
      img.removeAttribute("src");
      text.hidden = false;
      text.textContent = info.text || "(documento sem texto)";
    } else {
      text.hidden = true;
      canvas.hidden = false;
      img.src = `/api/files/${item.file_id}/view?page=${page}`;
    }
    drawSide(info);
    if (info.kind === "image") loadFaces(item.file_id, mine);
    const next = items[i + 1];  // warm the next preview so → is instant
    if (next && next.kind !== "docx") new Image().src = `/api/files/${next.file_id}/view`;
  };

  const onKey = (e) => {
    if (e.target.tagName === "INPUT") {  // typing a name: only Esc leaves the field
      if (e.key === "Escape") { e.target.blur(); selected = null; drawBoxes(); drawSide(lastInfo); }
      return;
    }
    const actions = {
      ArrowLeft: () => go(-1), ArrowRight: () => go(1), ArrowUp: () => turn(-1), ArrowDown: () => turn(1),
      PageUp: () => turn(-1), PageDown: () => turn(1), Escape: close,
      "+": zoom.zoomIn, "=": zoom.zoomIn, "-": zoom.zoomOut, "0": zoom.reset,
      r: () => { showFaces = !showFaces; drawBoxes(); drawSide(lastInfo); },
      i: () => setClean(!overlay.classList.contains("clean")),
    };
    if (actions[e.key]) { e.preventDefault(); actions[e.key](); }
  };
  function close() {
    document.removeEventListener("keydown", onKey);
    zoom.destroy();
    document.body.classList.remove("viewer-open");
    overlay.remove();
  }
  document.addEventListener("keydown", onKey);
  document.body.classList.add("viewer-open");  // no page scrolling behind the viewer
  document.body.append(overlay);
  let startClean = false;
  try { startClean = localStorage.getItem("aipdm.viewerClean") === "1"; } catch { /* private mode */ }
  setClean(startClean);
  show();
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
      ondblclick: () => openViewer(faces.map((x) => ({ file_id: x.file })), faces.indexOf(f)),
    }, h("img", { src: crop(f.id), loading: "lazy", alt: "" }),
      f.source === "suggested" ? h("span", { class: "tag" }, "sugerido")
        : f.source === "auto" ? h("span", { class: "tag ai", title: "Identificado automaticamente pela IA, ainda não confirmado por você" },
          `IA ${Math.round((f.score || 0) * 100)}%`) : null);
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
  const nameInput = h("input", { type: "text", class: "name-field", value: p.name || "", placeholder: "Nome desta pessoa", "data-people": "free" });
  const moveInput = h("input", { type: "text", placeholder: "Nome ou #id de destino", "data-people": "1" });
  const mergeInput = h("input", { type: "text", placeholder: "Mesclar com (nome ou #id)", "data-people": "1" });
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
  const confirmSelected = async () => {
    for (const face of selected) await api(`/api/faces/${face}/assign`, { person_id: p.id });
    toast(`${selected.size} rosto(s) confirmado(s)`);
    route();
  };
  const aiCount = p.faces.filter((f) => f.source === "auto").length;
  let onlyAi = false;
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
    aiCount ? h("label", { title: "Rostos que a IA atribuiu sozinha; revise principalmente crianças" },
      h("input", { type: "checkbox", onchange: (e) => { onlyAi = e.target.checked; selected.clear(); draw(); } }),
      ` só IA (${aiCount})`) : null,
    h("button", { class: "ok needs-sel", onclick: confirmSelected, title: "Marca como confirmado por você; o selo IA some" }, "Confirmar"),
    h("button", { class: "danger needs-sel", onclick: removeSelected }, "Não é esta pessoa"),
    moveInput, h("button", { class: "needs-sel", onclick: moveSelected }, "Mover para"),
    h("button", { class: "needs-sel", onclick: () => { selected.clear(); route(); } }, "Limpar seleção"),
    h("button", { onclick: () => { shown().forEach((f) => selected.add(f.id)); draw(); } }, "Selecionar todos"));

  const gridBox = h("div");
  const shown = () => (onlyAi ? p.faces.filter((f) => f.source === "auto") : p.faces);
  const draw = () => { put(gridBox, faceGrid(shown(), selected, onSelection)); onSelection(); };

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
      h("img", { src: crop(s.face), alt: "", title: "Clique para ver a foto", onclick: () => openViewer([{ file_id: s.file }], 0) }),
      h("div", { class: "question" }, "É ", h("strong", {}, s.name), "?",
        h("div", { class: "muted" }, `semelhança ${(s.score * 100).toFixed(0)}%`)),
      h("img", { src: crop(s.cover), alt: "" }),
      h("button", { class: "ok", onclick: async () => { await api(`/api/faces/${s.face}/assign`, { person_id: s.person }); el.remove(); refreshStatus(); } }, "Sim"),
      h("button", { class: "danger", onclick: async () => { await api(`/api/faces/${s.face}/remove`, {}); el.remove(); refreshStatus(); } }, "Não"),
      h("button", { onclick: () => { other.hidden = !other.hidden; if (!other.hidden) otherInput.focus(); } }, "Outra pessoa…"));
    const otherInput = h("input", { type: "text", placeholder: "Quem é? Comece a digitar o nome", "data-people": "1" });
    const assignOther = async () => {
      if (!otherInput.value.trim()) return toast("Digite quem é");
      await api(`/api/faces/${s.face}/assign`, resolvePerson(otherInput.value));
      el.remove(); other.remove(); refreshPeople(); refreshStatus();
      toast("Rosto atribuído");
    };
    otherInput.addEventListener("keydown", (e) => { if (e.key === "Enter") assignOther(); });
    const other = h("div", { class: "toolbar suggestion-other", hidden: true },
      otherInput, h("button", { class: "primary", onclick: assignOther }, "Salvar"));
    return [el, other];
  };
  list.append(...items.flatMap(row));
  put(view, h("h1", {}, "É esta pessoa?"),
    items.length ? list : h("div", { class: "empty" }, "Nenhuma sugestão pendente. Elas aparecem depois de você dar nome às pessoas e indexar fotos novas."));
}

async function screenUnassigned() {
  await refreshPeople();
  const selected = new Set();
  let faces = [];
  const target = h("input", { type: "text", placeholder: "Nome ou #id", "data-people": "1" });
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

function photoCard(x, open) {
  return h("div", { class: "card photo", title: x.rel_path, onclick: open },
    h("img", { src: `/api/files/${x.file_id}/thumb`, loading: "lazy", alt: "" }),
    h("div", { class: "muted small" }, fmtDate(x.taken_at)),
    x.place ? h("div", { class: "muted small place", title: x.place }, `📍 ${x.place.split(", ")[0]}`) : null,
    x.snippet && !(x.place && x.snippet.includes(x.place.split(", ")[0])) ? snippet(x.snippet) : null);
}

function docRow(x, open) {
  return h("div", { class: "doc", title: x.rel_path, onclick: open },
    h("div", {}, h("strong", {}, x.rel_path.split("/").pop()),
      h("span", { class: "muted small" }, `  ${x.kind.toUpperCase()}${x.page ? " · p. " + x.page : ""} · ${fmtDate(x.taken_at)}`)),
    x.snippet ? snippet(x.snippet) : null);
}

// Infinite scroll over /api/search: loads PAGE results at a time as the end comes into view.
// onFirst(response, ms) runs once with the first page (for totals and headers).
function pagedResults(params, onFirst) {
  const photos = h("div", { class: "grid photos" });
  const photoItems = [];
  const docItems = [];
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
      // Viewer navigation runs over everything loaded so far, photos and documents apart.
      for (const x of r.hits) {
        const list = x.kind === "image" ? photoItems : docItems;
        list.push(x);
        const at = list.length - 1;
        (x.kind === "image" ? photos : docs).append(
          x.kind === "image" ? photoCard(x, () => openViewer(photoItems, at)) : docRow(x, () => openViewer(docItems, at)));
      }
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
            h("div", { class: "docs" }, r.mentions.map((x, at) => docRow(x, () => openViewer(r.mentions, at))))));
        }
      }
      put(header, ...parts);
    });
    put(out, header, list);
  };
  const drawChips = () => put(chips, ...st.people.map((p) =>
    h("span", { class: "chip" }, p.name, h("button", { title: "Remover", onclick: () => { st.people = st.people.filter((q) => q.id !== p.id); drawChips(); run(); } }, "×"))));
  const personInput = h("input", { type: "text", placeholder: "+ pessoa", "data-people": "1", class: "small-input" });
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

async function screenConfig() {
  const cfg = await api("/api/settings");
  const result = h("div");
  const field = (key, label, help) => {
    const [min, max] = cfg.ranges[key];
    const input = h("input", { type: "number", step: "0.01", min, max, value: cfg[key], class: "num-input" });
    return { key, input, el: h("div", { class: "setting" },
      h("label", {}, h("strong", {}, label), " ", input,
        h("span", { class: "muted small" }, ` padrão ${cfg.defaults[key]} · entre ${min} e ${max}`)),
      h("p", { class: "muted" }, help)) };
  };
  const fields = [
    field("t_auto", "Atribuir automaticamente a partir de",
      "Semelhança mínima para a IA colocar um rosto novo numa pessoa sem perguntar (selo IA). Mais alto = menos erros, mais perguntas. Com adultos, 0,60–0,70 costuma acertar; crianças que cresceram e recém-nascidos erram mais."),
    field("t_suggest", "Perguntar \"É esta pessoa?\" a partir de",
      "Abaixo do limiar automático e acima deste, o rosto vai para a fila de perguntas. Mais baixo = mais perguntas."),
    field("cluster_eps", "Distância para agrupar rostos sem nome (eps)",
      "Controla como rostos ainda sem pessoa são juntados em grupos novos (1 − semelhança). Mais alto = grupos maiores, com risco de misturar pessoas diferentes; na sua pasta, 0,35 já misturava."),
  ];
  const values = () => Object.fromEntries(fields.map((f) => [f.key, Number(f.input.value)]));
  const send = async (extra) => {
    const r = await api("/api/settings", { ...values(), ...extra });
    fields.forEach((f) => { f.input.value = r[f.key]; });
    if (r.grouping) {
      const g = r.grouping;
      put(result, h("div", { class: "panel" }, `Reagrupado: ${g.auto} atribuídos automaticamente, ${g.suggested} sugeridos, ${g.clustered} rostos em ${g.new_people} grupos novos, ${g.unassigned} sem grupo.`));
      refreshStatus();
    }
    toast("Configurações salvas");
  };
  put(view,
    h("h1", {}, "Configurações"),
    h("p", { class: "muted" }, "Valem para a pasta atual. Nomes, confirmações e correções feitas por você nunca são alterados."),
    h("h2", {}, "Reconhecimento de rostos"),
    fields.map((f) => f.el),
    h("div", { class: "toolbar" },
      h("button", { onclick: () => send({}) }, "Salvar (vale para as próximas indexações)"),
      h("button", { class: "primary", onclick: () => confirm("Refazer os grupos sem nome e as atribuições automáticas com estes valores?") && send({ apply: true }) }, "Salvar e reagrupar agora"),
      h("button", { onclick: () => send({ reset: true }) }, "Restaurar padrões")),
    result);
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
  if (page === "config") return screenConfig();
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
