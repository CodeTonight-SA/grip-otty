import assert from "node:assert/strict";
import test from "node:test";
import {
  canSend,
  formatPaneLabel,
  parseMarkdownOutline,
  renderMarkdown,
  selectedPaneStillExists,
  splitPromptCapsules,
  validatePrompt,
  type Pane,
} from "./prompt.ts";

const pane: Pane = {
  id: "p_1",
  window_id: "w_1",
  tab_id: "t_1",
  index: 0,
  active: true,
  cwd: "/Users/void/CodeTonight/grip-otty",
  process: "⠐ Ship Otty Pad Studio",
  cols: 120,
  rows: 40,
  agent: true,
};

test("formatPaneLabel includes id, state, process, and agent marker", () => {
  const label = formatPaneLabel(pane);
  assert.match(label, /AGENT/);
  assert.match(label, /LIVE/);
  assert.match(label, /p_1/);
  assert.match(label, /Ship Otty Pad Studio/);
});

test("validatePrompt rejects empty prompts", () => {
  assert.throws(() => validatePrompt("  \n"), /empty/);
  assert.equal(validatePrompt("  hello  "), "  hello  ");
});

test("canSend requires pane, prompt, and idle state", () => {
  assert.equal(canSend({ selectedPaneId: "p_1", prompt: "hello", sending: false }), true);
  assert.equal(canSend({ selectedPaneId: "", prompt: "hello", sending: false }), false);
  assert.equal(canSend({ selectedPaneId: "p_1", prompt: " ", sending: false }), false);
  assert.equal(canSend({ selectedPaneId: "p_1", prompt: "hello", sending: true }), false);
});

test("parseMarkdownOutline ignores headings inside fenced code", () => {
  const outline = parseMarkdownOutline("# Mission\n```\n# not heading\n```\n## Step");
  assert.deepEqual(outline, [
    { level: 1, text: "Mission", line: 1 },
    { level: 2, text: "Step", line: 5 },
  ]);
});

test("splitPromptCapsules uses exact-line separators", () => {
  const capsules = splitPromptCapsules("alpha\n---\nbeta --- still beta\n---\nשלום Claude");
  assert.equal(capsules.length, 3);
  assert.equal(capsules[0].text, "alpha");
  assert.equal(capsules[1].text, "beta --- still beta");
  assert.equal(capsules[2].text, "שלום Claude");
});

test("renderMarkdown escapes HTML and renders basic markdown", () => {
  const html = renderMarkdown("# Hello <x>\n- **bold**\n`code`");
  assert.match(html, /<h1>Hello &lt;x&gt;<\/h1>/);
  assert.match(html, /<strong>bold<\/strong>/);
  assert.match(html, /<code>code<\/code>/);
});

test("selectedPaneStillExists rejects stale ids", () => {
  assert.equal(selectedPaneStillExists([pane], "p_1"), true);
  assert.equal(selectedPaneStillExists([pane], "p_missing"), false);
});
