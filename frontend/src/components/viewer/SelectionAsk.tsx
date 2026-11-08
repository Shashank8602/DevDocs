import { RefObject, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../ui";

export interface LineSelection {
  line_start: number;
  line_end: number;
}

interface Anchor extends LineSelection {
  left: number;
  top: number;
}

const lineOf = (node: Node | null): number | null => {
  const el = node instanceof Element ? node : node?.parentElement;
  const row = el?.closest("[data-line]");
  return row ? Number(row.getAttribute("data-line")) : null;
};

/** Watches the mouse and keyboard selection inside `container` and reports the 1-based line range it covers. */
export function useLineSelection(container: RefObject<HTMLElement>, enabled: boolean) {
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  const read = useCallback(() => {
    const el = container.current;
    const sel = window.getSelection();
    if (!el || !sel || sel.isCollapsed || sel.rangeCount === 0) return setAnchor(null);
    const a = lineOf(sel.anchorNode), b = lineOf(sel.focusNode);
    if (a === null || b === null || !el.contains(sel.anchorNode) || !el.contains(sel.focusNode)) return setAnchor(null);
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    setAnchor({ line_start: Math.min(a, b), line_end: Math.max(a, b), left: rect.left, top: rect.bottom });
  }, [container]);

  useEffect(() => {
    const el = container.current;
    if (!enabled || !el) return;
    // read after the browser has finished updating the selection
    const later = () => setTimeout(read, 0);
    el.addEventListener("mouseup", later);
    el.addEventListener("keyup", later);
    return () => {
      el.removeEventListener("mouseup", later);
      el.removeEventListener("keyup", later);
    };
  }, [container, enabled, read]);

  return { anchor, clear: () => setAnchor(null) };
}

const QUICK = ["Explain this", "Why is this here?", "What calls this?"] as const;

/** The floating prompt beside a selection: quick questions and a free-text box. */
export function SelectionPopover({
  anchor, path, onAsk, onClose,
}: { anchor: Anchor; path: string; onAsk: (question: string, selection: LineSelection) => void; onClose: () => void }) {
  const [text, setText] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const selection = { line_start: anchor.line_start, line_end: anchor.line_end };
  const range = anchor.line_start === anchor.line_end ? `line ${anchor.line_start}` : `lines ${anchor.line_start}-${anchor.line_end}`;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && (e.stopPropagation(), onClose());
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const left = Math.max(8, Math.min(anchor.left, window.innerWidth - 340));
  const top = Math.min(anchor.top + 8, window.innerHeight - 190);
  const ask = (q: string) => q.trim() && onAsk(q.trim(), selection);

  return createPortal(
    <div
      ref={box}
      role="dialog"
      aria-label={`Ask about ${path}, ${range}`}
      style={{ left, top }}
      className="fixed z-[65] w-80 animate-scale-in rounded-xl border border-slate-200 bg-surface p-3 shadow-pop"
      onMouseDown={(e) => { if ((e.target as HTMLElement).tagName !== "INPUT") e.preventDefault(); }}
    >
      <p className="truncate text-xs text-slate-500">
        <span className="font-mono text-slate-700">{path}</span> · {range}
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {QUICK.map((q) => (
          <button key={q} type="button" onClick={() => ask(q)} className="btn-secondary px-2.5 py-1 text-xs">{q}</button>
        ))}
      </div>
      <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
        <input
          value={text} onChange={(e) => setText(e.target.value)} maxLength={500} className="input py-1.5 text-sm"
          placeholder="Ask about these lines…" aria-label="Your question about the selected lines"
        />
        <button type="submit" disabled={!text.trim()} className="btn-primary px-3 py-1.5" aria-label="Ask">
          <Icon name="send" />
        </button>
      </form>
    </div>,
    document.body,
  );
}
