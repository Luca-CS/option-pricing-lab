// Per-browser state: preferences and the work in progress (the book itself lives on the server).

const KEY = "optlab.desk";

const defaults = {
  theme: "system",
  precision: "standard",
  control: true,
  greeks: false,
  mixBand: false, costs: true,
  fast: false,
  controlForwards: false,
  slvVega: false,
  sticky: "moneyness",
  model: "",
  draft: null, // {type, values, preset}
  lastQuote: null,
  lastBook: null,
};

function load() {
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(KEY) || "{}") };
  } catch (e) {
    return { ...defaults };
  }
}

export const state = load();

export function save(changes = {}) {
  Object.assign(state, changes);
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch (e) {
    /* private window or storage full: the page still works */
  }
}

export function applyTheme(theme = state.theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
  try { localStorage.setItem("optlab.theme", theme); } catch (e) { /* ignore */ }
}

// The server context (market) loaded once by main.js and shared by the views.
export const desk = { context: null, health: null };
