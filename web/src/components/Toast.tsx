import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import "./Toast.css";

// Toast (Design PRD 13, handoff README "App shell"): bottom centre of the content area, dark
// background, white text, underlined action links ("Undo", "View on website"). One at a time: a new
// toast replaces the one showing. 8 seconds for approvals and undoable actions, 4 for "Saved".

export const TOAST_LONG = 8000;
export const TOAST_SHORT = 4000;
const FADE_MS = 150; // matches --motion

export type ToastAction = { label: string; onClick: () => void };
export type ToastOptions = { message: string; actions?: ToastAction[]; duration?: number };
type Shown = ToastOptions & { seq: number; leaving: boolean };

const ToastContext = createContext<(t: ToastOptions) => void>(() => undefined);

/** `toast({message, actions, duration})` from any page. */
export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [shown, setShown] = useState<Shown | null>(null);
  const seq = useRef(0);

  const toast = useCallback((t: ToastOptions) => {
    seq.current += 1;
    setShown({ ...t, seq: seq.current, leaving: false });
  }, []);

  const dismiss = useCallback((which: number) => {
    setShown((s) => (s && s.seq === which ? { ...s, leaving: true } : s));
    window.setTimeout(() => setShown((s) => (s && s.seq === which ? null : s)), FADE_MS);
  }, []);

  useEffect(() => {
    if (!shown || shown.leaving) return;
    const t = window.setTimeout(() => dismiss(shown.seq), shown.duration ?? TOAST_LONG);
    return () => window.clearTimeout(t);
  }, [shown, dismiss]);

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {shown && (
          <div key={shown.seq} className={"toast" + (shown.leaving ? " is-leaving" : "")}>
            <span>{shown.message}</span>
            {shown.actions?.map((a) => (
              <button
                key={a.label}
                type="button"
                className="toast-action"
                onClick={() => {
                  dismiss(shown.seq);
                  a.onClick();
                }}
              >
                {a.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </ToastContext.Provider>
  );
}
