import { ReactNode } from "react";

export default function CountHeader({
  count,
  noun,
  children,
}: {
  count: number | null;
  noun: string;
  children?: ReactNode;
}) {
  return (
    <div className="row between mb12">
      <span className="muted">
        {count === null ? "…" : `${count} ${noun}${count === 1 ? "" : "s"}`}
      </span>
      {children && <div className="row">{children}</div>}
    </div>
  );
}
