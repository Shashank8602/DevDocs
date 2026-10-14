import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { createPortal } from "react-dom";
import { filesApi, ProjectFile } from "../../api/files";
import CodeRenderer from "./CodeRenderer";
import DocxRenderer from "./DocxRenderer";
import MarkdownRenderer from "./MarkdownRenderer";
import PdfRenderer from "./PdfRenderer";
import TableRenderer from "./TableRenderer";
import TextRenderer from "./TextRenderer";

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
  const res = await fetch(filesApi.contentUrl(file.id));
  if (!res.ok) throw new Error(res.status === 404 ? "This file doesn't exist anymore." : "Couldn't load this file.");
  return binary ? res.arrayBuffer() : res.text();
}

interface Props {
  file: ProjectFile;
  onClose: () => void;
  /** Cited line ranges to highlight (code files). */
  highlight?: [number, number][];
}

export default function FloatingFileViewer({ file, onClose, highlight }: Props) {
  const kind = kindOf(file);
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
      <div role="dialog" aria-modal="true" aria-label={file.file_path} className="flex h-[85vh] w-full max-w-4xl animate-scale-in flex-col overflow-hidden rounded-xl bg-surface shadow-pop">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <span className="min-w-0 truncate font-mono text-sm font-medium text-slate-800" title={file.file_path}>
            {file.file_path}
            {highlight && highlight.length > 0 && (
              <span className="ml-2 font-sans text-xs font-normal text-slate-500">
                cited: {highlight.map(([a, b]) => (a === b ? a : `${a}–${b}`)).join(", ")}
              </span>
            )}
          </span>
          <button onClick={onClose} aria-label="Close" className="ml-3 rounded px-2 text-xl leading-none text-slate-500 hover:bg-slate-100">×</button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {isLoading && <p className="p-4 text-sm text-slate-500">Loading…</p>}
          {error && <p className="p-4 text-sm text-red-600">{(error as Error).message}</p>}
          {data !== undefined && (
            <>
              {kind === "pdf" && <PdfRenderer data={data as ArrayBuffer} />}
              {kind === "docx" && <DocxRenderer data={data as ArrayBuffer} />}
              {kind === "markdown" && <MarkdownRenderer text={data as string} />}
              {kind === "csv" && <TableRenderer text={data as string} />}
              {kind === "text" && <TextRenderer text={data as string} />}
              {kind === "code" && <CodeRenderer text={data as string} language={file.language} highlight={highlight} />}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
