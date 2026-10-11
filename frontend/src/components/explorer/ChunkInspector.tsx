import { useQuery } from "@tanstack/react-query";
import { filesApi, ProjectFile } from "../../api/files";
import Modal from "../Modal";

/** Dev-only (DEV_MODE=true): the chunks stored for one file, with their line / page / section metadata. */
export default function ChunkInspector({ projectId, file, onClose }: { projectId: string; file: ProjectFile; onClose: () => void }) {
  const { data: chunks, isLoading, isError } = useQuery({
    queryKey: ["projects", projectId, "files", file.id, "chunks"],
    queryFn: () => filesApi.chunks(projectId, file.id),
  });

  return (
    <Modal title={`Chunks — ${file.file_path}`} onClose={onClose} wide>
      {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {isError && <p className="text-sm text-red-600">Couldn't load the chunks. Is DEV_MODE=true on the backend?</p>}
      {chunks && chunks.length === 0 && <p className="text-sm text-slate-500">This file has no chunks (status: {file.status}).</p>}
      {chunks && chunks.length > 0 && (
        <>
          <p className="mb-3 text-sm text-slate-500">
            {chunks.length} chunk{chunks.length === 1 ? "" : "s"}
          </p>
          <ol className="space-y-3">
            {chunks.map((c) => (
              <li key={c.id} className="rounded-md border border-slate-200">
                <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-600">
                  <span className="font-medium">#{c.ordinal}</span>
                  {c.line_start != null && <span>lines {c.line_start}–{c.line_end}</span>}
                  {c.page_number != null && <span>page {c.page_number}</span>}
                  {c.section && <span className="min-w-0 max-w-full truncate" title={c.section}>section: {c.section}</span>}
                  <span>{c.token_count} tokens</span>
                </div>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words p-3 text-xs">{c.content}</pre>
              </li>
            ))}
          </ol>
        </>
      )}
    </Modal>
  );
}
