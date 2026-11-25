import { describe, expect, it } from "vitest";
import { renderInApp } from "../test/render";
import { Grid, type GridColumn } from "./Grid";

type Row = { name: string; group: string };

const settle = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("the data grid", () => {
  it("renders once its rows are drawn, then rests (it once looped for ever, holding the URL's filters back)", async () => {
    let drawn = 0;
    const columns: GridColumn<Row>[] = [
      {
        id: "name",
        accessorKey: "name",
        header: "Name",
        cell: (c) => {
          drawn += 1;
          return String(c.getValue());
        },
      },
      { id: "group", accessorKey: "group", header: "Group" },
    ];
    const data = [
      { name: "a", group: "x" },
      { name: "b", group: "y" },
    ];
    renderInApp(<Grid data={data} columns={columns} groupBy="group" caption="rows" rowKey={(r) => r.name} />);
    await settle(150);
    const rested = drawn;
    expect(rested).toBeGreaterThan(0);
    await settle(300);
    expect(drawn).toBe(rested);
  });
});
