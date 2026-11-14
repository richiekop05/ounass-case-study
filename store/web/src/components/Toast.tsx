import { useEffect, useState } from "react";
import { useStore } from "../store";

// Fixed, centred under the header; fades in, hides after 1.8s (timer lives in the store).
export function Toast() {
  const { toast } = useStore();
  const [text, setText] = useState("");
  useEffect(() => {
    if (toast) setText(toast);
  }, [toast]);
  return (
    <div className={"toast" + (toast ? " is-on" : "")} role="status" aria-live="polite">
      {text}
    </div>
  );
}
