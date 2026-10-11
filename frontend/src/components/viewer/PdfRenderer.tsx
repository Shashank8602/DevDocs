import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export default function PdfRenderer({ data }: { data: ArrayBuffer }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [doc, setDoc] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // pdf.js transfers the buffer to its worker, so hand it a copy.
    const task = pdfjs.getDocument({ data: new Uint8Array(data.slice(0)) });
    task.promise.then((d) => { setDoc(d); setPage(1); }, () => setFailed(true));
    return () => { task.destroy(); };
  }, [data]);

  useEffect(() => {
    if (!doc || !canvas.current) return;
    let cancelled = false;
    doc.getPage(page).then((p) => {
      if (cancelled || !canvas.current) return;
      const viewport = p.getViewport({ scale: 1.4 });
      const el = canvas.current;
      el.width = viewport.width;
      el.height = viewport.height;
      p.render({ canvas: el, viewport });
    });
    return () => { cancelled = true; };
  }, [doc, page]);

  if (failed) return <p className="p-4 text-sm text-red-600">This PDF couldn't be displayed.</p>;
  if (!doc) return <p className="p-4 text-sm text-slate-500">Loading…</p>;

  const btn = "rounded border border-slate-300 px-2 py-1 text-sm disabled:opacity-40";
  return (
    <div className="flex flex-col items-center gap-3 p-4">
      <div className="flex items-center gap-3">
        <button className={btn} disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span className="text-sm text-slate-600">Page {page} of {doc.numPages}</span>
        <button className={btn} disabled={page >= doc.numPages} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      <canvas ref={canvas} className="max-w-full border border-slate-200 shadow-sm" />
    </div>
  );
}
