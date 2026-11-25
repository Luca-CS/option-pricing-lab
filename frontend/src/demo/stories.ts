// The demo's three guided stories, one per profile (HANDOFF_REFACTOR_2_UX.md §6, Luca's choice of 24 November 2026:
// trader, quant, structurer; Risk and Admin stay spaces of the real app). Each step opens a screen and says what to
// look at; `{name}` in a path is a reference the recorder wrote (an id it made: a quote, a run, a trade).
import type { Profile } from "./transport";

export type Step = { title: string; body: string; to: string };

export type Story = {
  profile: Profile;
  label: string;
  /** one line: who this is and what they do on the desk */
  pitch: string;
  steps: Step[];
};

export const STORIES: Story[] = [
  {
    profile: "structurer",
    label: "Structurer",
    pitch: "Builds a product from a preset, solves its coupon, explains its price and hands it to the trader.",
    steps: [
      {
        title: "Start from a preset",
        body: "An autocall on the index, every field from the server's schema. Change nothing yet: press Quote.",
        to: "/structuring/builder?preset={preset}",
      },
      {
        title: "The coupon, solved",
        body: "The coupon that prices the note at par, with its standard error, then the desk's lines that take the base price to the client's: provisions, bumps, digitals.",
        to: "/structuring/quotes/{quote}",
      },
      {
        title: "Its model risk",
        body: "The base model the policy chose for this class of product, and the provision for what the others say.",
        to: "/quant/calibration?quote={quote}",
      },
      {
        title: "Booked by the trader",
        body: "The trader books it from the quote; the trade keeps its quote and the reserve the desk's lines froze.",
        to: "/trading/trades/{trade}",
      },
      {
        title: "Variants side by side",
        body: "Quotes compared: what differs in their sheets, and what it does to the price.",
        to: "/structuring/quotes",
      },
    ],
  },
  {
    profile: "trader",
    label: "Trader",
    pitch:
      "Runs the book: its Greeks down to a node of the surface, its P&L explained day by day, the VaR and its day.",
    steps: [
      {
        title: "The book at a glance",
        body: "Its value, the main Greeks and the events of the next weeks, each a link.",
        to: "/trading",
      },
      {
        title: "The book's Greeks",
        body: "Every Greek of the book with its standard error; a figure inside its noise is greyed.",
        to: "/trading/risk?run={greeks}",
      },
      {
        title: "Down to a node",
        body: "Vega by strike and tenor. The node is picked: the trades that carry it are listed, and the surface is one click away.",
        to: "/trading/risk?run={greeks}&view=vega&name={name}&node={node}",
      },
      {
        title: "The node on its surface",
        body: "The same point on its smile, in the market explorer, with where every part of the market comes from.",
        to: "/quant/market/{name}?strike={strike}&tenor={tenor}",
      },
      {
        title: "The P&L, explained",
        body: "Three weeks of end-of-day runs, each day revalued step by step (time, spot, vol, skew, rates…) against what the Greeks predicted. The day that moved the book most is open.",
        to: "/trading/pnl?from={pnl_from}&to={pnl_to}&day={pnl_day}",
      },
      {
        title: "The VaR and its day",
        body: "Historical VaR by full revaluation: its distribution, the VaR day factor by factor, and each trade's share.",
        to: "/risk/var/{var}",
      },
    ],
  },
  {
    profile: "quant",
    label: "Quant",
    pitch: "Fits the models to a chain, reads the model risk, lives a product on past closes, writes a payoff.",
    steps: [
      {
        title: "The market in use",
        body: "Smiles by maturity, the term structure, the forward and the discount curve, and the lake behind them.",
        to: "/quant/market",
      },
      {
        title: "The vol lab",
        body: "A day of option quotes with their bid and ask. Press Fit the models: SSVI, SVI and Heston, each with its error against the quotes and by maturity.",
        to: "/quant/vollab",
      },
      {
        title: "Calibration and model risk",
        body: "The calibration's exotics under Black-Scholes, Heston, local vol and SLV: the spread across models is the model risk.",
        to: "/quant/calibration",
      },
      {
        title: "A product lived on past closes",
        body: "Struck on a past date, valued along the closes that followed: its value, the cash it paid, and its P&L explained by the Greeks.",
        to: "/quant/replay/{replay}",
      },
      {
        title: "A payoff script",
        body: "The language the builder compiles to, checked as it is typed and priced under a model.",
        to: "/quant/script",
      },
      {
        title: "The engine",
        body: "NumPy, the C++ engine on the CPU threads, or CUDA on the GPU: same random numbers, same prices.",
        to: "/quant/engine",
      },
    ],
  },
];

/** A step's path with the recording's references put in. */
export function stepPath(step: Step, refs: Record<string, string>): string {
  return step.to.replace(/\{([a-z_]+)\}/g, (_, k: string) => encodeURIComponent(refs[k] ?? ""));
}

export function storyOf(profile: Profile | null): Story | undefined {
  return STORIES.find((s) => s.profile === profile);
}
