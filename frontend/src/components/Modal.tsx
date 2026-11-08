import { ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./ui";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}

export default function Modal({ title, onClose, children, wide }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const close = (
    <button type="button" onClick={onClose} aria-label="Close" title="Close (Esc)" className="icon-btn -mr-1.5">
      <Icon name="x" />
    </button>
  );

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-slate-900/50 p-4 backdrop-blur-[2px]"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`flex w-full flex-col overflow-hidden ${wide ? "max-w-4xl" : "max-w-md"} max-h-[90vh] animate-scale-in rounded-2xl border border-slate-200 bg-surface shadow-pop`}
      >
        <div className={`flex shrink-0 items-center justify-between gap-4 px-6 ${wide ? "border-b border-slate-200 py-3" : "pb-1 pt-5"}`}>
          <h2 className="min-w-0 truncate text-lg font-semibold tracking-tight">{title}</h2>
          {close}
        </div>
        <div className={`min-h-0 overflow-y-auto ${wide ? "p-6" : "px-6 pb-6 pt-3"}`}>{children}</div>
      </div>
    </div>,
    document.body,
  );
}
