import type { ReactNode } from "react";
import classes from "./Panel.module.css";

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
      <div className={[classes.body, flush ? classes.flush : "", classes.scroll].filter(Boolean).join(" ")}>
        {children}
      </div>
    </section>
  );
}
