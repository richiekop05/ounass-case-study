import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";

// Demo chrome: who we are shopping as and their intent, the signals behind each persona, and a reset.
// Sticky, dark, monospace. No age appears anywhere (Design PRD 4).
export function PersonaBar() {
  const { personas, persona, state, switchPersona, setBtsOpen } = useStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const intent = state?.intent;

  return (
    <div className="persona-bar">
      <div className="persona-bar__inner">
        <div className="persona-bar__who" ref={ref}>
          <button className="persona-bar__toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <span className="wide-only persona-bar__muted">Shopping as: </span>
            <strong>{persona?.name ?? "…"}</strong>
            {intent && (
              <>
                <span className="persona-bar__muted"> · </span>
                <span className={"persona-bar__intent is-" + intent.state}>
                  {intent.state === "high" ? "High intent" : "Low intent"}
                </span>
              </>
            )}{" "}
            <span className="persona-bar__muted">▾</span>
          </button>
          {open && (
            <div className="persona-menu">
            <ul role="menu">
              {personas.map((p) => (
                <li key={p.id}>
                  <button
                    role="menuitem"
                    className={"persona-menu__item" + (p.id === persona?.id ? " is-current" : "")}
                    onClick={() => {
                      setOpen(false);
                      switchPersona(p.id);
                    }}
                  >
                    <span className="persona-menu__head">
                      <span className="persona-menu__name">{p.fullName}</span>
                      <span className={"persona-menu__badge is-" + p.startingIntent}>
                        {p.startingIntent === "high" ? "High intent" : "Low intent"}
                      </span>
                    </span>
                    <span className="persona-menu__signals">
                      {p.signals.map((s) => (
                        <span key={s}>– {s}</span>
                      ))}
                    </span>
                    <span className="persona-menu__does">→ {p.does}</span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="persona-menu__caption">Simulated starting sessions</p>
            </div>
          )}
        </div>
        <div className="persona-bar__right">
          {intent?.pending === "high" && intent.state !== "high" && (
            <span className="persona-bar__pending">High intent from next page</span>
          )}
          <button className="persona-bar__reset" onClick={() => persona && switchPersona(persona.id)}>
            Reset<span className="wide-only"> session</span>
          </button>
          <button className="persona-bar__bts" onClick={() => setBtsOpen(true)} aria-label="Behind the scenes">
            BTS
          </button>
        </div>
      </div>
    </div>
  );
}
