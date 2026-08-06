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
  guideLoaded: false,
  patchNotes: {},
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
  window.scrollTo(0, 0);
  if (name === "guide") loadGuide();
  if (name === "decks") loadDecks();
  if (name === "preferences") loadPreferences();
}

function prettyStatus(value) {
  return String(value || "unknown").replaceAll("-", " ");
}

function activeReviewDeck() {
  return state.selectedDeck || state.decks.find((deck) => !deck.verified) || state.decks[0] || null;
}

function activeModuleName() {
  return activeReviewDeck()?.name || "";
}

function activePatchNote() {
  return state.patchNotes[activeModuleName()]?.trim() || "";
}

function promptNeedsDeck(template) {
  return /<module(?: name)?>/i.test(String(template));
}

function promptNeedsPatchNote(template) {
  return String(template).includes("[Describe what should change.]");
}

function contextualizeGuidePrompt(template) {
  const module = activeModuleName() || "SELECT A REVIEW DECK";
  const feedback = activePatchNote() || "Add your change in Deck Review";
  return String(template)
    .replace(/<module(?: name)?>/gi, module)
    .replaceAll("[Describe what should change.]", feedback);
}

function refreshCopyButton(button) {
  const missingDeck = button._requiresDeck && !activeModuleName();
  const missingFeedback = button._requiresFeedback && !activePatchNote();
  button.disabled = Boolean(button._forceDisabled || missingDeck || missingFeedback);
  if (missingDeck) button.title = "Choose a review deck first.";
  else if (missingFeedback) button.title = "Describe the correction in Deck Review first.";
  else button.removeAttribute("title");
}

function updateContextualPrompts() {
  $$('[data-prompt-template]').forEach((node) => {
    node.textContent = contextualizeGuidePrompt(node.dataset.promptTemplate);
  });
  $$('[data-patch-hint]').forEach((node) => {
    node.textContent = activePatchNote()
      ? "Uses the correction you wrote in Deck Review."
      : "Write the correction in Deck Review to unlock this copy button.";
  });
  $$(".guide-copy-button").forEach(refreshCopyButton);
  renderPromptDeckSelector();
}

function guideSlug(value) {
  return String(value || "section")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "section";
}

function appendGuideInline(parent, source) {
  const pattern = /(\*\*[^*]+\*\*|`[^`\n]+`|\[[^\]]+\]\([^)]+\)|<https?:\/\/[^>]+>)/g;
  let cursor = 0;
  for (const match of String(source).matchAll(pattern)) {
    parent.append(document.createTextNode(source.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      appendGuideInline(strong, token.slice(2, -2));
      parent.append(strong);
    } else if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else {
      const markdownLink = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = markdownLink ? markdownLink[2] : token.slice(1, -1);
      const label = markdownLink ? markdownLink[1] : href;
      if (/^https?:\/\//i.test(href)) {
        const link = document.createElement("a");
        link.href = href;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = label;
        parent.append(link);
      } else {
        parent.append(document.createTextNode(label));
      }
    }
    cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(source.slice(cursor)));
}

async function copyGuideText(text, button) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const fallback = document.createElement("textarea");
      fallback.value = text;
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.append(fallback);
      fallback.select();
      document.execCommand("copy");
      fallback.remove();
    }
    const original = button.textContent;
    button.textContent = "Copied ✓";
    button.classList.add("copied");
    window.setTimeout(() => {
      button.textContent = original;
      button.classList.remove("copied");
    }, 1800);
    toast("Copied. Paste it into your LLM when you are ready.", "success");
  } catch (_) {
    toast("Copy was blocked. Select the prompt text and copy it manually.", "error");
  }
}

function guidePromptMeta(code) {
  const value = code.trim();
  const lower = value.toLowerCase();
  if (lower.includes("create a standalone scheduled task")) return ["Scheduled run", "Automate eight hours"];
  if (lower.includes("prompt_auto.md")) return ["Automatic mode", "Run the next safe phase"];
  if (lower.includes("prompt_build.md")) return ["Session A", "Build a module"];
  if (lower.includes("prompt_verify.md")) return ["Session B", "Verify in a fresh session"];
  if (lower.includes("prompt_patch.md")) return ["Correction loop", "Patch a judgement call"];
  if (lower.includes("prompt_dedupe.md")) return ["Final step", "Check across every deck"];
  if (lower.startsWith("compare completed/")) return ["Grade a rebuild", "Compare original and final"];
  if (lower.startsWith("grade completed/")) return ["Grade a new deck", "Audit creation mode"];
  if (lower.startsWith("fix the final deck")) return ["Apply the critique", "Repair the final deck"];
  if (lower.startsWith("approved. pass it")) return ["Human approval", "Pass the verified deck"];
  if (lower.startsWith("approved. cleanup")) return ["Finish the module", "Archive scratch work"];
  return null;
}

function makeCopyButton(code, label = "Copy prompt", options = {}) {
  const button = document.createElement("button");
  button.className = "guide-copy-button";
  button.type = "button";
  button.textContent = label;
  const getter = typeof code === "function" ? code : () => code;
  const template = options.template || (typeof code === "string" ? code : "");
  button._copyText = getter;
  button._requiresDeck = Boolean(options.requiresDeck ?? promptNeedsDeck(template));
  button._requiresFeedback = Boolean(options.requiresFeedback ?? promptNeedsPatchNote(template));
  button._forceDisabled = Boolean(options.disabled);
  button.addEventListener("click", () => {
    refreshCopyButton(button);
    if (button.disabled) return;
    copyGuideText(getter(), button);
  });
  refreshCopyButton(button);
  return button;
}

function renderPromptLibrary(markdown) {
  const grid = $("#prompt-card-grid");
  grid.replaceChildren();
  const seen = new Set();
  const prompts = [];
  const pattern = /```[^\n]*\n([\s\S]*?)```/g;
  for (const match of markdown.matchAll(pattern)) {
    const code = match[1].trim();
    const meta = guidePromptMeta(code);
    if (!meta || seen.has(code)) continue;
    seen.add(code);
    prompts.push({code, meta});
  }
  for (const [index, prompt] of prompts.entries()) {
    const card = document.createElement("article");
    card.className = "prompt-card";
    card.dataset.tone = String((index % 5) + 1);
    const top = document.createElement("div");
    top.className = "prompt-card-top";
    const label = document.createElement("span");
    label.textContent = prompt.meta[0];
    top.append(label, makeCopyButton(
      () => contextualizeGuidePrompt(prompt.code),
      "Copy prompt",
      {template: prompt.code},
    ));
    const heading = document.createElement("h3");
    heading.textContent = prompt.meta[1];
    const pre = document.createElement("pre");
    pre.dataset.promptTemplate = prompt.code;
    pre.textContent = contextualizeGuidePrompt(prompt.code);
    card.append(top, heading);
    if (promptNeedsPatchNote(prompt.code)) {
      const hint = document.createElement("small");
      hint.className = "prompt-requirement";
      hint.dataset.patchHint = "true";
      hint.textContent = activePatchNote()
        ? "Uses the correction you wrote in Deck Review."
        : "Write the correction in Deck Review to unlock this copy button.";
      card.append(hint);
    }
    card.append(pre);
    grid.append(card);
  }
  return prompts.length;
}

function isGuideBlockStart(lines, index) {
  const line = lines[index] || "";
  if (!line.trim()) return true;
  if (/^#{1,4}\s+/.test(line) || /^```/.test(line) || /^---+$/.test(line.trim())) return true;
  if (/^\s*(?:[-*]|\d+\.)\s+/.test(line)) return true;
  return /^\|/.test(line) && /^\|?\s*:?-+/.test(lines[index + 1] || "");
}

function appendGuideTable(container, lines, start) {
  const rows = [];
  let index = start;
  while (index < lines.length && /^\|/.test(lines[index])) {
    rows.push(lines[index].trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
    index += 1;
  }
  if (rows.length < 2) return start;
  const wrap = document.createElement("div");
  wrap.className = "guide-table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const value of rows[0]) {
    const cell = document.createElement("th");
    appendGuideInline(cell, value);
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const values of rows.slice(2)) {
    const row = document.createElement("tr");
    for (const value of values) {
      const cell = document.createElement("td");
      appendGuideInline(cell, value);
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);
  wrap.append(table);
  container.append(wrap);
  return index;
}

function renderGuide(markdown) {
  const content = $("#guide-content");
  const toc = $("#guide-toc");
  content.replaceChildren();
  toc.replaceChildren();
  const lines = markdown.replaceAll("\r\n", "\n").split("\n");
  let chapter = document.createElement("section");
  chapter.className = "guide-chapter guide-prologue";
  content.append(chapter);
  let chapterCount = 0;
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    if (/^#\s+/.test(line)) { index += 1; continue; }

    const heading = line.match(/^(#{2,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const title = heading[2].replace(/\*\*/g, "").trim();
      if (level === 2) {
        chapterCount += 1;
        chapter = document.createElement("section");
        chapter.className = "guide-chapter";
        chapter.id = `guide-${guideSlug(title)}`;
        const marker = document.createElement("span");
        marker.className = "chapter-marker";
        marker.textContent = /^PART\s+\d+/i.test(title) ? title.split("—")[0].trim() : "REFERENCE";
        const node = document.createElement("h2");
        appendGuideInline(node, title.replace(/^PART\s+\d+\s+—\s+/i, ""));
        chapter.append(marker, node);
        content.append(chapter);
        const link = document.createElement("button");
        link.type = "button";
        link.textContent = title;
        const targetChapter = chapter;
        link.addEventListener("click", () => targetChapter.scrollIntoView({behavior: "smooth", block: "start"}));
        toc.append(link);
      } else {
        const node = document.createElement(level === 3 ? "h3" : "h4");
        appendGuideInline(node, title);
        chapter.append(node);
      }
      index += 1;
      continue;
    }

    if (/^```/.test(line)) {
      const language = line.slice(3).trim();
      const block = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) {
        block.push(lines[index]);
        index += 1;
      }
      index += 1;
      const code = block.join("\n").trimEnd();
      const promptMeta = guidePromptMeta(code);
      const shell = document.createElement("section");
      shell.className = `guide-code-card${promptMeta ? " is-prompt" : ""}`;
      const top = document.createElement("div");
      top.className = "guide-code-top";
      const label = document.createElement("span");
      label.textContent = promptMeta?.[0] || (language ? language.toUpperCase() : "COPYABLE TEXT");
      top.append(label, makeCopyButton(
        promptMeta ? () => contextualizeGuidePrompt(code) : code,
        promptMeta ? "Copy prompt" : "Copy",
        promptMeta ? {template: code} : {requiresDeck: false},
      ));
      const pre = document.createElement("pre");
      if (promptMeta) pre.dataset.promptTemplate = code;
      pre.textContent = promptMeta ? contextualizeGuidePrompt(code) : code;
      shell.append(top, pre);
      chapter.append(shell);
      continue;
    }

    if (/^---+$/.test(line.trim())) {
      const divider = document.createElement("div");
      divider.className = "guide-divider";
      chapter.append(divider);
      index += 1;
      continue;
    }

    if (/^\|/.test(line) && /^\|?\s*:?-+/.test(lines[index + 1] || "")) {
      index = appendGuideTable(chapter, lines, index);
      continue;
    }

    const listStart = line.match(/^\s*([-*]|\d+\.)\s+(.+)$/);
    if (listStart) {
      const ordered = /\d+\./.test(listStart[1]);
      const list = document.createElement(ordered ? "ol" : "ul");
      let item = null;
      while (index < lines.length) {
        const match = lines[index].match(/^\s*([-*]|\d+\.)\s+(.+)$/);
        if (match && /\d+\./.test(match[1]) === ordered) {
          item = document.createElement("li");
          appendGuideInline(item, match[2]);
          list.append(item);
          index += 1;
        } else if (item && lines[index].trim() && !isGuideBlockStart(lines, index)) {
          item.append(document.createTextNode(` ${lines[index].trim()}`));
          index += 1;
        } else {
          break;
        }
      }
      chapter.append(list);
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length && !isGuideBlockStart(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    if (paragraphLines.length) {
      const paragraph = document.createElement("p");
      appendGuideInline(paragraph, paragraphLines.join(" "));
      chapter.append(paragraph);
    } else {
      index += 1;
    }
  }
  return chapterCount;
}

async function loadGuide() {
  if (state.guideLoaded) return;
  state.guideLoaded = true;
  try {
    const result = await api("/api/guide");
    const prompts = renderPromptLibrary(result.markdown);
    const chapters = renderGuide(result.markdown);
    updateContextualPrompts();
    $("#guide-source-label").textContent = `${result.file} · ${chapters} chapters · ${prompts} copyable prompts`;
  } catch (error) {
    state.guideLoaded = false;
    $("#guide-source-label").textContent = "The guide could not be loaded.";
    $("#guide-content").replaceChildren();
    const message = document.createElement("p");
    message.className = "guide-error";
    message.textContent = error.message;
    $("#guide-content").append(message);
    toast(error.message, "error");
  }
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
    if (!state.selectedDeck) {
      state.selectedDeck = state.decks.find((deck) => !deck.verified) || state.decks[0] || null;
    }
    updateContextualPrompts();
    if (render) renderDecks();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderPromptDeckSelector() {
  const select = $("#prompt-deck-select");
  if (!select) return;
  const deck = activeReviewDeck();
  select.replaceChildren();
  if (!state.decks.length) {
    const option = document.createElement("option");
    option.textContent = "No completed decks yet";
    select.append(option);
    select.disabled = true;
    $("#prompt-context-title").textContent = "No review deck is available yet";
    $("#prompt-context-status").textContent = "Finish a build, then return here for personalized prompts.";
    $("#open-context-review").disabled = true;
    return;
  }

  const groups = [
    ["Needs review", state.decks.filter((item) => !item.verified)],
    ["Verified", state.decks.filter((item) => item.verified)],
  ];
  for (const [label, decks] of groups) {
    if (!decks.length) continue;
    const group = document.createElement("optgroup");
    group.label = label;
    for (const item of decks) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = `${item.name} · ${prettyStatus(item.status)}`;
      group.append(option);
    }
    select.append(group);
  }
  select.disabled = false;
  select.value = deck?.name || state.decks[0].name;
  $("#prompt-context-title").textContent = deck?.name || "Choose a deck";
  $("#prompt-context-status").textContent = deck?.verified
    ? "Verified · prompts below are still personalized for grading or follow-up work."
    : `${deck?.judgement_count || 0} judgement call${deck?.judgement_count === 1 ? "" : "s"} · awaiting your decision.`;
  $("#open-context-review").disabled = !deck;
}

function selectReviewDeck(name, render = true) {
  const deck = state.decks.find((item) => item.name === name) || null;
  state.selectedDeck = deck;
  updateContextualPrompts();
  if (render) renderDecks();
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
    button.addEventListener("click", () => selectReviewDeck(deck.name));
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

function approvalPrompt(deck) {
  return `Approved. Pass it. Run: python3 scripts/verify_deck.py --pass "${deck.name}"`;
}

function patchPrompt(deck) {
  const feedback = state.patchNotes[deck.name]?.trim() || "[Your correction will appear here]";
  return `${feedback} Apply that correction to "${deck.name}". Now read scripts/PROMPT_patch.md and execute it for "${deck.name}".`;
}

function reviewSectionHeading(step, title) {
  const head = document.createElement("div");
  head.className = "review-section-head";
  const marker = document.createElement("span");
  marker.textContent = `STEP ${step}`;
  const heading = document.createElement("h3");
  heading.textContent = title;
  head.append(marker, heading);
  return head;
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
  summary.className = "deck-summary";
  if (deck.cards_before != null || deck.cards_after != null) {
    summary.textContent = `Cards: ${deck.cards_before ?? "?"} before · ${deck.cards_after ?? "?"} after`;
  } else {
    summary.textContent = "Open the evidence and judgement calls before making the final decision.";
  }
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  actions.append(
    actionButton("Open notes", deck.notes_path, true),
    actionButton("Open verification report", deck.report_path),
    actionButton("Open deck folder", deck.folder_path),
  );

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
      : "No judgement-call section was found yet. Open the report or audit folder before deciding.";
    calls.append(item);
  }

  const decisionGrid = document.createElement("div");
  decisionGrid.className = "review-decision-grid";

  const approve = document.createElement("section");
  approve.className = `decision-card approve${deck.verified ? " complete" : ""}`;
  const approveLabel = document.createElement("span");
  approveLabel.className = "decision-label";
  approveLabel.textContent = deck.verified ? "✓ VERIFIED" : "HUMAN APPROVAL";
  const approveHeading = document.createElement("h4");
  approveHeading.textContent = deck.verified ? "This deck is already closed" : "Everything holds up";
  const approveCopy = document.createElement("p");
  approveCopy.textContent = deck.verified
    ? "No pass command is needed. You can still request a correction if later review finds a problem."
    : "This exact module name is already in the command.";
  const approvePreview = document.createElement("pre");
  approvePreview.className = "review-prompt-preview";
  approvePreview.textContent = approvalPrompt(deck);
  const approveButton = makeCopyButton(
    () => approvalPrompt(deck),
    deck.verified ? "Already passed" : "Copy approval prompt",
    {requiresDeck: false, disabled: deck.verified},
  );
  approveButton.classList.add("decision-copy");
  approve.append(approveLabel, approveHeading, approveCopy, approvePreview, approveButton);

  const correct = document.createElement("section");
  correct.className = "decision-card correct";
  const correctLabel = document.createElement("span");
  correctLabel.className = "decision-label";
  correctLabel.textContent = "CORRECTION LOOP";
  const correctHeading = document.createElement("h4");
  correctHeading.textContent = "Something should change";
  const correctCopy = document.createElement("p");
  correctCopy.textContent = "Describe the outcome you want. Prism adds the deck and patch instructions.";
  const feedback = document.createElement("textarea");
  feedback.className = "judgement-feedback";
  feedback.maxLength = 6000;
  feedback.rows = 4;
  feedback.value = state.patchNotes[deck.name] || "";
  feedback.placeholder = "Example: Card 12 uses the wrong tidal volume. Change 50 mL to 500 mL and verify every related card.";
  feedback.setAttribute("aria-label", `Correction requested for ${deck.name}`);
  const correctPreview = document.createElement("pre");
  correctPreview.className = "review-prompt-preview patch-preview";
  correctPreview.textContent = patchPrompt(deck);
  const correctButton = makeCopyButton(
    () => patchPrompt(deck),
    "Copy correction prompt",
    {requiresDeck: false, requiresFeedback: true},
  );
  correctButton.classList.add("decision-copy");
  feedback.addEventListener("input", () => {
    state.patchNotes[deck.name] = feedback.value;
    correctPreview.textContent = patchPrompt(deck);
    updateContextualPrompts();
    refreshCopyButton(correctButton);
  });
  correct.append(correctLabel, correctHeading, correctCopy, feedback, correctPreview, correctButton);

  decisionGrid.append(approve, correct);
  detail.append(
    status, heading, summary, actions,
    reviewSectionHeading(1, `Inspect judgement calls (${deck.judgement_count})`), calls,
    reviewSectionHeading(2, "Make the call"), decisionGrid,
  );
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
    state.patchNotes = {};
    state.update = null;
    state.guideLoaded = false;
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
      state.patchNotes = {};
      state.guideLoaded = false;
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
    const visible = filteredDecks();
    if (!visible.some((deck) => deck.name === state.selectedDeck?.name)) {
      state.selectedDeck = visible[0] || null;
    }
    updateContextualPrompts();
    renderDecks();
    if (!state.selectedDeck) {
      $("#deck-detail").replaceChildren();
      const empty = document.createElement("div");
      empty.className = "empty-detail";
      const heading = document.createElement("h2");
      heading.textContent = "No decks in this view";
      empty.append(heading);
      $("#deck-detail").append(empty);
    }
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
  $("#jump-prompts").addEventListener("click", () => {
    $("#prompt-library").scrollIntoView({behavior: "smooth", block: "start"});
  });
  $("#prompt-deck-select").addEventListener("change", (event) => {
    selectReviewDeck(event.currentTarget.value);
  });
  $("#open-context-review").addEventListener("click", () => {
    const deck = activeReviewDeck();
    if (!deck) return;
    state.filter = deck.verified ? "verified" : "review";
    $$(".segmented button").forEach((button) => {
      button.classList.toggle("active", button.dataset.filter === state.filter);
    });
    setView("decks");
  });
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
