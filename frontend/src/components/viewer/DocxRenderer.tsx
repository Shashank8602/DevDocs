import mammoth from "mammoth";
import { useEffect, useState } from "react";

export default function DocxRenderer({ data }: { data: ArrayBuffer }) {
  const [html, setHtml] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // mammoth emits only structural HTML (no scripts or event handlers).
    mammoth.convertToHtml({ arrayBuffer: data }).then((r) => setHtml(r.value), () => setFailed(true));
  }, [data]);

  if (failed) return <p className="p-4 text-sm text-red-600">This document couldn't be displayed.</p>;
  if (html === null) return <p className="p-4 text-sm text-slate-500">Loading…</p>;
  return (
    <div
      className="prose-dd selectable-text mx-auto max-w-3xl px-6 py-5 text-sm leading-6"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
