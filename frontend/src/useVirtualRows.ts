import {
  type RefObject,
  useCallback,
  useEffect,
  useState,
} from "react";

/** Рендер только видимых строк + отступы — без лишних DOM-узлов при тысячах пар. */
export function useVirtualRows(
  scrollRef: RefObject<HTMLElement | null>,
  itemCount: number,
  rowHeight: number,
  overscan: number,
) {
  const [range, setRange] = useState({ start: 0, end: 0 });

  const recalc = useCallback(() => {
    const el = scrollRef.current;
    if (!el || itemCount === 0) {
      setRange((prev) =>
        prev.start === 0 && prev.end === 0 ? prev : { start: 0, end: 0 },
      );
      return;
    }
    const top = el.scrollTop;
    const h = el.clientHeight || 1;
    const start = Math.max(0, Math.floor(top / rowHeight) - overscan);
    const end = Math.min(
      itemCount,
      Math.ceil((top + h) / rowHeight) + overscan,
    );
    setRange((prev) =>
      prev.start === start && prev.end === end ? prev : { start, end },
    );
  }, [itemCount, rowHeight, overscan, scrollRef]);

  useEffect(() => {
    const el = scrollRef.current;
    recalc();
    if (!el) return;
    el.addEventListener("scroll", recalc, { passive: true });
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => recalc())
        : null;
    ro?.observe(el);
    return () => {
      el.removeEventListener("scroll", recalc);
      ro?.disconnect();
    };
  }, [recalc, scrollRef]);

  useEffect(() => {
    recalc();
  }, [itemCount, recalc]);

  const topPad = range.start * rowHeight;
  const bottomPad = Math.max(0, (itemCount - range.end) * rowHeight);

  return {
    start: range.start,
    end: range.end,
    topPad,
    bottomPad,
    rowHeight,
  };
}
