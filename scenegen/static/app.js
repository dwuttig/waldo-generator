"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

const bust = () => "?t=" + Date.now();
let people = [];

// ---------------------------------------------------------------- tabs
$$("nav button").forEach(b => b.addEventListener("click", () => {
  $$("nav button").forEach(x => x.classList.toggle("active", x === b));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "scenes") renderScenePeople();
}));

// ---------------------------------------------------------------- family
async function loadPeople() {
  people = await api("/api/people");
  renderPeople();
  renderScenePeople();
}

function renderPeople() {
  const box = $("#people");
  box.innerHTML = "";
  for (const p of people) {
    const el = $("#person-tpl").content.firstElementChild.cloneNode(true);
    el.dataset.id = p.id;
    $(".pname", el).textContent = p.name;
    const photos = $(".photos", el);
    for (const ph of p.photos) {
      const t = document.createElement("div");
      t.className = "thumb";
      t.innerHTML = `<img alt=""><div class="bar-actions"><span></span><button class="small danger">✕</button></div>`;
      $("img", t).src = `/files/people/${p.id}/${ph}`;
      $("button", t).onclick = async () => {
        await api(`/api/people/${p.id}/photos/${ph.split("/").pop()}`, { method: "DELETE" });
        loadPeople();
      };
      photos.appendChild(t);
    }
    if (!p.photos.length) photos.innerHTML = `<span class="muted">No photos yet.</span>`;
    const sheets = $(".sheets", el);
    const chosenId = (p.sheets.find(s => s.chosen) || p.sheets[0] || {}).id;
    for (const s of [...p.sheets].reverse()) {
      const t = document.createElement("div");
      t.className = "thumb" + (s.id === chosenId ? " chosen" : "");
      t.title = s.outfit || "";
      t.innerHTML = `<img alt=""><div class="bar-actions"><button class="small use">${s.id === chosenId ? "✓ In use" : "Use this"}</button><button class="small danger">✕</button></div>`;
      $("img", t).src = `/files/people/${p.id}/${s.file}`;
      $("img", t).onclick = () => window.open(`/files/people/${p.id}/${s.file}`, "_blank");
      $(".use", t).onclick = async () => { await api(`/api/people/${p.id}/sheets/${s.id}/choose`, { method: "POST" }); loadPeople(); };
      $(".danger", t).onclick = async () => { await api(`/api/people/${p.id}/sheets/${s.id}`, { method: "DELETE" }); loadPeople(); };
      sheets.appendChild(t);
    }
    $(".del-person", el).onclick = async () => {
      if (!confirm(`Remove ${p.name} and all their photos and characters?`)) return;
      await api(`/api/people/${p.id}`, { method: "DELETE" });
      loadPeople();
    };
    $(".photo-input", el).onchange = async (e) => {
      const fd = new FormData();
      for (const f of e.target.files) fd.append("files", f);
      try { await api(`/api/people/${p.id}/photos`, { method: "POST", body: fd }); }
      catch (err) { alert(err.message); }
      loadPeople();
    };
    $(".make-chars", el).onclick = async (e) => {
      const btn = e.target;
      btn.disabled = true;
      const prog = $(".progress", el);
      prog.classList.remove("hidden");
      try {
        const job = await api(`/api/people/${p.id}/characters`, { method: "POST", json: { outfit: $(".outfit", el).value } });
        await pollJob(job.id, (j) => { $(".bar", prog).style.width = (j.progress * 100) + "%"; $(".msg", prog).textContent = j.message; });
        loadPeople();
      } catch (err) {
        $(".msg", prog).textContent = "Error: " + err.message;
        btn.disabled = false;
      }
    };
    box.appendChild(el);
  }
}

async function pollJob(id, onUpdate) {
  while (true) {
    const j = await api(`/api/jobs/${id}`);
    onUpdate(j);
    if (j.status === "done") return j;
    if (j.status === "error") throw new Error(j.error);
    await new Promise(r => setTimeout(r, 1500));
  }
}

$("#add-person").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = e.target.name.value.trim();
  if (!name) return;
  await api("/api/people", { method: "POST", json: { name } });
  e.target.reset();
  loadPeople();
});

// ---------------------------------------------------------------- scene setup
const placement = {};   // person_id -> {on, x, y}
let activePerson = null;

function renderScenePeople() {
  const box = $("#scene-people");
  box.innerHTML = "";
  const ready = people.filter(p => p.sheets.length);
  if (!ready.length) box.innerHTML = `<p class="muted">Make a cartoon version of someone first.</p>`;
  for (const p of ready) {
    placement[p.id] ??= { on: true, x: null, y: null };
    const s = p.sheets.find(x => x.chosen) || p.sheets[0];
    const row = document.createElement("div");
    row.className = "scene-person" + (activePerson === p.id ? " active" : "");
    row.innerHTML = `<input type="checkbox"><img alt=""><span></span><span class="where"></span>`;
    const cb = $("input", row);
    cb.checked = placement[p.id].on;
    cb.onclick = (e) => { e.stopPropagation(); placement[p.id].on = cb.checked; updatePad(); updateEstimate(); };
    $("img", row).src = `/files/people/${p.id}/${s.file}`;
    $("span", row).textContent = p.name;
    $(".where", row).textContent = placement[p.id].x == null ? "random" : "placed";
    row.onclick = () => { activePerson = activePerson === p.id ? null : p.id; renderScenePeople(); };
    box.appendChild(row);
  }
  updatePad();
  updateEstimate();
}

function updatePad() {
  const pad = $("#pad");
  const [a, b] = $("#scene-aspect").value.split(":");
  pad.style.aspectRatio = `${a} / ${b}`;
  $$(".dot", pad).forEach(d => d.remove());
  for (const p of people) {
    const pl = placement[p.id];
    if (!pl || !pl.on || pl.x == null) continue;
    const d = document.createElement("span");
    d.className = "dot";
    d.textContent = p.name;
    d.style.left = (pl.x * 100) + "%";
    d.style.top = (pl.y * 100) + "%";
    pad.appendChild(d);
  }
}

$("#pad").addEventListener("click", (e) => {
  if (!activePerson) { alert("Click a person in the list first, then click here."); return; }
  const r = e.currentTarget.getBoundingClientRect();
  placement[activePerson] = { on: true, x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
  renderScenePeople();
});
$("#pad").addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (activePerson) { placement[activePerson].x = placement[activePerson].y = null; renderScenePeople(); }
});
$("#scene-aspect").addEventListener("change", () => { updatePad(); updateEstimate(); });

function selectedPlacements() {
  return people.filter(p => p.sheets.length && placement[p.id]?.on).map(p => {
    const s = p.sheets.find(x => x.chosen) || p.sheets[0];
    return { person_id: p.id, sheet_id: s.id, x: placement[p.id].x, y: placement[p.id].y };
  });
}

async function updateEstimate() {
  const n = selectedPlacements().length;
  try {
    const e = await api(`/api/estimate?n=${n}&aspect_ratio=${encodeURIComponent($("#scene-aspect").value)}`);
    $("#estimate").textContent = `Estimated cost: about $${e.total.toFixed(2)} (${n} people, ${e.tiles} detail tiles)`;
  } catch { $("#estimate").textContent = ""; }
}

$("#make-scene").addEventListener("click", async () => {
  const description = $("#scene-desc").value.trim();
  const placements = selectedPlacements();
  if (!description) return alert("Describe the scene first.");
  if (!placements.length) return alert("Tick at least one person to hide.");
  try {
    await api("/api/scenes", { method: "POST", json: { description, placements, aspect_ratio: $("#scene-aspect").value } });
    loadScenes();
  } catch (err) { alert(err.message); }
});

// ---------------------------------------------------------------- scenes list
let scenePoll = null;

async function loadScenes() {
  const scenes = await api("/api/scenes");
  const box = $("#scenes");
  box.innerHTML = "";
  for (const s of scenes) {
    const el = document.createElement("div");
    el.className = "card scene-card";
    const when = new Date(s.created * 1000).toLocaleString();
    el.innerHTML = `<div class="row between"><strong></strong><span class="muted">${when}</span></div>`;
    $("strong", el).textContent = s.description;
    if (s.status === "done") {
      const base = `/files/scenes/${s.id}/`;
      el.insertAdjacentHTML("beforeend", `
        <img class="preview" alt="Scene preview">
        <div class="row">
          <span class="muted">${s.size.join(" × ")} px · hidden: ${s.found.map(f => f.name).join(", ")}</span>
        </div>
        <div class="row">
          <button class="toggle">Show solution</button>
          <a href="${base}${s.files.final_jpg}" target="_blank"><button>Open full size</button></a>
          <a href="${base}${s.files.final}?download=1"><button>Download PNG</button></a>
          <a href="${base}${s.files.answer_key}?download=1"><button>Download solution</button></a>
          <button class="danger del">Delete</button>
        </div>`);
      const img = $("img.preview", el);
      img.src = base + s.files.preview;
      let showing = false;
      $(".toggle", el).onclick = (e) => {
        showing = !showing;
        img.src = base + (showing ? s.files.answer_key : s.files.preview);
        e.target.textContent = showing ? "Hide solution" : "Show solution";
      };
    } else if (s.status === "error") {
      el.insertAdjacentHTML("beforeend", `<p class="error"></p><button class="danger del">Delete</button>`);
      $(".error", el).textContent = s.error;
    } else {
      el.insertAdjacentHTML("beforeend", `<div class="progress"><div class="bar"></div><span class="msg"></span></div>
        <p class="muted">Estimated cost: about $${s.estimated_cost.toFixed(2)}. This can take several minutes.</p>`);
      $(".bar", el).style.width = (s.progress * 100) + "%";
      $(".msg", el).textContent = s.message || s.status;
    }
    const del = $(".del", el);
    if (del) del.onclick = async () => { if (confirm("Delete this scene?")) { await api(`/api/scenes/${s.id}`, { method: "DELETE" }); loadScenes(); } };
    box.appendChild(el);
  }
  clearTimeout(scenePoll);
  if (scenes.some(s => s.status === "queued" || s.status === "running")) scenePoll = setTimeout(loadScenes, 2000);
}

// ---------------------------------------------------------------- settings
const STEP_LABELS = {
  character: "Cartoon characters", scene: "Base scene", place: "Hide people",
  detail: "Detail pass", upscale: "Upscale",
};
const STEP_KIND = { character: "edit", scene: "generate", place: "edit", detail: "edit", upscale: "upscale" };
let presets = {};

async function loadSettings() {
  presets = await api("/api/presets");
  const s = await api("/api/settings");
  const f = $("#settings-form");
  f.fal_api_key.value = ""; f.google_api_key.value = "";
  f.fal_api_key.placeholder = s.fal_api_key || (s.fal_api_key_from_env ? "set via FAL_KEY" : "not set");
  f.google_api_key.placeholder = s.google_api_key || (s.google_api_key_from_env ? "set via GEMINI_API_KEY" : "not set");
  f.comfyui_url.value = s.comfyui_url;
  f.wf_generate.value = s.comfyui_workflows.generate || "";
  f.wf_edit.value = s.comfyui_workflows.edit || "";
  f.wf_upscale.value = s.comfyui_workflows.upscale || "";
  f.style_prompt.value = s.style_prompt;
  for (const k of ["aspect_ratio", "place_patch", "character_variations", "detail_grid", "detail_scale", "upscale_factor"]) f[k].value = s.scene[k];
  f.detail_enabled.checked = s.scene.detail_enabled;
  $("#scene-aspect").value = s.scene.aspect_ratio;

  const tbody = $("#steps tbody");
  tbody.innerHTML = "";
  for (const [step, label] of Object.entries(STEP_LABELS)) {
    const c = s.steps[step];
    const tr = document.createElement("tr");
    tr.dataset.step = step;
    tr.innerHTML = `<td>${label}</td>
      <td><select class="provider">${["fal", "google", "comfyui", "mock"].map(p => `<option>${p}</option>`).join("")}</select></td>
      <td><input class="model" list="dl-${step}"><datalist id="dl-${step}"></datalist></td>
      <td><select class="res"><option>1K</option><option>2K</option><option>4K</option></select></td>
      <td><input class="price" type="number" step="0.01" min="0" style="width:90px"></td>`;
    $(".provider", tr).value = c.provider;
    $(".model", tr).value = c.model;
    $(".res", tr).value = c.resolution;
    $(".price", tr).value = c.price;
    const fill = () => {
      const opts = (presets[$(".provider", tr).value] || {})[STEP_KIND[step]] || [];
      $("datalist", tr).innerHTML = opts.map(o => `<option value="${o}">`).join("");
    };
    $(".provider", tr).onchange = () => {
      fill();
      const opts = (presets[$(".provider", tr).value] || {})[STEP_KIND[step]] || [];
      $(".model", tr).value = opts[0] || "";
    };
    fill();
    tbody.appendChild(tr);
  }
}

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const steps = {};
  for (const tr of $$("#steps tbody tr")) {
    steps[tr.dataset.step] = {
      provider: $(".provider", tr).value, model: $(".model", tr).value.trim(),
      resolution: $(".res", tr).value, price: parseFloat($(".price", tr).value) || 0,
    };
  }
  const body = {
    comfyui_url: f.comfyui_url.value.trim(),
    comfyui_workflows: { generate: f.wf_generate.value.trim(), edit: f.wf_edit.value.trim(), upscale: f.wf_upscale.value.trim() },
    style_prompt: f.style_prompt.value,
    steps,
    scene: {
      aspect_ratio: f.aspect_ratio.value, place_patch: parseFloat(f.place_patch.value),
      character_variations: parseInt(f.character_variations.value), detail_enabled: f.detail_enabled.checked,
      detail_grid: parseInt(f.detail_grid.value), detail_scale: parseInt(f.detail_scale.value),
      upscale_factor: parseInt(f.upscale_factor.value),
    },
  };
  // Only send keys the user actually typed, so stored keys are never overwritten by accident.
  if (f.fal_api_key.value) body.fal_api_key = f.fal_api_key.value.trim();
  if (f.google_api_key.value) body.google_api_key = f.google_api_key.value.trim();
  try {
    await api("/api/settings", { method: "PUT", json: body });
    $("#settings-status").textContent = "Saved.";
    await loadSettings();
    updateEstimate();
  } catch (err) { $("#settings-status").textContent = "Error: " + err.message; }
  setTimeout(() => $("#settings-status").textContent = "", 4000);
});

loadSettings().then(loadPeople);
loadScenes();
