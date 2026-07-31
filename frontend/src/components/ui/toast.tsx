"use client";

import * as React from "react";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";

type ToastType = "success" | "error" | "info";

interface ToastOptions {
  message: string;
  type?: ToastType;
  /** Auto-dismiss delay in ms. */
  duration?: number;
}

interface ToastRecord extends Required<Omit<ToastOptions, "duration">> {
  id: number;
}

type ToastFn = (options: ToastOptions) => void;

const ToastContext = React.createContext<ToastFn | null>(null);

const DEFAULT_DURATION = 4000;

// Opaque backgrounds with a dark text colour in light mode (and a light one in dark mode).
// The earlier translucent `bg-*/10` + pale text washed out over the app's near-white
// background; these pairings clear WCAG AA at normal text sizes in both themes.
const TOAST_STYLES: Record<ToastType, { className: string; iconClassName: string; Icon: typeof Info }> = {
  success: {
    className:
      "border-emerald-600/30 bg-emerald-50 text-emerald-900 dark:border-emerald-400/30 dark:bg-emerald-950 dark:text-emerald-100",
    iconClassName: "text-emerald-700 dark:text-emerald-400",
    Icon: CheckCircle2,
  },
  error: {
    className:
      "border-red-600/30 bg-red-50 text-red-900 dark:border-red-400/30 dark:bg-red-950 dark:text-red-100",
    iconClassName: "text-red-700 dark:text-red-400",
    Icon: XCircle,
  },
  info: {
    className:
      "border-slate-300 bg-white text-slate-900 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100",
    iconClassName: "text-primary",
    Icon: Info,
  },
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<ToastRecord[]>([]);
  const timers = React.useRef<number[]>([]);
  const nextId = React.useRef(0);

  const dismiss = React.useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const toast = React.useCallback<ToastFn>(
    ({ message, type = "info", duration = DEFAULT_DURATION }) => {
      const id = nextId.current++;
      setToasts((current) => [...current, { id, message, type }]);
      const timer = window.setTimeout(() => dismiss(id), duration);
      timers.current.push(timer);
    },
    [dismiss]
  );

  // Unmounting with timers still pending would fire setState on a dead component.
  React.useEffect(
    () => () => {
      timers.current.forEach(window.clearTimeout);
      timers.current = [];
    },
    []
  );

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div
        className="pointer-events-none fixed top-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2"
        role="region"
        aria-label="Notifications"
      >
        {toasts.map(({ id, message, type }) => {
          const { className, iconClassName, Icon } = TOAST_STYLES[type];
          return (
            <div
              key={id}
              role="status"
              aria-live="polite"
              className={cn(
                "pointer-events-auto flex items-start gap-3 rounded-lg border p-3 text-sm font-medium shadow-lg",
                className
              )}
            >
              <Icon className={cn("mt-0.5 size-4 shrink-0", iconClassName)} />
              <p className="flex-1 break-words">{message}</p>
              <button
                type="button"
                onClick={() => dismiss(id)}
                className="shrink-0 rounded p-0.5 opacity-60 transition-opacity hover:opacity-100"
                aria-label="Dismiss notification"
              >
                <X className="size-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastFn {
  const context = React.useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
}
