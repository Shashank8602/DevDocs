import { ReactNode, useEffect } from "react";

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

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/40 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`w-full ${wide ? "flex max-w-4xl flex-col overflow-hidden p-0" : "max-w-md p-6"} max-h-[90vh] rounded-lg bg-surface shadow-xl`}
      >
        {wide ? (
          <>
            <div className="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 px-6 py-3">
              <h2 className="min-w-0 truncate text-lg font-semibold">{title}</h2>
              <button
                onClick={onClose}
                className="shrink-0 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-600 shadow-sm transition hover:bg-red-100 active:scale-95"
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
    </div>
  );
}
