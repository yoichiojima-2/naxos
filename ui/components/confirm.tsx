"use client";

import { useEffect, useRef, useState } from "react";

type Ask = {
  title: string;
  body?: string;
  action?: string;
  danger?: boolean;
  cancel?: boolean;
  input?: { placeholder?: string; initial?: string };
  resolve: (value: string | null) => void;
};

let enqueue: ((ask: Ask) => void) | null = null;

export function confirmDialog(opts: {
  title: string;
  body?: string;
  action?: string;
  danger?: boolean;
}): Promise<boolean> {
  return new Promise((resolve) => {
    if (!enqueue) return resolve(false);
    enqueue({ ...opts, cancel: true, resolve: (v) => resolve(v !== null) });
  });
}

export function promptDialog(opts: {
  title: string;
  body?: string;
  action?: string;
  placeholder?: string;
  initial?: string;
}): Promise<string | null> {
  return new Promise((resolve) => {
    if (!enqueue) return resolve(null);
    const { placeholder, initial, ...rest } = opts;
    enqueue({ ...rest, cancel: true, input: { placeholder, initial }, resolve });
  });
}

export function alertDialog(title: string, body?: string): Promise<void> {
  return new Promise((resolve) => {
    if (!enqueue) return resolve();
    enqueue({ title, body, action: "OK", resolve: () => resolve() });
  });
}

/** Singleton <dialog> host replacing window.confirm/prompt/alert. Mounted once
 * in the shell; the helpers above resolve when the user decides. */
export default function DialogHost() {
  const [queue, setQueue] = useState<Ask[]>([]);
  const [value, setValue] = useState("");
  const ref = useRef<HTMLDialogElement>(null);
  const ask = queue[0];

  useEffect(() => {
    enqueue = (next: Ask) => setQueue((prev) => [...prev, next]);
    return () => {
      enqueue = null;
    };
  }, []);

  useEffect(() => {
    if (ask && !ref.current?.open) {
      setValue(ask.input?.initial ?? "");
      ref.current?.showModal();
    }
  }, [ask]);

  if (!ask) return null;

  function settle(result: string | null) {
    ref.current?.close();
    ask.resolve(result);
    setQueue((prev) => prev.slice(1));
  }

  return (
    <dialog
      ref={ref}
      className="modal"
      onCancel={(e) => {
        e.preventDefault();
        settle(null);
      }}
    >
      <h3>{ask.title}</h3>
      {ask.body && <p>{ask.body}</p>}
      {ask.input && (
        <input
          autoFocus
          className="mt12"
          placeholder={ask.input.placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
              e.preventDefault();
              settle(value);
            }
          }}
        />
      )}
      <div className="row end">
        {ask.cancel && (
          <button className="ghost" onClick={() => settle(null)}>
            Cancel
          </button>
        )}
        <button
          className={ask.danger ? "danger" : "primary"}
          onClick={() => settle(ask.input ? value : "")}
        >
          {ask.action ?? "Confirm"}
        </button>
      </div>
    </dialog>
  );
}
