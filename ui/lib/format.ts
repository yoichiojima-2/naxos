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

export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    const rest = Math.round(seconds % 60);
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}
