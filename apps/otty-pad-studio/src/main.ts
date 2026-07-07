import { invoke } from "@tauri-apps/api/core";
import {
  canSend,
  formatPaneLabel,
  parseMarkdownOutline,
  renderMarkdown,
  selectedPaneStillExists,
  splitPromptCapsules,
  validatePrompt,
  type Pane,
} from "./lib/prompt";
import "./styles.css";

type OttyInfo = {
  available: boolean;
  version: string;
  sendKeysEnabled: boolean;
};

const starterPrompt = `# Mission\n\nWrite the prompt here. Use Markdown for structure.\n\n- Keep the ask concrete\n- Add context if needed\n- Close with the expected output\n\n---\n\nSecond capsule, if you want to send another block later.`;

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("#app root missing");

app.innerHTML = `
  <section class="shell" aria-label="Otty Pad Studio">
    <aside class="pane-rail" aria-label="Otty panes">
      <div class="brand-lockup">
        <span class="radar-dot"></span>
        <div>
          <p>Otty Pad Studio</p>
          <strong>Radar Manuscript</strong>
        </div>
      </div>
      <div class="rail-actions">
        <button id="refresh" type="button">Refresh panes</button>
        <button id="capture" type="button">Capture selected</button>
      </div>
      <label class="filter-label">Filter panes<input id="filter" type="search" placeholder="title, cwd, id" /></label>
      <div id="pane-list" class="pane-list" role="listbox" aria-label="Pane list"></div>
    </aside>

    <section class="composer" aria-label="Markdown prompt editor">
      <header class="composer-head">
        <div>
          <p class="eyebrow">Markdown prompt</p>
          <h1>Write once. Aim once. Send safely.</h1>
        </div>
        <div class="toolbar" aria-label="Markdown shortcuts">
          <button data-insert="# ">Heading</button>
          <button data-insert="- [ ] ">Checklist</button>
          <button data-insert="\n\`\`\`\n\`\`\`\n">Code fence</button>
          <button data-insert="\n---\n">Separator</button>
        </div>
      </header>
      <textarea id="prompt" spellcheck="true" aria-label="Prompt Markdown"></textarea>
      <div class="send-row">
        <label class="toggle" title="When checked, send the prompt then press Enter automatically"><input id="submit" type="checkbox" checked /> Auto-send</label>
        <button id="copy" type="button">Copy Markdown</button>
        <button id="clear" type="button">Clear draft</button>
        <button id="send" type="button">Send to selected pane</button>
      </div>
    </section>

    <aside class="preview" aria-label="Markdown preview and diagnostics">
      <section class="status-card">
        <p class="eyebrow">Dry-run envelope</p>
        <dl>
          <div><dt>Target</dt><dd id="target">No pane selected</dd></div>
          <div><dt>Chars</dt><dd id="chars">0</dd></div>
          <div><dt>Otty</dt><dd id="otty-info">checking...</dd></div>
          <div><dt>State</dt><dd id="state" role="status" aria-live="polite">unsent</dd></div>
        </dl>
      </section>
      <section>
        <p class="eyebrow">Mission outline</p>
        <ol id="outline" class="outline"></ol>
      </section>
      <section>
        <p class="eyebrow">Sealed capsules</p>
        <div id="capsules" class="capsules"></div>
      </section>
      <section>
        <p class="eyebrow">Preview</p>
        <article id="rendered" class="rendered"></article>
      </section>
      <section>
        <p class="eyebrow">Capture</p>
        <pre id="capture-output" class="capture-output">No capture yet.</pre>
      </section>
    </aside>
  </section>
`;

const paneList = document.querySelector<HTMLDivElement>("#pane-list")!;
const promptInput = document.querySelector<HTMLTextAreaElement>("#prompt")!;
const sendButton = document.querySelector<HTMLButtonElement>("#send")!;
const captureButton = document.querySelector<HTMLButtonElement>("#capture")!;
const refreshButton = document.querySelector<HTMLButtonElement>("#refresh")!;
const filterInput = document.querySelector<HTMLInputElement>("#filter")!;
const submitInput = document.querySelector<HTMLInputElement>("#submit")!;
const target = document.querySelector<HTMLElement>("#target")!;
const chars = document.querySelector<HTMLElement>("#chars")!;
const ottyInfo = document.querySelector<HTMLElement>("#otty-info")!;
const stateLabel = document.querySelector<HTMLElement>("#state")!;
const outline = document.querySelector<HTMLOListElement>("#outline")!;
const capsules = document.querySelector<HTMLDivElement>("#capsules")!;
const rendered = document.querySelector<HTMLElement>("#rendered")!;
const captureOutput = document.querySelector<HTMLPreElement>("#capture-output")!;

let panes: Pane[] = [];
let selectedPaneId = "";
let userSelectedPane = false;
let sending = false;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function studioState() {
  return { selectedPaneId, prompt: promptInput.value, sending };
}

function setState(text: string) {
  stateLabel.textContent = text;
}

function renderPanes() {
  const filter = filterInput.value.toLowerCase();
  paneList.replaceChildren();
  panes
    .filter((pane) => formatPaneLabel(pane).toLowerCase().includes(filter))
    .forEach((pane) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `pane-card ${pane.id === selectedPaneId ? "selected" : ""}`;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(pane.id === selectedPaneId));
      button.append(
        el("span", "", `${pane.agent ? "AGENT" : "SHELL"} · ${pane.active ? "LIVE" : `#${pane.index}`}`),
        el("strong", "", pane.process || "(shell)"),
        el("small", "", `${pane.id} · ${pane.cwd || "unknown cwd"}`),
      );
      button.addEventListener("click", () => {
        selectedPaneId = pane.id;
        userSelectedPane = true;
        update();
      });
      paneList.append(button);
    });
}

function renderPreview() {
  const prompt = promptInput.value;
  chars.textContent = String(prompt.length);
  target.textContent = selectedPaneId || "Choose a pane";
  sendButton.disabled = !canSend(studioState());
  outline.replaceChildren(
    ...parseMarkdownOutline(prompt).map((heading) => {
      const item = el("li", `depth-${heading.level}`, `L${heading.line} · ${heading.text}`);
      return item;
    }),
  );
  capsules.replaceChildren(
    ...splitPromptCapsules(prompt).map((capsule) => {
      const card = el("div", "capsule");
      card.append(
        el("strong", "", `#${capsule.index}`),
        el("span", "", `L${capsule.startLine}-${capsule.endLine}`),
        el("p", "", capsule.text.slice(0, 120)),
      );
      return card;
    }),
  );
  rendered.innerHTML = renderMarkdown(prompt);
}

function update() {
  renderPanes();
  renderPreview();
  localStorage.setItem("otty-pad-studio:draft", promptInput.value);
}

async function refreshPanes() {
  try {
    panes = await invoke<Pane[]>("pane_list");
    if (!selectedPaneStillExists(panes, selectedPaneId)) {
      selectedPaneId = "";
      userSelectedPane = false;
    }
    setState(`ready · ${panes.length} panes`);
  } catch (error) {
    panes = [];
    selectedPaneId = "";
    setState(String(error));
  }
  update();
}

async function refreshInfo() {
  try {
    const info = await invoke<OttyInfo>("otty_info");
    ottyInfo.textContent = `${info.version} · send-keys ${info.sendKeysEnabled ? "on" : "off"}`;
  } catch (error) {
    ottyInfo.textContent = String(error);
  }
}

async function sendPrompt() {
  let prompt: string;
  try {
    prompt = validatePrompt(promptInput.value);
  } catch (error) {
    setState(String(error));
    update();
    return;
  }
  if (!selectedPaneId.trim()) {
    setState("select a pane first");
    return;
  }
  if (!userSelectedPane || !selectedPaneStillExists(panes, selectedPaneId)) {
    setState("click a current pane before sending");
    selectedPaneId = "";
    userSelectedPane = false;
    update();
    return;
  }
  sending = true;
  setState("sending...");
  update();
  try {
    await invoke("send_prompt", { paneId: selectedPaneId, prompt, submit: submitInput.checked });
    setState(`sent to ${selectedPaneId}`);
  } catch (error) {
    setState(String(error));
  } finally {
    sending = false;
    update();
  }
}

async function captureSelected() {
  if (!selectedPaneId.trim()) {
    setState("select a pane before capture");
    return;
  }
  try {
    captureOutput.textContent = await invoke<string>("capture_pane", { paneId: selectedPaneId });
    setState(`captured ${selectedPaneId}`);
  } catch (error) {
    captureOutput.textContent = String(error);
    setState("capture failed");
  }
}

promptInput.value = localStorage.getItem("otty-pad-studio:draft") || starterPrompt;
promptInput.addEventListener("input", update);
filterInput.addEventListener("input", renderPanes);
refreshButton.addEventListener("click", refreshPanes);
captureButton.addEventListener("click", captureSelected);
sendButton.addEventListener("click", sendPrompt);
document.querySelector<HTMLButtonElement>("#copy")!.addEventListener("click", async () => {
  await navigator.clipboard.writeText(promptInput.value);
  setState("copied markdown");
});
document.querySelector<HTMLButtonElement>("#clear")!.addEventListener("click", () => {
  promptInput.value = "";
  localStorage.removeItem("otty-pad-studio:draft");
  setState("draft cleared");
  update();
});
document.querySelectorAll<HTMLButtonElement>("[data-insert]").forEach((button) => {
  button.addEventListener("click", () => {
    const insert = button.dataset.insert || "";
    const start = promptInput.selectionStart;
    const end = promptInput.selectionEnd;
    promptInput.value = `${promptInput.value.slice(0, start)}${insert}${promptInput.value.slice(end)}`;
    promptInput.focus();
    promptInput.selectionStart = promptInput.selectionEnd = start + insert.length;
    update();
  });
});
window.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    void sendPrompt();
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "r") {
    event.preventDefault();
    void refreshPanes();
  }
});

void refreshInfo();
void refreshPanes();
update();
