// Mantine writes its CSS variables and a few global classes into <style> elements at run time. The desk's page CSP
// (`style-src 'self'`, security.md §4) refuses inline styles, so the provider runs with `withCssVariables` and
// `withGlobalClasses` off and this plugin writes the same CSS as a static module at build time
// (`import "virtual:mantine-vars.css"`). The CSS comes from Mantine's own functions on the app's theme, so it stays
// what MantineCssVariables and MantineClasses would have written.
import {
  convertCssVariables,
  DEFAULT_THEME,
  defaultCssVariablesResolver,
  em,
  keys,
  mergeMantineTheme,
  px,
} from "@mantine/core";
import type { Plugin } from "vite";
import { cssVariablesResolver, theme } from "../src/theme/theme.ts";

const ID = "virtual:mantine-vars.css";
const RESOLVED = `\0${ID}`;

export function mantineCss(): string {
  const merged = mergeMantineTheme(DEFAULT_THEME, theme);
  const base = defaultCssVariablesResolver(merged);
  const own = cssVariablesResolver(merged);
  const variables = convertCssVariables(
    {
      variables: { ...base.variables, ...own.variables },
      light: { ...base.light, ...own.light, "--mantine-color-scheme": "light" },
      dark: { ...base.dark, ...own.dark, "--mantine-color-scheme": "dark" },
    },
    ":root",
  );
  const classes = keys(merged.breakpoints)
    .map((b) => {
      const value = String(merged.breakpoints[b]);
      const width = Number(px(value));
      const below = value.includes("px") ? `${width - 0.1}px` : em(width - 0.1);
      const from = value.includes("px") ? `${width}px` : em(width);
      return (
        `@media (max-width: ${below}) {.mantine-visible-from-${b} {display: none !important;}}` +
        `@media (min-width: ${from}) {.mantine-hidden-from-${b} {display: none !important;}}`
      );
    })
    .join("");
  return `${variables}\n${classes}\n`;
}

export function mantineVariables(): Plugin {
  return {
    name: "optlab-mantine-variables",
    resolveId(id) {
      return id === ID ? RESOLVED : undefined;
    },
    load(id) {
      return id === RESOLVED ? mantineCss() : undefined;
    },
  };
}
