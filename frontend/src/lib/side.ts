// The desk's side of a trade and its size, as the screens show them. The API takes one signed quantity (> 0 when the
// book holds the investor's side, its own convention): a sold product is booked with −size, a bought one with +size.

export type DeskSide = "sold" | "bought";

/** The signed quantity the API takes for a side and a size (the size's sign is ignored). */
export function signedQuantity(side: DeskSide, size: number): number {
  const s = Math.abs(size);
  return side === "sold" ? -s : s;
}

/** The side and size of a signed quantity: what a form opens with on an existing trade. */
export function sideOf(quantity: number): { side: DeskSide; size: number } {
  return { side: quantity < 0 ? "sold" : "bought", size: Math.abs(quantity) };
}

/** The side a sheet was priced for (its `costs.side`, sell by default) as the toggle's default. */
export function pricedSide(sheet: Record<string, unknown>): DeskSide {
  const costs = sheet.costs as Record<string, unknown> | undefined;
  return costs?.side === "buy" ? "bought" : "sold";
}
