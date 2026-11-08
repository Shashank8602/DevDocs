import Papa from "papaparse";
import { useMemo } from "react";

export default function TableRenderer({ text }: { text: string }) {
  const rows = useMemo(() => Papa.parse<string[]>(text.trim(), { skipEmptyLines: true }).data, [text]);
  const [head, ...body] = rows;
  if (!head) return <p className="p-4 text-sm text-slate-500">This file is empty.</p>;
  return (
    <div className="p-4"><div className="overflow-auto rounded-lg border border-slate-200">
      <table className="selectable-text min-w-full border-collapse text-sm">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className="sticky top-0 border-b border-r border-slate-200 bg-slate-100 px-3 py-2 text-left font-semibold text-slate-700 last:border-r-0">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, i) => (
            <tr key={i} className="even:bg-slate-50/70 hover:bg-slate-100/70">
              {r.map((c, j) => (
                <td key={j} className="border-b border-r border-slate-200 px-3 py-1.5 last:border-r-0">{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table></div>
    </div>
  );
}
