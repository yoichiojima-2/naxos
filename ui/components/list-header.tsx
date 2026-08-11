import { ReactNode } from "react";

export default function CountHeader({
  count,
  of,
  noun,
  children,
}: {
  count: number | null;
  of?: number;
  noun: string;
  children?: ReactNode;
}) {
  const label =
    count === null
      ? "…"
      : of !== undefined && of !== count
        ? `${count} of ${of} ${noun}${of === 1 ? "" : "s"}`
        : `${count} ${noun}${count === 1 ? "" : "s"}`;
  return (
    <div className="row between mb12">
      <span className="muted">{label}</span>
      {children && <div className="row">{children}</div>}
    </div>
  );
}
