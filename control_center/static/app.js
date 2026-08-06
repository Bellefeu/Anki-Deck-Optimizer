"use strict";

const TOKEN = document.querySelector('meta[name="control-token"]').content;
const state = {
  status: null,
  decks: [],
  filter: "all",
  selectedDeck: null,
  update: null,
  activeJob: null,
  resetStep: 1,
};

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const pathMeasure = document.createElement("canvas").getContext("2d");

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Control-Token", TOKEN);
  if (options.body && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, {...options, headers});
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    payload = {error: `The local helper returned HTTP ${response.status}.`};
  }
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`.trim();
  node.textContent = message;
  $("#toast-stack").append(node);
  window.setTimeout(() => node.remove(), 5200);
}

function setView(name) {
  $$(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  if (name === "decks") loadDecks();
  if (name === "preferences") loadPreferences();
}

function prettyStatus(value) {
  return String(value || "unknown").replaceAll("-", " ");
}

function syncProjectPathWidth() {
  const input = $("#project-path");
  const row = input.parentElement;
  const check = $("#apply-project");
  if (!pathMeasure || !row.clientWidth) return;
  pathMeasure.font = getComputedStyle(input).font;
  const measured = pathMeasure.measureText(input.value || input.placeholder || "Project folder").width + 5;
  const available = Math.max(150, row.clientWidth - check.offsetWidth - 8);
  input.style.width = `${Math.max(150, Math.min(measured, available))}px`;
}

async function loadStatus() {
  try {
    state.status = await api("/api/status");
    const data = state.status;
    $("#project-path").value = data.project;
    syncProjectPathWidth();
    $("#rail-version").textContent = data.version;
    const pipeline = data.pipeline;
    $("#metric-total").textContent = pipeline.modules;
    $("#metric-review").textContent = pipeline.built_unverified + pipeline.in_progress;
    $("#metric-verified").textContent = pipeline.verified;
    $("#metric-staged").textContent = data.staged.decks;
    renderHealth(data.health);
    await loadDecks(false);
    renderNextReview();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderHealth(health) {
  const items = [
    ["Python", Boolean(health.python), health.python],
    ["PDF tools", health.poppler, ""],
    ["Image text", health.tesseract, ""],
    ["Node", health.node, ""],
  ];
  const healthy = items.every((item) => item[1]);
  $("#health-title").textContent = healthy ? "Ready for pipeline work" : "A few setup items are missing";
  const pills = $("#health-pills");
  pills.replaceChildren();
  for (const [label, ok, detail] of items) {
    const pill = document.createElement("span");
    pill.className = `health-pill ${ok ? "ok" : "missing"}`;
    pill.textContent = `${ok ? "✓" : "○"} ${label}${detail ? ` ${detail}` : ""}`;
    pills.append(pill);
  }
}

async function loadDecks(render = true) {
  try {
    const response = await api("/api/decks");
    state.decks = response.decks;
    if (state.selectedDeck) {
      state.selectedDeck = state.decks.find((deck) => deck.name === state.selectedDeck.name) || null;
    }
    if (render) renderDecks();
  } catch (error) {
    toast(error.message, "error");
  }
}

function filteredDecks() {
  if (state.filter === "verified") return state.decks.filter((deck) => deck.verified);
  if (state.filter === "review") return state.decks.filter((deck) => !deck.verified);
  return state.decks;
}

function renderDecks() {
  const list = $("#deck-list");
  list.replaceChildren();
  const decks = filteredDecks();
  if (!decks.length) {
    const empty = document.createElement("div");
    empty.className = "empty-list";
    empty.textContent = state.filter === "all" ? "No completed decks yet." : "No decks match this view.";
    list.append(empty);
  }
  for (const deck of decks) {
    const button = document.createElement("button");
    button.className = `deck-row${state.selectedDeck?.name === deck.name ? " active" : ""}`;
    const dot = document.createElement("span");
    dot.className = `status-dot ${deck.status}`;
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = deck.name;
    const sub = document.createElement("small");
    sub.textContent = prettyStatus(deck.status);
    copy.append(title, sub);
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = deck.judgement_count ? `${deck.judgement_count} calls` : "";
    button.append(dot, copy, count);
    button.addEventListener("click", () => {
      state.selectedDeck = deck;
      renderDecks();
      renderDeckDetail(deck);
    });
    list.append(button);
  }
  if (state.selectedDeck) renderDeckDetail(state.selectedDeck);
}

function actionButton(label, path, primary = false) {
  const button = document.createElement("button");
  button.className = primary ? "primary-button" : "quiet-button";
  button.textContent = label;
  button.disabled = !path;
  if (path) {
    button.addEventListener("click", async () => {
      try {
        await api("/api/open", {method: "POST", body: {path}});
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }
  return button;
}

function renderDeckDetail(deck) {
  const detail = $("#deck-detail");
  detail.replaceChildren();
  const status = document.createElement("span");
  status.className = "detail-status";
  status.textContent = `${deck.verified ? "✓" : "○"} ${prettyStatus(deck.status)}`;
  const heading = document.createElement("h2");
  heading.textContent = deck.name;
  const summary = document.createElement("p");
  if (deck.cards_before != null || deck.cards_after != null) {
    summary.textContent = `Cards: ${deck.cards_before ?? "?"} before · ${deck.cards_after ?? "?"} after`;
  } else {
    summary.textContent = "Open the audit notes and judgement calls before approving this deck.";
  }
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  actions.append(
    actionButton("Open notes", deck.notes_path, true),
    actionButton("Open verification report", deck.report_path),
    actionButton("Open deck folder", deck.folder_path),
  );
  const callHeading = document.createElement("h3");
  callHeading.textContent = `Judgement calls (${deck.judgement_count})`;
  const calls = document.createElement("ul");
  calls.className = "judgement-list";
  if (deck.judgements.length) {
    for (const call of deck.judgements) {
      const item = document.createElement("li");
      item.textContent = call;
      calls.append(item);
    }
  } else {
    const item = document.createElement("li");
    item.textContent = deck.verified
      ? "No outstanding judgement calls were found."
      : "No judgement-call section was found yet. Open the report or audit folder to review the work.";
    calls.append(item);
  }
  detail.append(status, heading, summary, actions, callHeading, calls);
}

function renderNextReview() {
  const target = $("#next-review-content");
  target.replaceChildren();
  const next = state.decks.find((deck) => !deck.verified);
  const heading = document.createElement("h2");
  const copy = document.createElement("p");
  if (next) {
    heading.textContent = next.name;
    copy.textContent = next.judgement_count
      ? `${next.judgement_count} judgement call${next.judgement_count === 1 ? "" : "s"} ready for you.`
      : `${prettyStatus(next.status)} · open its notes and audit files.`;
  } else {
    heading.textContent = state.decks.length ? "All tracked decks are verified" : "No completed deck loaded yet";
    copy.textContent = state.decks.length
      ? "There are no open human checkpoints."
      : "Your next judgement review will appear here.";
  }
  target.append(heading, copy);
}

async function loadPreferences() {
  try {
    const data = await api("/api/preferences");
    $("#profile-editor").value = data.profile;
    $("#prompts-editor").value = data.prompts;
  } catch (error) {
    toast(error.message, "error");
  }
}

async function savePreferences() {
  const button = $("#save-preferences");
  button.disabled = true;
  try {
    const result = await api("/api/preferences", {
      method: "POST",
      body: {profile: $("#profile-editor").value, prompts: $("#prompts-editor").value},
    });
    toast(result.message, "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function renderResetStep(step) {
  state.resetStep = step;
  const final = step === 2;
  $("#reset-title").textContent = final ? "Final check" : "Reset to default preferences?";
  $("#reset-copy").textContent = final
    ? "This will replace both preference editors with the original defaults. Reset them now?"
    : "Are you sure you want to reset to default preferences?";
  $("#reset-note").textContent = final
    ? "Your currently saved PROFILE.md and USER_PROMPTS.md will be kept as .bak recovery copies."
    : "Unsaved editor changes will be discarded. Nothing changes until the second check.";
  $("#reset-step-two").classList.toggle("active", final);
  $("#reset-cancel").textContent = final ? "Go back" : "Keep my preferences";
  $("#reset-continue").textContent = final ? "Reset to defaults" : "Continue";
  $("#reset-continue").classList.toggle("danger-button", final);
}

function openResetModal() {
  renderResetStep(1);
  $("#reset-modal").hidden = false;
  document.body.classList.add("modal-open");
  $("#reset-cancel").focus();
}

function closeResetModal() {
  $("#reset-modal").hidden = true;
  document.body.classList.remove("modal-open");
  $("#reset-preferences").focus();
}

async function continuePreferenceReset() {
  if (state.resetStep === 1) {
    renderResetStep(2);
    $("#reset-cancel").focus();
    return;
  }
  const button = $("#reset-continue");
  button.disabled = true;
  try {
    const result = await api("/api/preferences/reset", {
      method: "POST", body: {confirmation: "RESET TO DEFAULTS"},
    });
    $("#profile-editor").value = result.profile;
    $("#prompts-editor").value = result.prompts;
    closeResetModal();
    toast(result.message, "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function cancelPreferenceReset() {
  if (state.resetStep === 2) {
    renderResetStep(1);
    $("#reset-cancel").focus();
  } else {
    closeResetModal();
  }
}

function inferModule(file) {
  const current = $("#module-name").value.trim();
  if (current) return current;
  if (file.name.toLowerCase().endsWith(".apkg")) {
    const inferred = file.name.slice(0, -5).replace(/\s*\((?:final|notes)\)\s*$/i, "");
    $("#module-name").value = inferred;
    return inferred;
  }
  return "";
}

function uploadFile(file, kind, overwrite = false) {
  const module = inferModule(file);
  if (!module) {
    toast("Enter the module name before staging files.", "error");
    return Promise.reject(new Error("Module name is required."));
  }
  const row = document.createElement("div");
  row.className = "upload-item";
  const name = document.createElement("span");
  name.textContent = file.name;
  const bar = document.createElement("span");
  bar.className = "bar";
  const fill = document.createElement("span");
  fill.style.width = "0%";
  bar.append(fill);
  const label = document.createElement("small");
  label.textContent = "waiting";
  row.append(name, bar, label);
  $("#upload-list").prepend(row);

  return new Promise((resolve, reject) => {
    const query = new URLSearchParams({kind, module, name: file.name, overwrite: overwrite ? "1" : "0"});
    const request = new XMLHttpRequest();
    request.open("POST", `/api/stage?${query}`);
    request.setRequestHeader("X-Control-Token", TOKEN);
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        fill.style.width = `${percent}%`;
        label.textContent = `${percent}%`;
      }
    });
    request.addEventListener("load", async () => {
      let payload = {};
      try { payload = JSON.parse(request.responseText); } catch (_) { /* handled below */ }
      if (request.status === 409 && !overwrite) {
        row.remove();
        if (window.confirm(`${payload.error || file.name + " already exists."}\n\nReplace that staged copy?`)) {
          try { resolve(await uploadFile(file, kind, true)); } catch (error) { reject(error); }
        } else {
          reject(new Error("Replacement cancelled."));
        }
        return;
      }
      if (request.status < 200 || request.status >= 300 || payload.ok === false) {
        row.classList.add("failed");
        label.textContent = "stopped";
        const error = new Error(payload.error || `Upload failed (${request.status}).`);
        toast(error.message, "error");
        reject(error);
        return;
      }
      fill.style.width = "100%";
      label.textContent = "staged";
      row.classList.add("done");
      toast(`${payload.name} is ready.`, "success");
      await loadStatus();
      resolve(payload);
    });
    request.addEventListener("error", () => {
      row.classList.add("failed");
      label.textContent = "stopped";
      reject(new Error("The local upload connection stopped."));
    });
    request.send(file);
  });
}

async function stageFiles(files, kind) {
  const accepted = [...files];
  if (kind === "deck" && accepted.length > 1) {
    toast("Drop one .apkg deck at a time so it pairs with the right module.", "error");
    return;
  }
  for (const file of accepted) {
    try { await uploadFile(file, kind); } catch (_) { /* each row already explains the result */ }
  }
}

function bindDropZone(zone) {
  const kind = zone.dataset.kind;
  const picker = $(`#${kind === "deck" ? "deck" : "source"}-picker`);
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("dragging"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragging");
    stageFiles(event.dataTransfer.files, kind);
  });
  zone.addEventListener("click", (event) => {
    if (!event.target.closest("button")) picker.click();
  });
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); picker.click(); }
  });
  picker.addEventListener("change", () => {
    stageFiles(picker.files, kind);
    picker.value = "";
  });
}

async function setProject(path) {
  try {
    const result = await api("/api/project", {method: "POST", body: {path}});
    toast("Project folder selected.", "success");
    state.selectedDeck = null;
    state.update = null;
    await loadStatus();
    return result;
  } catch (error) {
    toast(error.message, "error");
  }
}

async function chooseProject() {
  try {
    const result = await api("/api/project/select", {method: "POST", body: {}});
    $("#project-path").value = result.project;
    syncProjectPathWidth();
    if (result.changed) {
      toast("Project folder selected.", "success");
      state.selectedDeck = null;
      await loadStatus();
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

async function openStagingDestination(kind) {
  try {
    const result = await api("/api/staging/open", {
      method: "POST", body: {kind, module: $("#module-name").value.trim()},
    });
    toast(`Opened ${result.path}`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function openProject() {
  try {
    await api("/api/open", {method: "POST", body: {path: $("#project-path").value}});
  } catch (error) {
    toast(error.message, "error");
  }
}

async function runSetup() {
  try {
    const result = await api("/api/setup", {method: "POST", body: {}});
    toast(result.message, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

const progressForStep = {
  start: 8, check: 24, download: 28, verify: 42, test: 55,
  backup: 68, rollback: 72, done: 100, error: 100,
};

async function startJob(path, body, kind) {
  if (state.activeJob) {
    toast("One maintenance task is already running.", "error");
    return;
  }
  try {
    const response = await api(path, {method: "POST", body});
    state.activeJob = {id: response.job_id, kind};
    const card = $("#job-card");
    card.hidden = false;
    card.className = "job-card glass";
    $("#job-label").textContent = kind === "check" ? "Checking for updates…" : "Installing the update…";
    $("#job-log").textContent = "Starting…";
    $("#progress-fill").style.width = "8%";
    pollJob();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function pollJob() {
  const active = state.activeJob;
  if (!active) return;
  try {
    const response = await api(`/api/jobs/${active.id}`);
    const job = response.job;
    $("#job-label").textContent = job.label;
    $("#job-log").textContent = job.messages.join("\n") || "Working…";
    $("#progress-fill").style.width = `${progressForStep[job.step] || (job.status === "running" ? 50 : 100)}%`;
    const card = $("#job-card");
    card.classList.toggle("done", job.status === "done");
    card.classList.toggle("error", job.status === "error");
    if (job.status === "running") {
      window.setTimeout(pollJob, 650);
      return;
    }
    const kind = active.kind;
    state.activeJob = null;
    if (job.status === "error") {
      $("#job-log").textContent += `${job.messages.length ? "\n\n" : ""}${job.error}`;
      toast(job.error, "error");
      return;
    }
    if (kind === "check") {
      state.update = job.result;
      renderUpdateResult();
    } else {
      toast(`Update ${job.result.version} installed. Your project data was preserved.`, "success");
      state.update = null;
      $("#install-update").disabled = true;
      await loadStatus();
    }
  } catch (error) {
    state.activeJob = null;
    toast(error.message, "error");
  }
}

function renderUpdateResult() {
  const result = state.update;
  if (!result) return;
  if (result.available) {
    $("#update-heading").textContent = `Version ${result.latest} is ready`;
    $("#update-copy").textContent = `Installed: ${result.installed}. The update will be tested, backed up, and checked again before it is kept.`;
    $("#install-update").disabled = false;
    toast("A stable update is available.", "success");
  } else {
    $("#update-heading").textContent = "You are up to date";
    $("#update-copy").textContent = `Version ${result.installed} is the latest stable release.`;
    $("#install-update").disabled = true;
    toast("No update is needed.", "success");
  }
}

function bindEvents() {
  $$(".nav-button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $$('[data-go]').forEach((button) => button.addEventListener("click", () => setView(button.dataset.go)));
  $$(".segmented button").forEach((button) => button.addEventListener("click", () => {
    $$(".segmented button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.filter = button.dataset.filter;
    state.selectedDeck = null;
    renderDecks();
    $("#deck-detail").replaceChildren();
    const empty = document.createElement("div");
    empty.className = "empty-detail";
    const heading = document.createElement("h2");
    heading.textContent = "Select a deck";
    empty.append(heading);
    $("#deck-detail").append(empty);
  }));
  $$(".drop-zone").forEach(bindDropZone);
  $$('[data-picker]').forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    $(`#${button.dataset.picker}-picker`).click();
  }));
  $$('[data-open-staging]').forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    openStagingDestination(button.dataset.openStaging);
  }));
  $("#apply-project").addEventListener("click", () => setProject($("#project-path").value));
  $("#project-path").addEventListener("input", syncProjectPathWidth);
  $("#project-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") setProject(event.currentTarget.value);
  });
  $("#choose-project").addEventListener("click", chooseProject);
  $("#open-project").addEventListener("click", openProject);
  $("#save-preferences").addEventListener("click", savePreferences);
  $("#reset-preferences").addEventListener("click", openResetModal);
  $("#reset-cancel").addEventListener("click", cancelPreferenceReset);
  $("#reset-continue").addEventListener("click", continuePreferenceReset);
  $("#reset-modal").addEventListener("click", (event) => {
    if (event.target === event.currentTarget && state.resetStep === 1) closeResetModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#reset-modal").hidden) closeResetModal();
  });
  window.addEventListener("resize", syncProjectPathWidth);
  $("#run-setup-home").addEventListener("click", runSetup);
  $("#run-setup").addEventListener("click", runSetup);
  $("#check-update").addEventListener("click", () => startJob("/api/update/check", {}, "check"));
  $("#install-update").addEventListener("click", () => {
    const latest = state.update?.latest || "the latest version";
    if (window.confirm(`Install ${latest} now?\n\nThe patcher will make a backup first and roll back if a check fails.`)) {
      startJob("/api/update/install", {}, "install");
    }
  });
}

bindEvents();
loadStatus();
