import { useSetupStream } from "../setups/useSetupStream";

interface BadgeMap {
  [path: string]: { count: number; level: "info" | "warning" | "critical" };
}

/** Бейджи навигации: число активных сетапов у пункта «Сетапы». */
export function useNavBadges(): BadgeMap {
  const { active } = useSetupStream();
  return active.length > 0 ? { "/": { count: active.length, level: "info" } } : {};
}
