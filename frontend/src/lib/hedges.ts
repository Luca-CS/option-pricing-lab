// A hedging ticket's values as the API takes them: each field read by the kind the server's description gives it
// (GET /trading/hedges/types), the empty ones left out so the server applies its defaults. No rule of an instrument
// lives here: what a type needs, its units and its defaults come from the server.

export type FieldSpec = {
  name: string;
  kind: string;
  required: boolean;
  default?: unknown;
};

export type Values = Record<string, string | number | boolean | null | undefined>;

/** The instrument's body: `{type, field: value}`, numbers as numbers, dates as ISO strings, blanks omitted. */
export function instrumentBody(type: string, fields: FieldSpec[], values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = { type };
  for (const f of fields) {
    const v = values[f.name];
    if (v === undefined || v === null || v === "") continue;
    if (f.kind === "number" || f.kind === "int") {
      const x = typeof v === "number" ? v : Number(String(v).replace(/,/g, ""));
      if (Number.isFinite(x)) out[f.name] = f.kind === "int" ? Math.round(x) : x;
    } else if (f.kind === "bool") {
      out[f.name] = Boolean(v);
    } else {
      out[f.name] = String(v);
    }
  }
  return out;
}

/** The fields a ticket still lacks before it can be marked or booked. */
export function missing(fields: FieldSpec[], values: Values): string[] {
  return fields
    .filter((f) => f.required && (values[f.name] === undefined || values[f.name] === null || values[f.name] === ""))
    .map((f) => f.name);
}

/** A ticket's first values: each field's default from the description. */
export function initialValues(fields: FieldSpec[]): Values {
  const out: Values = {};
  for (const f of fields) {
    if (f.default !== undefined && f.default !== null) out[f.name] = f.default as Values[string];
  }
  return out;
}
