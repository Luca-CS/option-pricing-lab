// The sheet's form, drawn from what the server says (POST /structuring/termsheet/form): its sections, the fields it
// shows for these values (`visible_if` is the server's), their units, read-only fields, and each field's error under
// it in the unit shown (HANDOFF_REFACTOR_2_UX.md §5). The form holds no rule of its own.
import { Select, Switch, TextInput } from "@mantine/core";
import { useEffect, useState } from "react";
import type { Schemas } from "../../api/client";
import { choiceLabel, display, type FormField, parse, suffix, type Values } from "../../lib/sheet";
import classes from "./SheetForm.module.css";

type Form = Schemas["SheetForm"];

/** A text input that keeps what is typed until it leaves the field, then hands the parsed value up. */
function TextField({
  field,
  value,
  error,
  onChange,
}: {
  field: FormField;
  value: unknown;
  error?: string | undefined;
  onChange: (v: unknown) => void;
}) {
  const shown = display(field, value);
  const [text, setText] = useState(shown);
  useEffect(() => setText(shown), [shown]);
  const unit = suffix(field);
  return (
    <TextInput
      label={field.label}
      value={text}
      type={field.kind === "date" ? "date" : "text"}
      inputMode={field.kind === "number" || field.kind === "int" ? "decimal" : undefined}
      onChange={(e) => setText(e.currentTarget.value)}
      onBlur={() => {
        if (text !== shown) onChange(parse(field, text));
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" && text !== shown) onChange(parse(field, text));
      }}
      rightSection={unit ? <span className={classes.unit}>{unit}</span> : undefined}
      rightSectionWidth={unit ? Math.max(36, unit.length * 8 + 14) : undefined}
      error={error}
      disabled={field.read_only}
      placeholder={field.optional ? "from the cost table" : undefined}
      classNames={{ input: field.kind === "number" || field.kind === "int" ? classes.numeric : undefined }}
    />
  );
}

export function SheetForm({
  form,
  values,
  errors,
  onChange,
  hidden = [],
}: {
  form: Form;
  values: Values;
  errors: Record<string, string>;
  onChange: (section: string, field: string, value: unknown) => void;
  /** sections drawn elsewhere (the costs, under "Advanced") */
  hidden?: string[];
}) {
  return (
    <div className={classes.form}>
      {form.sections
        .filter((s) => !hidden.includes(s.name))
        .map((section) => (
          <fieldset key={section.name} className={classes.section}>
            <legend className={classes.legend}>{section.label}</legend>
            <div className={classes.fields}>
              {section.fields.map((field) => {
                const value = values[section.name]?.[field.name] ?? field.value;
                const error = errors[field.path];
                const set = (v: unknown) => onChange(section.name, field.name, v);
                if (field.kind === "choice") {
                  return (
                    <Select
                      key={field.path}
                      label={field.label}
                      data={(field.choices ?? []).map((c) => ({ value: String(c), label: choiceLabel(c) }))}
                      value={value === null || value === undefined ? null : String(value)}
                      onChange={(v) => set(v)}
                      allowDeselect={false}
                      disabled={field.read_only}
                      error={error}
                      comboboxProps={{ withinPortal: true }}
                    />
                  );
                }
                if (field.kind === "bool") {
                  return (
                    <Switch
                      key={field.path}
                      label={field.label}
                      checked={Boolean(value)}
                      onChange={(e) => set(e.currentTarget.checked)}
                      disabled={field.read_only}
                      error={error}
                      className={classes.switch}
                    />
                  );
                }
                return <TextField key={field.path} field={field} value={value} error={error} onChange={set} />;
              })}
            </div>
          </fieldset>
        ))}
    </div>
  );
}
