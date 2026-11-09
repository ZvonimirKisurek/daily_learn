"use client";

import { animate, useInView, useReducedMotion } from "framer-motion";
import { useEffect, useRef } from "react";
import { usd } from "@/lib/bitcoin";

/**
 * Counts up to `value` when it scrolls into view, and glides from the old value to the new one when the server sends a
 * fresh price after a Refresh. framer-motion's imperative `animate(from, to, { onUpdate })` drives a number, not a DOM
 * style, and we write it straight into the node's textContent. That way no React re-render happens per frame: 60
 * updates a second touch one text node and nothing else.
 *
 * The server-rendered text is already the final value, so the HTML (and screen readers, via aria-label) is correct before
 * hydration and the count-up is pure decoration. With "reduce motion" on, it simply stays at the final value.
 */
// `cents` (not a formatter function): props from a Server Component must be serialisable, and functions are not.
export function AnimatedNumber({ value, cents = false, className }: { value: number; cents?: boolean; className?: string }) {
  const format = (v: number) => usd(v, cents);
  const ref = useRef<HTMLSpanElement>(null);
  const prev = useRef<number | null>(null);
  const inView = useInView(ref, { once: true });
  const reduce = useReducedMotion();

  useEffect(() => {
    const node = ref.current;
    if (!node || !inView) return;
    if (reduce) { node.textContent = format(value); prev.current = value; return; }
    // First reveal: count up from slightly below (a full 0 -> 75,000 spin reads as a slot machine, not a terminal).
    const start = prev.current ?? value * 0.985;
    prev.current = value;
    const controls = animate(start, value, {
      duration: 1.1, ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => { node.textContent = format(v); },
    });
    return () => controls.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `format` is derived from `cents`
  }, [value, inView, reduce, cents]);

  return <span ref={ref} className={className} aria-label={format(value)}>{format(value)}</span>;
}
