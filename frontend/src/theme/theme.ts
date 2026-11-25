// The desk's Mantine theme: graphite and amber (docs/ux.md §4). Our own tokens, used by
// the app's CSS, are in tokens.css; Mantine's variables point at the same colours. No React here: the build reads this
// file to write Mantine's CSS variables statically (tooling/mantine-vars.ts).
import {
  type CSSVariablesResolver,
  createTheme,
  defaultVariantColorsResolver,
  type MantineColorsTuple,
  parseThemeColor,
  type VariantColorsResolver,
} from "@mantine/core";

// interactive: links, selection, primary buttons
const accent: MantineColorsTuple = [
  "#e6f8fc",
  "#c5eef7",
  "#9fe2f1",
  "#78d6ea",
  "#56c2de",
  "#3fb0ce",
  "#2b97b3",
  "#1f7a92",
  "#165e71",
  "#0d4252",
];

// the brand and the focus ring, used sparingly
const amber: MantineColorsTuple = [
  "#fff6e5",
  "#fde7bf",
  "#f9d38f",
  "#f5be5f",
  "#f0a93a",
  "#d9921f",
  "#b47712",
  "#9a5b00",
  "#7a4700",
  "#5a3400",
];

// graphite: text (0) down to the ground (9); Mantine's dark scheme reads 0, 2, 4, 5, 6 and 7
const dark: MantineColorsTuple = [
  "#e7e9ec",
  "#c9ced4",
  "#a0a8b1",
  "#7f8893",
  "#3a4047",
  "#2b3036",
  "#23282e",
  "#1a1d21",
  "#16191c",
  "#121417",
];

// paper: the light scheme's warm greys
const gray: MantineColorsTuple = [
  "#faf9f6",
  "#f6f5f1",
  "#efede7",
  "#e6e3db",
  "#dddad2",
  "#bdb8ad",
  "#565e67",
  "#454c54",
  "#2e3439",
  "#1d2125",
];

// The ink on a filled accent or amber button follows the scheme: dark on the bright dark-scheme shade, white on the
// deep light-scheme one (Mantine's own auto contrast reads the light shade only, which fails AA in the dark scheme).
const INK: Record<string, string> = { accent: "var(--ol-accent-ink)", amber: "var(--ol-brand-ink)" };

const variantColorResolver: VariantColorsResolver = (input) => {
  const colors = defaultVariantColorsResolver(input);
  const parsed = parseThemeColor({ color: input.color || input.theme.primaryColor, theme: input.theme });
  const ink = INK[parsed.color];
  if (input.variant === "filled" && parsed.isThemeColor && ink !== undefined) {
    return { ...colors, color: ink };
  }
  return colors;
};

export const theme = createTheme({
  variantColorResolver,
  primaryColor: "accent",
  primaryShade: { light: 7, dark: 4 },
  colors: { accent, amber, dark, gray },
  fontFamily: '"Inter Variable", Inter, system-ui, sans-serif',
  fontFamilyMonospace: '"JetBrains Mono Variable", "JetBrains Mono", ui-monospace, monospace',
  headings: { fontFamily: '"Inter Variable", Inter, system-ui, sans-serif', fontWeight: "600" },
  defaultRadius: "sm",
  focusRing: "auto",
  cursorType: "pointer",
  components: {
    // a modal or a drawer locks the page's scroll through react-remove-scroll, which writes a <style> element: the
    // page CSP refuses it (ADR-0024), so they leave the page as it is (the palette is a modal too)
    // a dialog's close button is an icon: it needs its name (axe: button-name)
    Modal: { defaultProps: { lockScroll: false, closeButtonProps: { "aria-label": "Close" } } },
    Drawer: { defaultProps: { lockScroll: false, closeButtonProps: { "aria-label": "Close" } } },
    Notification: { defaultProps: { closeButtonProps: { "aria-label": "Hide this notification" } } },
  },
});

export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {
    "--mantine-color-body": "#f6f5f1",
    "--mantine-color-text": "#1d2125",
    "--mantine-color-dimmed": "#565e67",
  },
  dark: {
    "--mantine-color-body": "#121417",
    "--mantine-color-text": "#e7e9ec",
    "--mantine-color-dimmed": "#a0a8b1",
  },
});
