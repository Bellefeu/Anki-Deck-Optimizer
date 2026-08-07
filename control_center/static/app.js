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
  workspace: null,
  integrity: null,
};

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const pathMeasure = document.createElement("canvas").getContext("2d");

/* Each view is framed against a different body from the field renderer,
   pushed right of the reading column and dimmed to whatever that screen
   can afford. Dense screens get a quieter backdrop; the screens that are
   mostly heading get a louder one.
 *
 * Scale is the lever that matters most. The same number of samples spread
 * over a larger silhouette gives a dimmer, flatter form, so a body that
 * reads as luminous string art at a quarter of the viewport reads as a
 * grey wall at three quarters of it. Small and bright, with a lot of void
 * around it, is what the reference renders actually do. */
/* recede is how strongly the body climbs out of the way as the page is
   scrolled. Home earns the most: its lower half is bare text and numbers
   sitting straight on the void, with no surface of their own to separate
   them from whatever is lit behind. The guide and the deck screens put
   their content on filled panels, so their bodies can stay where they
   are and keep following the page down. */
/* First run has no rail and no top bar, so its body can sit further into
   the page than any other screen and stay there: there is nothing below
   the fold to be lit from behind. */
const FIELD_SCENES = {
  welcome: {body: "aperture", x: 0.74, y: 0.42, scale: 0.20, point: 1.00, intensity: 1.00, recede: 0.35},
  home: {body: "ruled", x: 0.58, y: 0.38, scale: 0.22, point: 1.05, intensity: 1.25, recede: 1.0},
  guide: {body: "weave", x: 0.50, y: 0.12, scale: 0.24, point: 1.00, intensity: 1.45, recede: 0.45},
  decks: {body: "foam", x: 0.70, y: 0.40, scale: 0.19, point: 1.00, intensity: 1.05, recede: 0.3},
  preferences: {body: "coil", x: 0.74, y: 0.24, scale: 0.17, point: 0.95, intensity: 0.85, recede: 0.3},
  updates: {body: "aperture", x: 0.62, y: 0.32, scale: 0.23, point: 1.05, intensity: 1.20, recede: 0.6},
};

const field = window.PrismField ? window.PrismField.mount($("#prism-field"), {samples: 220000}) : null;

function currentViewName() {
  return ($(".view.active")?.id || "view-home").replace(/^view-/, "");
}

function applyFieldScene(name) {
  if (!field) return;
  const scene = FIELD_SCENES[name] || FIELD_SCENES.home;
  /* Warmth is the one thing the backdrop says about your work rather than
     about itself: it rises only while a deck is still waiting on you. */
  const waiting = state.decks.some((deck) => !deck.verified);
  const warm = waiting && (name === "decks" || name === "home") ? 0.45 : 0.12;

  /* Below the layout breakpoint there is no empty right-hand column to
     stand the body in, so it moves to the top of the page and shrinks.
     Left where it is, it would sit directly under the reading column and
     turn into a grey smear behind the text. */
  const compact = window.innerWidth < 980;
  const framed = compact
    ? {...scene, x: 0.62, y: 0.56, scale: scene.scale * 0.60, intensity: scene.intensity * 0.95}
    : scene;

  field.setScene(scene.body, {...framed, warm});
  syncFieldDrift();
}

/* Full recession by the time roughly one screen has gone past, which is
   about where every view stops being a heading and starts being work. A
   view too short to scroll that far still reaches full recession at its
   own bottom, otherwise the body would sit half in the way and stay
   there with nowhere left to scroll. */
function syncFieldDrift() {
  if (!field) return;
  const reach = document.documentElement.scrollHeight - window.innerHeight;
  const span = Math.max(1, Math.min(reach, window.innerHeight * 1.05));
  field.setDrift(window.scrollY / span);
}

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
  applyFieldScene(name);
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

/* A prompt can be waiting on a deck, on a written correction, or on
   nothing at all. Whatever the answer, the card says it out loud: a
   disabled button with only a tooltip leaves the reason invisible to
   anyone who does not happen to hover it. */
function promptRequirement(template, options = {}) {
  const needsDeck = Boolean(options.requiresDeck ?? promptNeedsDeck(template));
  const needsNote = Boolean(options.requiresFeedback ?? promptNeedsPatchNote(template));
  if (!needsDeck && !needsNote) return null;

  if (needsDeck && !activeModuleName()) {
    return {
      blocked: true,
      text: state.decks.length
        ? "Pick a module under Active review deck above, and this prompt fills in with its name."
        : "No finished deck exists yet. Build one first, then this prompt fills in with its name.",
    };
  }
  if (needsNote && !activePatchNote()) {
    return {
      blocked: true,
      /* The correction box is on the Decks screen. Telling someone who is
         already standing in front of it to go and find it reads as a bug. */
      text: currentViewName() === "decks"
        ? "Write the correction in the box above, and this prompt picks it up."
        : `Open Deck Review, write what should change about ${activeModuleName()}, and this prompt picks it up.`,
    };
  }
  return {blocked: false, text: `Ready, and already written for ${activeModuleName()}.`};
}

function refreshCopyButton(button) {
  const requirement = promptRequirement(button._template, {
    requiresDeck: button._requiresDeck,
    requiresFeedback: button._requiresFeedback,
  });
  button.disabled = Boolean(button._forceDisabled || requirement?.blocked);
  if (requirement?.blocked) button.title = requirement.text;
  else button.removeAttribute("title");
}

function requirementNote(template, options = {}) {
  const requirement = promptRequirement(template, options);
  if (!requirement) return null;
  const note = document.createElement("small");
  note.className = "prompt-requirement";
  note.dataset.promptRequirement = template;
  /* Carried on the node so the live refresh can rebuild the same
     requirement later. Buttons whose prompt is assembled in code rather
     than parsed from the guide have no tokens to infer it from. */
  if (options.requiresDeck !== undefined) note.dataset.requiresDeck = options.requiresDeck ? "1" : "0";
  if (options.requiresFeedback !== undefined) note.dataset.requiresFeedback = options.requiresFeedback ? "1" : "0";
  note.dataset.state = requirement.blocked ? "blocked" : "ready";
  note.textContent = requirement.text;
  return note;
}

function noteOptions(node) {
  const options = {};
  if (node.dataset.requiresDeck !== undefined) options.requiresDeck = node.dataset.requiresDeck === "1";
  if (node.dataset.requiresFeedback !== undefined) options.requiresFeedback = node.dataset.requiresFeedback === "1";
  return options;
}

function updateContextualPrompts() {
  $$('[data-prompt-template]').forEach((node) => {
    node.textContent = contextualizeGuidePrompt(node.dataset.promptTemplate);
  });
  $$('[data-prompt-requirement]').forEach((node) => {
    const requirement = promptRequirement(node.dataset.promptRequirement, noteOptions(node));
    if (!requirement) return;
    node.dataset.state = requirement.blocked ? "blocked" : "ready";
    node.textContent = requirement.text;
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
  if (lower.includes("create a scheduled cowork task")) return ["Claude scheduler", "Automate eight hours in Cowork"];
  if (lower.includes("create a standalone scheduled task")) return ["ChatGPT scheduler", "Automate eight hours in Codex"];
  if (lower.includes("prompt_auto.md")) return ["Automatic mode", "Run the next safe phase"];
  if (lower.includes("prompt_build.md")) return ["Session A", "Build a module"];
  if (lower.includes("prompt_verify.md")) return ["Session B", "Verify in a fresh session"];
  if (lower.includes("prompt_patch.md")) return ["Correction loop", "Patch a judgement call"];
  if (lower.includes("prompt_dedupe.md")) return ["Final step", "Check across every deck"];
  if (lower.startsWith("compare completed/")) return ["Grade a rebuild", "Compare original and final"];
  if (lower.startsWith("grade completed/")) return ["Grade a new deck", "Audit creation mode"];
  if (lower.startsWith("approved. pass it")) return ["Human approval", "Pass the verified deck"];
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
  button._template = template;
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
    const note = requirementNote(prompt.code);
    if (note) card.append(note);
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
        marker.textContent = /^PART\s+\d+/i.test(title) ? title.split(":")[0].trim() : "REFERENCE";
        const node = document.createElement("h2");
        appendGuideInline(node, title.replace(/^PART\s+\d+\s*:\s*/i, ""));
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
      shell.append(top);
      /* The same prompts appear inline in the guide as in the library, so
         they have to explain themselves in both places. */
      const note = promptMeta ? requirementNote(code) : null;
      if (note) shell.append(note);
      shell.append(pre);
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
          item.append(document.createTextNode(" "));
          appendGuideInline(item, lines[index].trim());
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

/* ------------------------------------------------------------------ *
 * First run
 *
 * A downloaded PRISM has no folder to work on until someone says where it
 * should be. Until then the rail, the top bar and every view are simply
 * not drawn, because none of them can answer a question about a project
 * that does not exist yet.
 * ------------------------------------------------------------------ */

function showWelcome(show) {
  document.body.classList.toggle("needs-workspace", show);
  $("#welcome").hidden = !show;
  applyFieldScene(show ? "welcome" : currentViewName());
}

function setWelcomeStatus(message, tone = "") {
  const node = $("#welcome-status");
  node.textContent = message || "";
  if (tone) node.dataset.state = tone;
  else delete node.dataset.state;
}

function welcomeBusy(busy) {
  for (const id of ["#welcome-create", "#welcome-browse", "#welcome-open"]) {
    $(id).disabled = busy;
  }
}

async function loadWorkspaceOptions() {
  try {
    const options = await api("/api/workspace");
    state.workspace = options;
    $("#welcome-eyebrow").textContent = `PRISM ${options.app_version}`;
    const input = $("#welcome-path");
    if (!input.value.trim()) input.value = options.suggested;
    const recent = $("#welcome-recent");
    recent.replaceChildren();
    for (const path of options.recent) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = path;
      button.addEventListener("click", () => openWorkspace(path));
      recent.append(button);
    }
    if (!options.can_create) {
      $("#welcome-create").disabled = true;
      setWelcomeStatus(
        "This copy of PRISM is missing its toolkit payload, so it cannot create a "
        + "workspace. Download PRISM again, or open a folder you already have.",
        "error",
      );
    }
  } catch (error) {
    setWelcomeStatus(error.message, "error");
  }
}

async function enterWorkspace(path) {
  state.selectedDeck = null;
  state.patchNotes = {};
  state.update = null;
  state.guideLoaded = false;
  state.integrity = null;
  showWelcome(false);
  await loadStatus();
  setView("home");
  if (path) toast(`Working in ${path}`, "success");
}

async function createWorkspace() {
  const path = $("#welcome-path").value.trim();
  if (!path) {
    setWelcomeStatus("Give the folder a name before creating it.", "error");
    return;
  }
  welcomeBusy(true);
  setWelcomeStatus("Laying down the toolkit and checking every file", "working");
  try {
    const result = await api("/api/workspace/create", {method: "POST", body: {path}});
    setWelcomeStatus(`Workspace ready at ${result.workspace}`, "done");
    await enterWorkspace(result.workspace);
  } catch (error) {
    setWelcomeStatus(error.message, "error");
  } finally {
    welcomeBusy(false);
  }
}

async function browseForNewWorkspace() {
  welcomeBusy(true);
  try {
    const result = await api("/api/workspace/browse", {method: "POST", body: {mode: "create"}});
    if (result.path) {
      $("#welcome-path").value = result.suggestion || result.path;
      setWelcomeStatus("");
    }
  } catch (error) {
    setWelcomeStatus(error.message, "error");
  } finally {
    welcomeBusy(false);
  }
}

async function openWorkspace(path) {
  welcomeBusy(true);
  setWelcomeStatus("Opening that folder", "working");
  try {
    const result = await api("/api/workspace/open", {method: "POST", body: {path}});
    await enterWorkspace(result.workspace);
  } catch (error) {
    setWelcomeStatus(error.message, "error");
  } finally {
    welcomeBusy(false);
  }
}

async function browseForExistingWorkspace() {
  welcomeBusy(true);
  try {
    const result = await api("/api/workspace/browse", {method: "POST", body: {mode: "open"}});
    if (result.path) {
      await openWorkspace(result.path);
      return;
    }
  } catch (error) {
    setWelcomeStatus(error.message, "error");
  } finally {
    welcomeBusy(false);
  }
}

async function loadStatus() {
  try {
    state.status = await api("/api/status");
    const data = state.status;
    $("#rail-version").textContent = `PRISM ${data.app_version}`;
    $("#rail-toolkit").textContent = data.version ? `toolkit ${data.version}` : "";
    renderHealth(data.health);
    renderApplicationOffer(data);
    if (!data.ready) {
      showWelcome(true);
      await loadWorkspaceOptions();
      return;
    }
    showWelcome(false);
    $("#project-path").value = data.project;
    syncProjectPathWidth();
    const pipeline = data.pipeline;
    $("#metric-total").textContent = pipeline.modules;
    $("#metric-review").textContent = pipeline.built_unverified + pipeline.in_progress;
    $("#metric-verified").textContent = pipeline.verified;
    $("#metric-staged").textContent = data.staged.decks;
    await loadDecks(false);
    renderNextReview();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* Shown only in a browser tab. Inside the application window it would be
   an advertisement for what the reader is already using. */
const PLATFORM_DOWNLOAD = {
  darwin: "Get PRISM for Mac",
  win32: "Get PRISM for Windows",
  linux: "Get PRISM for Linux",
};

function renderApplicationOffer(status) {
  const offer = $("#app-offer");
  if (!offer) return;
  const browser = status.shell?.mode !== "desktop";
  offer.hidden = !browser;
  if (!browser) return;
  const link = $("#app-offer-link");
  link.href = status.releases_url || "#";
  const platform = String(status.shell?.platform || "");
  const key = platform.startsWith("linux") ? "linux" : platform;
  link.textContent = PLATFORM_DOWNLOAD[key] || "Get the application";
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
    applyFieldScene(currentViewName());
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
  correctCopy.textContent = "Describe the outcome you want. PRISM adds the deck and patch instructions.";
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
  const correctNote = requirementNote("", {requiresDeck: false, requiresFeedback: true});
  feedback.addEventListener("input", () => {
    state.patchNotes[deck.name] = feedback.value;
    correctPreview.textContent = patchPrompt(deck);
    updateContextualPrompts();
    refreshCopyButton(correctButton);
  });
  correct.append(correctLabel, correctHeading, correctCopy, feedback);
  if (correctNote) correct.append(correctNote);
  correct.append(correctPreview, correctButton);

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

/* ------------------------------------------------------------------ *
 * Toolkit integrity
 *
 * A workspace is publisher files plus your own work, and only the first
 * half has a known answer. Missing files can be put back from the copy
 * inside PRISM; edited ones are reported and left exactly as they are,
 * because replacing them would throw away work with no way back.
 * ------------------------------------------------------------------ */

function renderIntegrity(report, {restored = 0} = {}) {
  state.integrity = report;
  const detail = $("#integrity-detail");
  const missing = report.missing || [];
  const altered = report.altered || [];
  detail.replaceChildren();

  const lines = [];
  if (restored) {
    lines.push([`Restored ${restored} ${restored === 1 ? "file" : "files"} from the copy inside PRISM.`, "ok"]);
  }
  if (missing.length) {
    lines.push([`${missing.length} toolkit ${missing.length === 1 ? "file is" : "files are"} missing.`, "warn"]);
  }
  if (altered.length) {
    lines.push([`${altered.length} toolkit ${altered.length === 1 ? "file differs" : "files differ"} from the published copy. Nothing was changed.`, "warn"]);
  }
  if (!missing.length && !altered.length && !restored) {
    lines.push(["Every toolkit file matches the published copy.", "ok"]);
  }
  for (const [text, tone] of lines) {
    const note = document.createElement("p");
    note.className = "integrity-note";
    note.dataset.tone = tone;
    note.textContent = text;
    detail.append(note);
  }
  for (const path of [...missing, ...altered].slice(0, 12)) {
    const item = document.createElement("code");
    item.textContent = path;
    detail.append(item);
  }
  $("#repair-integrity").disabled = missing.length === 0;
  $("#integrity-heading").textContent = missing.length || altered.length
    ? "Toolkit needs attention" : "Toolkit is intact";
}

async function checkIntegrity() {
  const button = $("#check-integrity");
  button.disabled = true;
  try {
    const report = await api("/api/workspace/inspect", {method: "POST", body: {}});
    renderIntegrity(report);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function repairIntegrity() {
  const button = $("#repair-integrity");
  button.disabled = true;
  try {
    const result = await api("/api/workspace/repair", {method: "POST", body: {}});
    const report = await api("/api/workspace/inspect", {method: "POST", body: {}});
    renderIntegrity(report, {restored: (result.restored || []).length});
    toast(result.message, "success");
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
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
    card.className = "band job-card";
    $("#job-label").textContent = kind === "check" ? "Checking for updates" : "Installing the update";
    $("#job-log").textContent = "Starting";
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
    $("#job-log").textContent = job.messages.join("\n") || "Working";
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
  /* Crossing the layout breakpoint changes where the field belongs, so
     the composition has to be recomputed rather than just re-projected. */
  let reframe = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(reframe);
    reframe = window.setTimeout(() => applyFieldScene(currentViewName()), 180);
  });
  window.addEventListener("scroll", syncFieldDrift, {passive: true});
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
  $("#check-integrity").addEventListener("click", checkIntegrity);
  $("#repair-integrity").addEventListener("click", repairIntegrity);
  $("#welcome-create").addEventListener("click", createWorkspace);
  $("#welcome-browse").addEventListener("click", browseForNewWorkspace);
  $("#welcome-open").addEventListener("click", browseForExistingWorkspace);
  $("#welcome-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") createWorkspace();
  });
}

/* The application window drives the page through this, so a menu item can
   move the view or report something without the shell needing to know how
   any of it is built. */
window.prismShell = {
  showView(name) {
    if (document.body.classList.contains("needs-workspace")) return false;
    setView(name);
    return true;
  },
  toast(message, kind) {
    toast(String(message || ""), kind === "error" ? "error" : "success");
    return true;
  },
  refresh() {
    loadStatus();
    return true;
  },
};

bindEvents();
applyFieldScene("home");
loadStatus();
