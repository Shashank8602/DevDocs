import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { filesApi, ProjectFile } from "../../api/files";
import { apiFetch } from "../../api/http";
import CodeRenderer from "./CodeRenderer";
import DocxRenderer from "./DocxRenderer";
import MarkdownRenderer from "./MarkdownRenderer";
import PdfRenderer from "./PdfRenderer";
import TableRenderer from "./TableRenderer";
import { LineSelection, SelectionPopover, useLineSelection } from "./SelectionAsk";
import SelectionAnswer from "./SelectionAnswer";
import TextRenderer from "./TextRenderer";
import { FileIcon, Icon, Skeleton } from "../ui";

type Kind = "pdf" | "docx" | "markdown" | "csv" | "text" | "code";

function kindOf(file: ProjectFile): Kind {
  const ext = file.file_name.slice(file.file_name.lastIndexOf(".")).toLowerCase();
  if (ext === ".pdf") return "pdf";
  if (ext === ".docx") return "docx";
  if (ext === ".md") return "markdown";
  if (ext === ".csv") return "csv";
  if (ext === ".txt") return "text";
  return "code"; // source, json/yaml/xml/toml, and HTML (shown as source)
}

async function load(file: ProjectFile, binary: boolean) {
  const res = await apiFetch(filesApi.contentUrl(file.id));
  if (!res.ok) throw new Error(res.status === 404 ? "This file doesn't exist anymore." : "Couldn't load this file.");
  return binary ? res.arrayBuffer() : res.text();
}

interface Props {
  file: ProjectFile;
  onClose: () => void;
  /** Cited line ranges to highlight (code files). */
  highlight?: [number, number][];
  /** Select lines in a code file and ask about them. Omit to turn the feature off. */
  onAsk?: (question: string, selection: LineSelection & { file_id: string }) => void;
}

export default function FloatingFileViewer({ file, onClose, highlight, onAsk }: Props) {
  const kind = kindOf(file);
  const body = useRef<HTMLDivElement>(null);
  const { anchor, clear } = useLineSelection(body, kind === "code");
  // the question being answered in the small window on top of the viewer (nothing is saved)
  const [asking, setAsking] = useState<{ question: string; line_start: number; line_end: number; left: number; top: number } | null>(null);
  const binary = kind === "pdf" || kind === "docx";
  const { data, error, isLoading } = useQuery({
    queryKey: ["file-content", file.id],
    queryFn: () => load(file, binary),
    retry: false,
    gcTime: 0,
  });

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
      <div role="dialog" aria-modal="true" aria-label={file.file_path} className="flex h-[85vh] w-full max-w-4xl animate-scale-in flex-col overflow-hidden rounded-2xl border border-slate-200 bg-surface shadow-pop">
        <div className="flex items-center gap-3 border-b border-slate-200 px-4 py-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-slate-50">
            <FileIcon name={file.file_name} />
          </span>
          <div className="min-w-0 flex-1" title={file.file_path}>
            <p className="truncate text-sm font-semibold text-slate-900">{file.file_name}</p>
            <p className="flex min-w-0 items-center gap-1 truncate text-xs text-slate-500">
              {file.file_path.split("/").slice(0, -1).map((part, i) => (
                <span key={i} className="flex shrink-0 items-center gap-1">
                  {i > 0 && <Icon name="chevron-right" className="h-3 w-3 text-slate-300" />}
                  <span className="font-mono">{part}</span>
                </span>
              ))}
            </p>
          </div>
          {highlight && highlight.length > 0 && (
            <span className="hidden shrink-0 items-center gap-1.5 sm:flex">
              <span className="text-xs text-slate-500">Cited</span>
              {highlight.slice(0, 4).map(([a, b], i) => (
                <span key={i} className="rounded-md bg-amber-500/15 px-1.5 py-0.5 text-xs font-medium tabular-nums text-amber-700">
                  {a === b ? `L${a}` : `L${a}–${b}`}
                </span>
              ))}
              {highlight.length > 4 && <span className="text-xs text-slate-500">+{highlight.length - 4}</span>}
            </span>
          )}
          <button onClick={onClose} aria-label="Close" title="Close (Esc)" className="icon-btn"><Icon name="x" /></button>
        </div>
        <div ref={body} className="min-h-0 flex-1 overflow-auto">
          {isLoading && (
            <div className="space-y-2 p-4">{[70, 55, 80, 40, 65, 50].map((w, i) => <Skeleton key={i} className="h-3.5" style={{ width: `${w}%` }} />)}</div>
          )}
          {error && <p className="flex items-center gap-2 p-4 text-sm text-red-600"><Icon name="alert" /> {(error as Error).message}</p>}
          {data !== undefined && (
            <>
              {kind === "pdf" && <PdfRenderer data={data as ArrayBuffer} />}
              {kind === "docx" && <DocxRenderer data={data as ArrayBuffer} />}
              {kind === "markdown" && <MarkdownRenderer text={data as string} />}
              {kind === "csv" && <TableRenderer text={data as string} />}
              {kind === "text" && <TextRenderer text={data as string} />}
              {kind === "code" && (
                <CodeRenderer
                  text={data as string} language={file.language}
                  highlight={asking ? [...(highlight ?? []), [asking.line_start, asking.line_end]] : highlight}
                />
              )}
            </>
          )}
        </div>
        {kind === "code" && (
          <p className="flex items-center gap-1.5 border-t border-slate-200 bg-slate-50/60 px-4 py-2 text-xs text-slate-500">
            <Icon name="sparkles" className="h-3.5 w-3.5 text-brand-link" /> Tip: select some lines to ask about them.
          </p>
        )}
      </div>
      {anchor && (
        <SelectionPopover
          anchor={anchor}
          path={file.file_path}
          onClose={clear}
          onAsk={(question, sel) => {
            setAsking({ question, ...sel, left: anchor.left, top: anchor.top });
            clear();
            window.getSelection()?.removeAllRanges();
          }}
        />
      )}
      {asking && (
        <SelectionAnswer
          key={`${asking.line_start}-${asking.line_end}-${asking.question}`}
          fileId={file.id} path={file.file_path} {...asking}
          onClose={() => setAsking(null)}
          onOpenInChat={onAsk && ((question) => onAsk(question, { line_start: asking.line_start, line_end: asking.line_end, file_id: file.id }))}
        />
      )}
    </div>,
    document.body,
  );
}
