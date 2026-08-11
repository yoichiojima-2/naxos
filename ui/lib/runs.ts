import { RunStatus } from "@/lib/api";

// Run status is a semantic state, not a categorical series: the colours are the
// app's status tokens, and because green/red separate poorly under colour-vision
// deficiency every mark pairs the colour with the glyph and the label below.
export const RUN_STATUS: Record<RunStatus, { kind: string; glyph: string; badge: string }> = {
  succeeded: { kind: "ok", glyph: "✓", badge: "ok" },
  failed: { kind: "fail", glyph: "✕", badge: "terminated" },
  cancelled: { kind: "cancel", glyph: "⊘", badge: "idle" },
  running: { kind: "active", glyph: "●", badge: "running" },
  queued: { kind: "active", glyph: "○", badge: "running" },
};
