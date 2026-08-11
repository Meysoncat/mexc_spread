import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type ReactNode,
} from "react";
import { createChart } from "lightweight-charts";
import type { IChartApi, DeepPartial, ChartOptions } from "lightweight-charts";
import { resolveChartOptions, useAppTheme } from "./chartTheme";

export interface ChartCoreProps {
  children?: ReactNode;
  containerRef?: React.RefObject<HTMLDivElement | null>;
  /** Explicit theme override; defaults to the detected app theme (.dark). */
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
    const { mode: detectedMode } = useAppTheme();
    const themeMode = mode ?? detectedMode;

    useImperativeHandle(
      ref,
      () => ({
        get chart() {
          return chartRef.current;
        },
      }),
      [],
    );

    // Create the chart once, independent of theme so zoom/pan survives a theme
    // toggle. Theme colours come from resolveChartOptions() (reads CSS tokens).
    useEffect(() => {
      const el = containerRef.current;
      if (!el) return;

      const chart = createChart(el, {
        ...resolveChartOptions(),
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
      // Chart is created once; theme/option changes are applied below.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [containerRef]);

    // Re-apply theme on toggle without recreating the chart.
    useEffect(() => {
      chartRef.current?.applyOptions(resolveChartOptions());
    }, [themeMode]);

    // Apply consumer-provided option overrides.
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
        {children}</div>
    );
  },
);
