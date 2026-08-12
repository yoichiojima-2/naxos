"use client";

import { useEffect, useRef, useState } from "react";

const draftStore = (key: string) => `naxos.draft.${key}`;

function SendIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 19V5M5 12l7-7 7 7" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <rect x="5" y="5" width="14" height="14" rx="2.5" />
    </svg>
  );
}

/** The rounded chat composer. Enter sends, Shift+Enter breaks a line, drafts
 * survive navigation, and the send button becomes Stop while the agent works
 * and nothing is typed. */
export default function Composer({
  placeholder,
  hint,
  disabled,
  working,
  draftKey,
  autoFocus,
  left,
  onSend,
  onStop,
}: {
  placeholder: string;
  hint?: string;
  disabled?: boolean;
  working?: boolean;
  draftKey?: string;
  autoFocus?: boolean;
  left?: React.ReactNode;
  onSend: (text: string) => Promise<void>;
  onStop?: () => void;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!draftKey) return;
    try {
      const draft = localStorage.getItem(draftStore(draftKey));
      if (draft) setText(draft);
    } catch {
      /* storage unavailable */
    }
  }, [draftKey]);

  function update(next: string) {
    setText(next);
    if (draftKey) {
      try {
        if (next) localStorage.setItem(draftStore(draftKey), next);
        else localStorage.removeItem(draftStore(draftKey));
      } catch {
        /* storage unavailable */
      }
    }
    const area = areaRef.current;
    if (area) {
      area.style.height = "auto";
      area.style.height = `${area.scrollHeight + 2}px`;
    }
  }

  async function send() {
    const body = text.trim();
    if (!body || sending || disabled) return;
    setSending(true);
    try {
      await onSend(body);
      update("");
      if (areaRef.current) areaRef.current.style.height = "auto";
    } finally {
      setSending(false);
    }
  }

  const showStop = !!working && !text.trim() && !!onStop;

  return (
    <div className="chat-composer">
      <textarea
        ref={areaRef}
        rows={1}
        value={text}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus={autoFocus}
        onChange={(e) => update(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        aria-label={placeholder}
      />
      <div className="composer-row">
        {left}
        <span className="hint">{hint ?? "Enter to send · Shift+Enter for a new line"}</span>
        {showStop ? (
          <button className="send-btn stop" onClick={onStop} aria-label="stop the agent" title="Stop">
            <StopIcon />
          </button>
        ) : (
          <button
            className="send-btn"
            onClick={send}
            disabled={disabled || sending || !text.trim()}
            aria-label="send message"
            title="Send"
          >
            <SendIcon />
          </button>
        )}
      </div>
    </div>
  );
}
