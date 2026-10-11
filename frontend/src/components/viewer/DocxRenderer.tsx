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
      className="space-y-3 p-5 text-sm leading-6 text-slate-800 [&_h1]:text-2xl [&_h1]:font-semibold [&_h2]:text-xl [&_h2]:font-semibold [&_li]:ml-5 [&_ol]:list-decimal [&_table]:border-collapse [&_td]:border [&_td]:px-2 [&_td]:py-1 [&_ul]:list-disc"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
