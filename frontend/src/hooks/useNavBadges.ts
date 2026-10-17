interface BadgeMap {
  [path: string]: { count: number; level: "info" | "warning" | "critical" };
}

/** Бейджи навигации. Торговых движков больше нет — бейджей пока нет. */
export function useNavBadges(): BadgeMap {
  return {};
}
