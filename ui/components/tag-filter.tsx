"use client";

import { useState } from "react";

export const TAG_PATTERN = /^[a-z0-9][a-z0-9-]{0,31}$/;

export const parseTags = (value: string) =>
  [...new Set(value.toLowerCase().split(/[\s,]+/).filter(Boolean))];

/** Single-select tag filter plus the "#tag" chip row list views share. */
export function useTagFilter<T extends { tags: string[] }>(items: T[]) {
  const [selected, setSelected] = useState<string | null>(null);
  const all = [...new Set(items.flatMap((item) => item.tags))].sort();
  const active = selected !== null && all.includes(selected) ? selected : null;

  const apply = (list: T[]) =>
    active === null ? list : list.filter((item) => item.tags.includes(active));

  const chips = all.map((tag) => (
    <button
      key={tag}
      type="button"
      className={`chip ${active === tag ? "on" : ""}`}
      onClick={() => setSelected(active === tag ? null : tag)}
    >
      #{tag}
    </button>
  ));

  return { apply, chips, active };
}
