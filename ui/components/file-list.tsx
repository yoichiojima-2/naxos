"use client";

import { ReactNode, useEffect, useRef, useState } from "react";

// `open` is frozen at mount so refreshes crossing the threshold don't
// override a toggle the user already made. `onOpen` fires once, the first time
// the list is actually shown, so the caller can fetch its contents then.
export default function FileList({
  count,
  onOpen,
  children,
}: {
  count: number;
  onOpen?: () => void;
  children: ReactNode;
}) {
  const [defaultOpen] = useState(count <= 5);
  const opened = useRef(false);

  function open() {
    if (opened.current) return;
    opened.current = true;
    onOpen?.();
  }

  useEffect(() => {
    if (defaultOpen) open();
  });

  return (
    <details
      className="filelist"
      open={defaultOpen || undefined}
      onToggle={(e) => e.currentTarget.open && open()}
    >
      <summary>
        {count} {count === 1 ? "file" : "files"}
      </summary>
      <div className="table-wrap">{children}</div>
    </details>
  );
}
