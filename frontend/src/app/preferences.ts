// A person's preferences, kept in this browser only: the colourblind palette and the density (the user menu), applied
// as attributes of <html>, which tokens.css reads; the colour scheme is Mantine's (its own key); and the screens' own
// (the builder's pricing options and the sheet being edited). Every key starts with "optlab-".
export type Palette = "standard" | "colorblind";
export type Density = "compact" | "comfortable";

const KEYS = { palette: "optlab-palette", density: "optlab-density" } as const;

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // private window or blocked storage: the preference lasts until the page reloads
  }
}

export function palette(): Palette {
  return read(KEYS.palette) === "colorblind" ? "colorblind" : "standard";
}

export function density(): Density {
  return read(KEYS.density) === "comfortable" ? "comfortable" : "compact";
}

function apply(): void {
  const root = document.documentElement;
  if (palette() === "colorblind") root.setAttribute("data-palette", "colorblind");
  else root.removeAttribute("data-palette");
  if (density() === "comfortable") root.setAttribute("data-density", "comfortable");
  else root.removeAttribute("data-density");
}

export function setPalette(p: Palette): void {
  write(KEYS.palette, p === "colorblind" ? p : null);
  apply();
}

export function setDensity(d: Density): void {
  write(KEYS.density, d === "comfortable" ? d : null);
  apply();
}

/** At start, before the first render. */
export function applyPreferences(): void {
  apply();
}

/** A JSON value this browser keeps for the person (the pricing options, the sheet being edited), or null. */
export function stored<T>(key: string): T | null {
  const text = read(key);
  if (!text) return null;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

export function store(key: string, value: unknown): void {
  write(key, value === null ? null : JSON.stringify(value));
}

/** Everything this app keeps in this browser (display, options, drafts): forgotten, as v0's "clear local preferences". */
export function forgetAll(): void {
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith("optlab-")) localStorage.removeItem(k);
  } catch {
    // blocked storage: nothing was kept
  }
  apply();
}
