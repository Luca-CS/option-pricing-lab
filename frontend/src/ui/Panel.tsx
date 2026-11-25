import type { ReactNode } from "react";
import classes from "./Panel.module.css";

/** A table's frame that may scroll sideways in its panel: a named region that takes the keyboard's focus, so it
 * scrolls by keys too (axe: scrollable-region-focusable, WCAG 2.1.1). Biome's rules prefer a <section> and refuse a
 * tabIndex on a non-interactive element; the WAI-ARIA pattern for a scrollable region needs both. */
function ScrollFrame({ label, children }: { label: string; children?: ReactNode }) {
  return (
    // biome-ignore lint/a11y/useSemanticElements: the panel is already the section; this is its scrollable region
    <div
      className={[classes.body, classes.flush, classes.scroll].join(" ")}
      role="region"
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be reachable by keyboard
      tabIndex={0}
      aria-label={label}
    >
      {children}
    </div>
  );
}

/** A section of a screen: a title, an optional hint (units, method) and actions, then its body. `flush` lets a
 * table run to the panel's edges; a wide body scrolls inside the panel, never the page. */
export function Panel({
  title,
  hint,
  actions,
  flush,
  children,
  className,
  labelledBy,
}: {
  title?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  children?: ReactNode;
  className?: string;
  labelledBy?: string;
}) {
  return (
    <section className={[classes.panel, className].filter(Boolean).join(" ")} aria-labelledby={labelledBy}>
      {(title || hint || actions) && (
        <div className={classes.head}>
          {title && (
            <h2 className={classes.title} id={labelledBy}>
              {title}
            </h2>
          )}
          {hint && <span className={classes.hint}>{hint}</span>}
          {actions && <div className={classes.actions}>{actions}</div>}
        </div>
      )}
      {flush ? (
        <ScrollFrame label={typeof title === "string" ? title : "table"}>{children}</ScrollFrame>
      ) : (
        <div className={[classes.body, classes.scroll].join(" ")}>{children}</div>
      )}
    </section>
  );
}
