"use strict";
// Runs the digest page's script against a minimal DOM stand-in, with no network:
// every external script (Leaflet, map.js) fails to load. Prints what the list shows.
// Usage: node page_harness.js <index.html> '<actions JSON>' '<localStorage JSON>' [now ISO]
const fs = require("fs");

const html = fs.readFileSync(process.argv[2], "utf8");
const actions = JSON.parse(process.argv[3] || "[]");
const storage = new Map(Object.entries(JSON.parse(process.argv[4] || "{}")));
const dataText = html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const code = html.match(/<script>\n([\s\S]*?)<\/script>/)[1];
const markup = html.replace(/<script[\s\S]*?<\/script>/g, "");
const staticIds = new Set(["data", ...[...markup.matchAll(/ id="([^"]+)"/g)].map((m) => m[1])]);
const loaded = [];
const layout = {};
const scrolled = [];
const windowListeners = {};
if (process.argv[5]) Date.now = () => Date.parse(process.argv[5]);

const elements = new Map();
class Element {
  constructor(id) {
    this.id = id;
    this.html = "";
    this.textContent = id === "data" ? dataText : "";
    this.hidden = false;
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.open = false;
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    const classes = new Set();
    this.classList = {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      toggle: (name, on) => ((on ?? !classes.has(name)) ? classes.add(name) : classes.delete(name)),
      contains: (name) => classes.has(name),
    };
  }
  get innerHTML() { return this.html; }
  set innerHTML(text) {
    this.html = text;
    for (const id of [...elements.keys()]) if (!staticIds.has(id)) elements.delete(id);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(type, listener) { (this.listeners[type] = this.listeners[type] || []).push(listener); }
  insertAdjacentHTML(position, text) { this.html += text; }
  appendChild(child) {
    if (child.src) { loaded.push(child.src); setTimeout(() => child.onerror && child.onerror(new Error("offline")), 0); }
    return child;
  }
  scrollIntoView() {}
  getBoundingClientRect() { return layout[this.id] || { top: 0, bottom: 0 }; }
}

global.document = {
  getElementById(id) {
    if (elements.has(id)) return elements.get(id);
    const dynamic = [...elements.values()].some((element) => element.html.includes(` id="${id}"`));
    if (!staticIds.has(id) && !dynamic) return null;
    const element = new Element(id);
    elements.set(id, element);
    return element;
  },
  createElement: () => new Element(""),
  head: new Element("head"),
  documentElement: { dataset: {} },
};
global.window = global;
global.localStorage = {
  getItem: (key) => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => storage.set(key, String(value)),
};
global.matchMedia = () => ({ matches: false, addEventListener() {} });
global.getComputedStyle = () => ({ getPropertyValue: () => "#123456" });
global.scrollTo = () => {};
global.innerWidth = 375;
global.scrollBy = (x, y) => scrolled.push([x, y]);
global.requestAnimationFrame = (callback) => setTimeout(callback, 16);
global.addEventListener = (type, listener) => (windowListeners[type] = windowListeners[type] || []).push(listener);

const settle = () => new Promise((resolve) => setTimeout(resolve, 5));
const target = (action) => ({
  closest: (selector) => {
    if (selector.startsWith("a[")) return action.open ? { dataset: { open: action.open } } : null;
    if (selector.startsWith("button[data-act]")) return action.act ? { dataset: { act: action.act, id: action.id } } : null;
    return null;
  },
});

(async () => {
  new Function(code)();
  await settle();
  for (const action of actions) {
    if (action.width) {
      global.innerWidth = action.width;
    } else if (action.layout) {
      Object.assign(layout, action.layout);
    } else if (action.event) {
      for (const listener of windowListeners[action.event] || []) listener({});
    } else if (action.wait) {
      await new Promise((resolve) => setTimeout(resolve, action.wait));
    } else if (action.change) {
      const element = document.getElementById(action.change);
      if ("value" in action) element.value = action.value;
      if ("checked" in action) element.checked = action.checked;
      for (const listener of element.listeners.change || []) listener({});
    } else {
      for (const listener of document.getElementById(action.click).listeners.click || []) listener({ target: target(action) });
    }
    await settle();
  }
  const listed = document.getElementById("list").innerHTML + (document.getElementById("grid") || { html: "" }).html;
  const note = document.getElementById("map-note");
  const stale = document.getElementById("stale");
  process.stdout.write(JSON.stringify({
    cards: [...listed.matchAll(/<article class="[^"]*" id="c-([^"]+)"/g)].map((m) => m[1]),
    list: listed,
    today: document.getElementById("view-today").textContent,
    all: document.getElementById("view-all").textContent,
    visitDisabled: document.getElementById("f-visit").disabled,
    mapNote: note.hidden ? null : note.textContent,
    stale: stale.hidden ? null : stale.textContent,
    scrolled,
    loaded,
    storage: Object.fromEntries(storage),
  }));
})().catch((error) => { process.stderr.write(String(error && error.stack || error)); process.exit(1); });
