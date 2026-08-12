import { useLayoutEffect, useRef, useState } from "react";

export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export function niceScale(max: number): { top: number; ticks: number[] } {
  if (max <= 0) return { top: 1, ticks: [0, 0.5, 1] };
  const step = Math.pow(10, Math.floor(Math.log10(max)));
  const mult = max / step;
  const top = (mult <= 1 ? 1 : mult <= 2 ? 2 : mult <= 5 ? 5 : 10) * step;
  return { top, ticks: [0, top / 2, top] };
}

// Seconds, minutes, hours and days — a duration axis that stops at 41m 40s reads
// as noise, so the top of the scale snaps to a step people actually name.
const TIME_STEPS = [
  1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400,
];

export function durationScale(maxSeconds: number): { top: number; ticks: number[] } {
  if (maxSeconds <= 0) return { top: 60, ticks: [0, 30, 60] };
  const top =
    TIME_STEPS.find((step) => step >= maxSeconds) ?? Math.ceil(maxSeconds / 86_400) * 86_400;
  return { top, ticks: [0, top / 2, top] };
}

/** A bar with rounded ends at the value side, anchored to the baseline. */
export function barPath(x: number, width: number, top: number, bottom: number): string {
  const h = bottom - top;
  if (h <= 0) return "";
  const r = Math.min(4, width / 2, h);
  return (
    `M ${x} ${bottom} L ${x} ${top + r} Q ${x} ${top} ${x + r} ${top} ` +
    `L ${x + width - r} ${top} Q ${x + width} ${top} ${x + width} ${top + r} ` +
    `L ${x + width} ${bottom} Z`
  );
}
