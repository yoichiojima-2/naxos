"use client";

import { BackIcon } from "@/components/icons";

export const PATH_PATTERN = /^[a-zA-Z0-9._/-]{1,200}$/;

export default function FileEditor({
  path,
  onPathChange,
  pathInputProps,
  pathExtra,
  content,
  onContentChange,
  onBack,
  onSave,
  saveDisabled,
  saveLabel,
  children,
}: {
  path: string;
  onPathChange?: (path: string) => void;
  pathInputProps?: React.InputHTMLAttributes<HTMLInputElement>;
  pathExtra?: React.ReactNode;
  content: string;
  onContentChange: (content: string) => void;
  onBack: () => void;
  onSave: () => void;
  saveDisabled: boolean;
  saveLabel: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="panel">
      <div className="row between mb12">
        <div className="row">
          <button
            className="ghost flex-inline"
            onClick={onBack}
            aria-label="back"
          >
            <BackIcon />
          </button>
          {onPathChange ? (
            <input
              className="mono"
              value={path}
              onChange={(e) => onPathChange(e.target.value)}
              {...pathInputProps}
            />
          ) : (
            <span className="mono">{path}</span>
          )}
          {pathExtra}
        </div>
        <button className="primary" onClick={onSave} disabled={saveDisabled}>
          {saveLabel}
        </button>
      </div>
      {children}
      <textarea
        style={{ minHeight: 360 }}
        value={content}
        onChange={(e) => onContentChange(e.target.value)}
      />
    </div>
  );
}
