import type { InputHTMLAttributes, SelectHTMLAttributes } from "react";

/**
 * The two form controls for the whole app.
 *
 * Both resolve to `--control-h` via the shared `.control` class, which is
 * what keeps a filter row on one line. Before this, a select measured
 * 33.8px, an input 31.8px and a button 31.6px, so "Status / Engine /
 * Search" sat on three slightly different baselines -- the kind of 2px
 * disagreement nobody consciously spots and everybody feels.
 *
 * Styling lives in index.css rather than inline so hover, :focus-visible
 * and :disabled can be expressed at all -- a style attribute cannot carry
 * a pseudo-class, which is why the old inline version had no focus ring.
 */
export function Select({ className, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={["control", "control-mono", className].filter(Boolean).join(" ")} {...rest} />;
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={["control", "control-mono", className].filter(Boolean).join(" ")} {...rest} />;
}
