import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type ReactNode,
} from "react";
import { ColorType, createChart } from "lightweight-charts";
import type { IChartApi, DeepPartial, ChartOptions } from "lightweight-charts";

export interface ChartCoreProps {
  children?: ReactNode;
  containerRef?: React.RefObject<HTMLDivElement | null>;
  mode?: "dark" | "light";
  onChartReady?: (chart: IChartApi) => void;
  className?: string;
  options?: DeepPartial<ChartOptions>;
}

export interface ChartCoreRef {
  chart: IChartApi | null;
}

export const ChartCore = forwardRef<ChartCoreRef, ChartCoreProps>(
  function ChartCore(
    {
      children,
      containerRef: externalContainerRef,
      mode,
      onChartReady,
      className,
      options,
    },
    ref,
  ) {
    const internalContainerRef = useRef<HTMLDivElement | null>(null);
    const chartRef = useRef<IChartApi | null>(null);
    const onChartReadyRef = useRef(onChartReady);
    onChartReadyRef.current = onChartReady;

    const containerRef = externalContainerRef ?? internalContainerRef;

    useImperativeHandle(
      ref,
      () => ({
        get chart() {
          return chartRef.current;
        },
      }),
      [],
    );

    useEffect(() => {
      const el = containerRef.current;
      if (!el) return;

      let resolvedMode = mode;
      if (!resolvedMode) {
        resolvedMode = el.closest(".dark") ? "dark" : "light";
      }

      const isDark = resolvedMode === "dark";
      const bg = isDark ? "#1e293b" : "#ffffff";
      const fg = isDark ? "#e2e8f0" : "#0f172a";
      const grid = isDark ? "#334155" : "#e2e8f0";

      const chart = createChart(el, {
        layout: {
          background: { type: ColorType.Solid, color: bg },
          textColor: fg,
        },
        grid: {
          vertLines: { color: grid },
          horzLines: { color: grid },
        },
        rightPriceScale: {
          borderColor: grid,
          autoScale: true,
          scaleMargins: { top: 0.12, bottom: 0.12 },
          entireTextOnly: false,
        },
        timeScale: {
          borderColor: grid,
          timeVisible: true,
          secondsVisible: false,
        },
        crosshair: {
          mode: 0,
        },
        width: el.clientWidth,
        height: el.clientHeight,
        ...options,
      });

      chartRef.current = chart;

      const ro = new ResizeObserver(() => {
        if (!containerRef.current) return;
        chart.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      });
      ro.observe(el);

      onChartReadyRef.current?.(chart);

      return () => {
        ro.disconnect();
        chartRef.current = null;
        chart.remove();
      };
    }, [mode, containerRef]);

    useEffect(() => {
      if (chartRef.current && options) {
        chartRef.current.applyOptions(options);
      }
    }, [options]);

    if (externalContainerRef) {
      return <>{children}</>;
    }

    return (
      <div
        ref={internalContainerRef}
        className={className ?? "h-full w-full min-h-[280px]"}
      >
        {children}
      </div>
    );
  },
);
