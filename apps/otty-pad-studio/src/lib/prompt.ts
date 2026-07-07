export interface Heading {
  level: number;
  text: string;
  line: number;
}

export interface PromptCapsule {
  index: number;
  text: string;
  startLine: number;
  endLine: number;
}

export interface Pane {
  id: string;
  window_id: string;
  tab_id: string;
  index: number;
  active: boolean;
  cwd: string;
  process: string;
  cols: number;
  rows: number;
  agent: boolean;
}

export interface StudioState {
  selectedPaneId: string;
  prompt: string;
  sending: boolean;
}

export function validatePrompt(text: string): string {
  if (!text.trim()) {
    throw new Error("prompt is empty; nothing to send");
  }
  return text;
}

export function canSend(state: StudioState): boolean {
  return Boolean(state.selectedPaneId.trim()) && Boolean(state.prompt.trim()) && !state.sending;
}

export function formatPaneLabel(pane: Pane): string {
  const marker = pane.agent ? "AGENT" : "SHELL";
  const active = pane.active ? "LIVE" : `#${pane.index}`;
  const title = pane.process.trim() || "(shell)";
  const cwd = pane.cwd ? pane.cwd.split("/").filter(Boolean).slice(-2).join("/") : "unknown cwd";
  return `${marker} ${active} ${pane.id} ${title} · ${cwd}`;
}

export function parseMarkdownOutline(markdown: string): Heading[] {
  const headings: Heading[] = [];
  let inFence = false;
  markdown.split(/\r?\n/).forEach((line, index) => {
    if (line.trim().startsWith("```")) {
      inFence = !inFence;
      return;
    }
    if (inFence) return;
    const match = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (match) {
      headings.push({ level: match[1].length, text: match[2], line: index + 1 });
    }
  });
  return headings;
}

export function splitPromptCapsules(markdown: string): PromptCapsule[] {
  const lines = markdown.split(/\r?\n/);
  const capsules: PromptCapsule[] = [];
  let current: string[] = [];
  let startLine = 1;

  const flush = (endLine: number) => {
    const text = current.join("\n").trim();
    if (text) {
      capsules.push({ index: capsules.length + 1, text, startLine, endLine });
    }
    current = [];
    startLine = endLine + 1;
  };

  lines.forEach((line, index) => {
    if (line.trim() === "---") {
      flush(index);
    } else {
      current.push(line);
    }
  });
  flush(lines.length);
  return capsules;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inlineMarkdown(text: string): string {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

export function renderMarkdown(markdown: string): string {
  const html: string[] = [];
  let inCode = false;
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      html.push("</ul>");
      listOpen = false;
    }
  };

  markdown.split(/\r?\n/).forEach((line) => {
    if (line.trim().startsWith("```")) {
      closeList();
      html.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      return;
    }
    if (inCode) {
      html.push(`${escapeHtml(line)}\n`);
      return;
    }
    const heading = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      return;
    }
    const item = /^[-*]\s+(.+)$/.exec(line);
    if (item) {
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${inlineMarkdown(item[1])}</li>`);
      return;
    }
    closeList();
    if (line.trim() === "---") {
      html.push('<hr aria-label="prompt separator" />');
    } else if (line.trim()) {
      html.push(`<p>${inlineMarkdown(line)}</p>`);
    }
  });
  closeList();
  if (inCode) html.push("</code></pre>");
  return html.join("\n");
}

export function selectedPaneStillExists(panes: Pane[], selectedPaneId: string): boolean {
  return panes.some((pane) => pane.id === selectedPaneId);
}
