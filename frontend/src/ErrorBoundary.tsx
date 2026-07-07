import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * Ловит необработанные ошибки рендера (иначе — пустой/«синий» экран без текста).
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[MEXC UI] ErrorBoundary", error.message, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      const e = this.state.error;
      return (
        <div className="min-h-screen bg-slate-100 p-8 text-slate-900 dark:bg-slate-900 dark:text-slate-100">
          <h1 className="text-xl font-semibold text-red-600 dark:text-red-400">
            Ошибка интерфейса
          </h1>
          <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
            Сообщение и стек ниже — их можно скопировать в отчёт. В консоли браузера
            (F12) обычно есть дополнительные логи{" "}
            <code className="rounded bg-slate-200 px-1 dark:bg-slate-800">[MEXC UI]</code>.
          </p>
          <pre className="mt-4 max-h-[60vh] overflow-auto rounded-lg border border-slate-300 bg-white p-4 text-xs dark:border-slate-600 dark:bg-slate-950">
            {e.message}
            {"\n\n"}
            {e.stack ?? ""}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}
