import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import "./ConfirmDialog.css";

// Confirm dialog (handoff README "App shell"): 420px, white, title in lg, the body in muted text,
// a secondary Cancel and a primary OK. `confirm({...})` resolves true on OK, false on Cancel,
// Escape or a click outside.

export type ConfirmOptions = { title: string; body?: ReactNode; ok: string; cancel?: string };
type Open = ConfirmOptions & { resolve: (ok: boolean) => void };

const ConfirmContext = createContext<(c: ConfirmOptions) => Promise<boolean>>(() => Promise.resolve(false));

export function useConfirm() {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<Open | null>(null);

  const confirm = useCallback(
    (c: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setOpen((prev) => {
          prev?.resolve(false);
          return { ...c, resolve };
        });
      }),
    [],
  );

  const close = useCallback(
    (ok: boolean) => {
      open?.resolve(ok);
      setOpen(null);
    },
    [open],
  );

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {open && <ConfirmDialog options={open} onClose={close} />}
    </ConfirmContext.Provider>
  );
}

export function ConfirmDialog({ options, onClose }: { options: ConfirmOptions; onClose: (ok: boolean) => void }) {
  const okRef = useRef<HTMLButtonElement | null>(null);
  const returnTo = useRef<Element | null>(null);

  useEffect(() => {
    returnTo.current = document.activeElement;
    okRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (returnTo.current instanceof HTMLElement) returnTo.current.focus();
    };
  }, [onClose]);

  return (
    <div className="confirm-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose(false)}>
      <div className="confirm" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <h2 id="confirm-title" className="confirm-title">
          {options.title}
        </h2>
        {options.body && <div className="confirm-body">{options.body}</div>}
        <div className="confirm-buttons">
          <button type="button" className="btn btn-secondary" onClick={() => onClose(false)}>
            {options.cancel ?? "Cancel"}
          </button>
          <button ref={okRef} type="button" className="btn btn-primary" onClick={() => onClose(true)}>
            {options.ok}
          </button>
        </div>
      </div>
    </div>
  );
}
