"use client";

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
