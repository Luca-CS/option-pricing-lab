import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderInApp } from "../test/render";
import { JobProgress } from "./Flow";
import { originKind } from "./Markers";
import { NodeHeatmap, shortUnit } from "./Risk";
import { Est, Num } from "./Value";

describe("numbers on screen", () => {
  it("carry their sign in text, not in colour alone", () => {
    const { container } = renderInApp(<Num value={-39416.7} signed />);
    expect(container.textContent).toBe("▼−39,417");
    expect(container.querySelector("[aria-hidden='true']")?.textContent).toBe("▼");
  });

  it("show the standard error and say when a value is noise", () => {
    renderInApp(
      <>
        <Est value={{ value: -9948, se: 299 }} signed />
        <Est value={{ value: -367, se: 209 }} signed />
      </>,
    );
    expect(screen.getByText(/9,948/).textContent).toContain("299");
    const noise = screen.getByTitle(/two standard errors/);
    expect(noise.textContent).toContain("367");
    expect(noise.textContent).toContain("(in the noise)");
    // inside its noise a value loses its arrow: no direction to read
    expect(noise.textContent).not.toContain("▼");
  });
});

describe("the node heatmap", () => {
  const cells = [
    [
      { value: -2539, se: 198 },
      { value: 12, se: 70 },
    ],
    [null, { value: 3264, se: 274 }],
  ];

  it("is a table of buttons that say their node, value and error", async () => {
    const select = vi.fn();
    renderInApp(
      <NodeHeatmap strikes={["80%", "90%"]} tenors={["6m", "1y"]} cells={cells} onSelect={select} caption="vega" />,
    );
    const node = screen.getByRole("button", { name: /80% at 6m: −2,539 plus or minus 198/ });
    expect(screen.getByRole("button", { name: /80% at 1y: \+12 .* in the noise/ })).toBeInTheDocument();
    await userEvent.click(node);
    expect(select).toHaveBeenCalledWith({ row: 0, col: 0 });
    // an empty node is not a button
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("marks the selected node", () => {
    renderInApp(
      <NodeHeatmap
        strikes={["80%", "90%"]}
        tenors={["6m", "1y"]}
        cells={cells}
        selected={{ row: 1, col: 1 }}
        caption="vega"
      />,
    );
    expect(screen.getByRole("button", { pressed: true })).toHaveTextContent("+3,264");
  });
});

describe("markers and progress", () => {
  it("read the API's origins", () => {
    expect(originKind("lake:reconstructed")).toBe("reconstructed");
    expect(originKind("observed")).toBe("observed");
    expect(originKind("derived")).toBe("derived");
    expect(originKind("flat")).toBe("typed");
    expect(originKind("simulated")).toBe("synthetic");
    expect(originKind(null)).toBe("synthetic");
  });

  it("keep the core of a unit", () => {
    expect(shortUnit("currency per +1 % spot (sticky moneyness)")).toBe("per +1 % spot");
    expect(shortUnit("currency per +1 bp")).toBe("per +1 bp");
  });

  it("say what a job is doing and offer to cancel it while it runs", async () => {
    const cancel = vi.fn();
    const { rerender } = renderInApp(
      <JobProgress
        job={{ state: "running", progress: 0.42, step: "quote of a Phoenix", eta: 2.6 }}
        onCancel={cancel}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("42 % · quote of a Phoenix · 2.6 s left");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(cancel).toHaveBeenCalled();
    rerender(<JobProgress job={{ state: "done" }} onCancel={cancel} />);
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });
});
