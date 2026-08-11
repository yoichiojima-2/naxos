const UNITS: [number, Intl.RelativeTimeFormatUnit][] = [
  [60, "second"],
  [60, "minute"],
  [24, "hour"],
  [7, "day"],
  [4.35, "week"],
  [12, "month"],
];

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "narrow" });

export function relativeTime(iso: string): string {
  let delta = (new Date(iso).getTime() - Date.now()) / 1000;
  if (!Number.isFinite(delta)) return iso;
  if (Math.abs(delta) < 45) return "just now";
  for (const [step, unit] of UNITS) {
    if (Math.abs(delta) < step) return rtf.format(Math.round(delta), unit);
    delta /= step;
  }
  return rtf.format(Math.round(delta), "year");
}

export function fullTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function shortId(id: string): string {
  return id.length > 16 ? `${id.slice(0, 14)}…` : id;
}
