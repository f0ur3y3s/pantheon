import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { CircleAlert, CircleCheck } from "lucide-react";

const ClipboardContext = createContext<(text: string) => Promise<void>>(async () => {});

export function ClipboardFeedback({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<{ id: number; message: string; error: boolean } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const sequence = useRef(0);
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = useCallback(async (text: string) => {
    let message = "Copied to clipboard!";
    let error = false;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
    } catch {
      error = true;
      message = window.isSecureContext
        ? "Could not copy. Select and copy the text manually."
        : "Copy needs HTTPS. Open the HTTPS dashboard to copy.";
    }
    clearTimeout(timer.current);
    setNotice({ id: ++sequence.current, message, error });
    timer.current = setTimeout(() => setNotice(null), error ? 6000 : 3000);
  }, []);

  return <ClipboardContext.Provider value={copy}>
    {children}
    <div className="toast-region" role="status" aria-live="polite" aria-atomic="true">
      {notice && <div className={`toast-message ${notice.error ? "is-error" : ""}`} key={notice.id}>
        {notice.error ? <CircleAlert size={16} strokeWidth={1.5} aria-hidden="true"/> : <CircleCheck size={16} strokeWidth={1.5} aria-hidden="true"/>}
        <span>{notice.message}</span>
      </div>}
    </div>
  </ClipboardContext.Provider>;
}

export function useClipboard() { return useContext(ClipboardContext); }
