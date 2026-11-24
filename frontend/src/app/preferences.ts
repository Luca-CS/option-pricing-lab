// A person's display preferences, kept in this browser only (the user menu): the colourblind palette and the density.
// The colour scheme is Mantine's (its own key). Applied as attributes of <html>, which tokens.css reads.
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
