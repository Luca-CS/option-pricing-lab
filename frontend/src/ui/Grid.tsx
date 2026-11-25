// The desk's data grid (TanStack Table, styled with the tokens): sortable columns, grouping by a column with the
// group's rows folded under a header, numbers right-aligned in monospace, a frame that scrolls (never the page).

import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import {
  type ColumnDef,
  type ExpandedState,
  flexRender,
  type GroupingState,
  getCoreRowModel,
  getExpandedRowModel,
  getGroupedRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useMemo, useState } from "react";
import classes from "./Grid.module.css";

export type GridColumn<T> = ColumnDef<T, unknown> & { meta?: { numeric?: boolean } };

export function Grid<T>({
  data,
  columns,
  groupBy,
  caption,
  rowKey,
  initialSort = [],
  footer,
}: {
  data: T[];
  columns: GridColumn<T>[];
  /** the id of the column to group the rows by, or none */
  groupBy?: string | null;
  caption: string;
  rowKey: (row: T) => string;
  initialSort?: SortingState;
  footer?: React.ReactNode;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  const [expanded, setExpanded] = useState<ExpandedState>(true);
  // The table's state must keep its identity between renders: TanStack recomputes its row models when a piece of state
  // is a new object, and each recomputation queues a reset of the page index, a new state object, hence a render. A
  // grouping built afresh on each render made that a loop that never ended: the page kept rendering, its CPU busy and
  // the router's transitions (a filter in the URL) never committed.
  const grouping: GroupingState = useMemo(() => (groupBy ? [groupBy] : []), [groupBy]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting, grouping, expanded },
    onSortingChange: setSorting,
    onExpandedChange: setExpanded,
    getRowId: (row, i, parent) => (parent ? `${parent.id}.${rowKey(row)}` : rowKey(row) || String(i)),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    autoResetExpanded: false,
    autoResetPageIndex: false, // the grid has no pages: nothing to reset when its rows change
  });
  const width = table.getVisibleLeafColumns().length;
  return (
    <div className={classes.frame}>
      <table className={classes.grid}>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const numeric = (h.column.columnDef as GridColumn<T>).meta?.numeric;
                const sorted = h.column.getIsSorted();
                return (
                  <th
                    key={h.id}
                    scope="col"
                    className={numeric ? classes.num : undefined}
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                  >
                    {h.isPlaceholder ? null : h.column.getCanSort() ? (
                      <button type="button" className={classes.sort} onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        <span aria-hidden="true" className={classes.arrow}>
                          {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}
                        </span>
                      </button>
                    ) : (
                      flexRender(h.column.columnDef.header, h.getContext())
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            if (row.getIsGrouped()) {
              const open = row.getIsExpanded();
              return (
                <tr key={row.id} className={classes.group}>
                  <td colSpan={width}>
                    <button
                      type="button"
                      className={classes.toggle}
                      onClick={row.getToggleExpandedHandler()}
                      aria-expanded={open}
                    >
                      {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                      <span className={classes.groupName}>{String(row.groupingValue ?? "—")}</span>
                      <span className={classes.count}>{row.subRows.length} rows</span>
                    </button>
                  </td>
                </tr>
              );
            }
            return (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => {
                  if (cell.getIsPlaceholder() || cell.getIsAggregated()) return <td key={cell.id} />;
                  const numeric = (cell.column.columnDef as GridColumn<T>).meta?.numeric;
                  return (
                    <td key={cell.id} className={numeric ? classes.num : undefined}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
        {footer && (
          <tfoot>
            <tr>
              <td colSpan={width}>{footer}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
