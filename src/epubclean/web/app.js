const HINTS = {
  introduction: "Left out of narration. Turn it on if this one is part of the book.",
  afterword: "Left out of narration. Turn it on if you want it read.",
  preface: "Left out of narration.",
  foreword: "Left out of narration.",
  dedication: "Left out of narration.",
  acknowledgments: "Left out of narration.",
};

let model = null;
let preset = "narration";
let overrides = {};
let selectedId = null;
let spokenTimer = 0;

// The server's per-start secret, sent with every request so other pages can't drive it.
const TOKEN = document.querySelector('meta[name="epub-2-eleven-token"]').content;

const titleEl = document.querySelector("#book-title");
const metaEl = document.querySelector("#book-meta");
const errorEl = document.querySelector("#error");
const emptyEl = document.querySelector("#empty");
const chaptersEl = document.querySelector("#chapters");
const stageEl = document.querySelector("#stage");
const previewEl = document.querySelector("#preview");
const voiceEl = document.querySelector("#voice");
const bannerEl = document.querySelector("#chapter-banner");
const countsEl = document.querySelector("#counts");
const exportBtn = document.querySelector("#export");
const exportPath = document.querySelector("#export-path");

function included(entry) {
  if (Object.prototype.hasOwnProperty.call(overrides, entry.id)) return overrides[entry.id];
  return !entry.drop[preset];
}

function held(entry) {
  return Object.prototype.hasOwnProperty.call(overrides, entry.id);
}

function selectedChapter() {
  return model && model.chapters.find((chapter) => chapter.id === selectedId);
}

function showError(message) {
  errorEl.hidden = !message;
  errorEl.textContent = message || "";
}

async function post(url, body, headers) {
  const response = await fetch(url, {
    method: "POST",
    headers: { ...(headers || { "Content-Type": "application/json" }), "X-Epub-2-Eleven-Token": TOKEN },
    body: body instanceof ArrayBuffer ? body : JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed.");
  return payload;
}

function acceptBook(payload) {
  model = payload;
  preset = "narration";
  overrides = {};
  selectedId = (payload.chapters.find((chapter) => included(chapter)) || payload.chapters[0]).id;
  exportPath.textContent = "";
  showError("");
  document.querySelectorAll(".preset").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.preset === preset));
  });
  titleEl.textContent = payload.title || "Untitled";
  const who = [payload.author, payload.language].filter(Boolean).join(" · ");
  metaEl.textContent = who ? `${who} · ${payload.source_name}` : payload.source_name;
  emptyEl.hidden = true;
  chaptersEl.hidden = false;
  stageEl.hidden = false;
  voiceEl.textContent = payload.spoken.text || "Nothing in this selection would be read.";
  paint();
  applyCounts(payload.original_chars, payload.spoken);
}

function paint() {
  if (!model) return;
  chaptersEl.replaceChildren();
  for (const chapter of model.chapters) {
    const on = included(chapter);
    const row = document.createElement("div");
    row.className = `row${chapter.id === selectedId ? " selected" : ""}${on ? "" : " is-out"}`;
    row.tabIndex = 0;
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = on;
    box.setAttribute("aria-label", `Include ${chapter.heading}`);
    box.addEventListener("click", (event) => {
      event.stopPropagation();
      overrides[chapter.id] = box.checked;
      paint();
      scheduleSpoken();
    });
    const copy = document.createElement("div");
    const heading = document.createElement("h2");
    heading.textContent = chapter.heading || "Untitled";
    const detail = document.createElement("p");
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = chapter.role;
    detail.append(badge, document.createTextNode(chapter.reason || ""));
    if (held(chapter)) {
      const yours = document.createElement("span");
      yours.className = "badge";
      yours.textContent = "your choice";
      detail.append(document.createTextNode(" "), yours);
    }
    const hint = HINTS[chapter.role];
    if (hint && preset === "narration" && !on) {
      const hintLine = document.createElement("p");
      hintLine.textContent = hint;
      copy.append(heading, detail, hintLine);
    } else {
      copy.append(heading, detail);
    }
    const count = document.createElement("p");
    count.textContent = `${chapter.original_chars.toLocaleString()} characters in the file`;
    copy.append(count);
    row.append(box, copy);
    row.addEventListener("click", () => {
      selectedId = chapter.id;
      paint();
    });
    chaptersEl.append(row);
  }

  const chapter = selectedChapter();
  if (!chapter) return;
  previewEl.innerHTML = chapter.html;
  const on = included(chapter);
  bannerEl.hidden = on;
  bannerEl.textContent = on
    ? ""
    : "This chapter is left out. Check it in the list to put it back.";
  for (const el of previewEl.querySelectorAll("[data-epubclean-id]")) {
    const block = chapter.blocks.find((item) => item.id === el.getAttribute("data-epubclean-id"));
    if (!block) continue;
    el.classList.toggle("is-out", !on || !included(block));
  }
  exportBtn.disabled = !model.spoken || model.spoken.chars === 0;
}

function applyCounts(original, spoken) {
  model.spoken = spoken;
  const read = spoken.chars.toLocaleString();
  const source = original.toLocaleString();
  countsEl.textContent = `${read} characters will be read · ${source} in the file. ElevenLabs bills by character.`;
  exportBtn.disabled = spoken.chars === 0;
}

function scheduleSpoken() {
  window.clearTimeout(spokenTimer);
  spokenTimer = window.setTimeout(refreshSpoken, 150);
}

async function refreshSpoken() {
  if (!model) return;
  try {
    const spoken = await post("/api/spoken", {
      book_id: model.book_id,
      preset,
      overrides,
    });
    voiceEl.textContent = spoken.text || "Nothing in this selection would be read.";
    applyCounts(model.original_chars, spoken);
  } catch (error) {
    showError(error.message);
  }
}

previewEl.addEventListener("click", (event) => {
  const el = event.target.closest("[data-epubclean-id]");
  if (!el || !previewEl.contains(el)) return;
  event.preventDefault();
  const chapter = selectedChapter();
  if (!chapter) return;
  const block = chapter.blocks.find((item) => item.id === el.getAttribute("data-epubclean-id"));
  if (!block) return;
  overrides[block.id] = !included(block);
  paint();
  scheduleSpoken();
});

document.querySelectorAll(".preset").forEach((button) => {
  button.addEventListener("click", () => {
    preset = button.dataset.preset;
    document.querySelectorAll(".preset").forEach((item) => {
      item.setAttribute("aria-pressed", String(item === button));
    });
    paint();
    scheduleSpoken();
  });
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    const voice = button.dataset.tab === "voice";
    document.querySelectorAll(".tab").forEach((item) => {
      item.setAttribute("aria-selected", String(item === button));
    });
    document.querySelector("#chapter-pane").hidden = voice;
    document.querySelector("#voice-pane").hidden = !voice;
  });
});

document.querySelector("#open").addEventListener("click", async () => {
  showError("");
  try {
    const payload = await post("/api/pick", {});
    if (!payload.cancelled) acceptBook(payload);
  } catch (error) {
    showError(error.message);
  }
});

async function upload(file) {
  showError("");
  try {
    const payload = await post("/api/upload", await file.arrayBuffer(), {
      "Content-Type": "application/epub+zip",
      "X-Filename": encodeURIComponent(file.name),
    });
    acceptBook(payload);
  } catch (error) {
    showError(error.message);
  }
}

document.querySelector("#file").addEventListener("change", (event) => {
  const file = event.target.files && event.target.files[0];
  if (file) upload(file);
});

document.querySelector("#export").addEventListener("click", async () => {
  if (!model) return;
  exportBtn.disabled = true;
  try {
    const result = await post("/api/export", {
      book_id: model.book_id,
      preset,
      overrides,
    });
    exportPath.textContent = `Wrote ${result.path}`;
  } catch (error) {
    showError(error.message);
  } finally {
    exportBtn.disabled = !model.spoken || model.spoken.chars === 0;
  }
});

window.addEventListener("dragover", (event) => {
  event.preventDefault();
  emptyEl.classList.add("drag");
});

window.addEventListener("dragleave", () => emptyEl.classList.remove("drag"));

window.addEventListener("drop", (event) => {
  event.preventDefault();
  emptyEl.classList.remove("drag");
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) upload(file);
});
