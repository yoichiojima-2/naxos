"use client";

import { useState } from "react";
import { favKey, FavoriteType } from "@/lib/api";
import { StarIcon } from "@/components/icons";

export type FavoriteProps = {
  favorites: Set<string>;
  onToggleFavorite: (type: FavoriteType, id: string) => void;
};

export default function FavoriteStar({
  type,
  id,
  favorites,
  onToggleFavorite,
}: { type: FavoriteType; id: string } & FavoriteProps) {
  const on = favorites.has(favKey(type, id));
  return (
    <button
      className={`icon-btn star ${on ? "on" : ""}`}
      onClick={() => onToggleFavorite(type, id)}
      aria-label={on ? "remove favorite" : "add favorite"}
    >
      <StarIcon filled={on} />
    </button>
  );
}

/** Favorites-first ordering plus the "favorites N" chip every list view shares. */
export function useFavoriteFilter<T extends { id: string }>(
  type: FavoriteType,
  items: T[],
  favorites: Set<string>,
) {
  const [favOnly, setFavOnly] = useState(false);
  const isFav = (item: T) => favorites.has(favKey(type, item.id));
  const count = items.filter(isFav).length;

  const apply = (list: T[]) =>
    list.filter((item) => !favOnly || isFav(item)).sort((a, b) => Number(isFav(b)) - Number(isFav(a)));

  const chip = count > 0 || favOnly ? (
    <button className={`chip ${favOnly ? "on" : ""}`} onClick={() => setFavOnly(!favOnly)}>
      favorites {count}
    </button>
  ) : null;

  return { apply, chip, favOnly, clear: () => setFavOnly(false) };
}
