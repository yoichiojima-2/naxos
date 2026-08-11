"use client";

import { ReactNode, useState } from "react";

// `open` is frozen at mount so refreshes crossing the threshold don't
// override a toggle the user already made.
export default function FileList({ count, children }: { count: number; children: ReactNode }) {
  const [defaultOpen] = useState(count <= 5);
  return (
    <details className="filelist" open={defaultOpen || undefined}>
      <summary>
        {count} {count === 1 ? "file" : "files"}
      </summary>
      <div className="table-wrap">{children}</div>
    </details>
  );
}
