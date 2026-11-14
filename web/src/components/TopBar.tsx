import type { ReactNode } from "react";
import "./TopBar.css";

// Top bar (Design PRD 4): 56px, page title on the left, page-level controls on the right.
export function TopBar({ left, right }: { left: ReactNode; right?: ReactNode }) {
  return (
    <header className="topbar">
      <div className="topbar-left">{left}</div>
      {right && <div className="topbar-right">{right}</div>}
    </header>
  );
}
