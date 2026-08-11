export default function TableStates({
  items,
  filtered,
  colSpan,
  empty,
  noMatch,
}: {
  items: unknown[] | null;
  filtered: unknown[];
  colSpan: number;
  empty: string;
  noMatch: string;
}) {
  if (items === null) return <tr><td className="empty" colSpan={colSpan}>loading…</td></tr>;
  if (items.length === 0) return <tr><td className="empty" colSpan={colSpan}>{empty}</td></tr>;
  if (filtered.length === 0) return <tr><td className="empty" colSpan={colSpan}>{noMatch}</td></tr>;
  return null;
}
