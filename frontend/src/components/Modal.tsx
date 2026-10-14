import { ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";

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

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-slate-900/50 p-4 backdrop-blur-[2px]"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`w-full ${wide ? "flex max-w-4xl flex-col overflow-hidden p-0" : "max-w-md p-6"} max-h-[90vh] animate-scale-in rounded-xl bg-surface shadow-pop`}
      >
        {wide ? (
          <>
            <div className="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 px-6 py-3">
              <h2 className="min-w-0 truncate text-lg font-semibold">{title}</h2>
              <button
                onClick={onClose}
                className="btn-danger shrink-0 px-3 py-1.5"
              >
                Close
              </button>
            </div>
            <div className="overflow-y-auto p-6">{children}</div>
          </>
        ) : (
          <>
            <h2 className="text-lg font-semibold">{title}</h2>
            <div className="mt-4">{children}</div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
