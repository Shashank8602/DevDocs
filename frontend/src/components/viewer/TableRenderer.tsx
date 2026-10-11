import Papa from "papaparse";
import { useMemo } from "react";

export default function TableRenderer({ text }: { text: string }) {
  const rows = useMemo(() => Papa.parse<string[]>(text.trim(), { skipEmptyLines: true }).data, [text]);
  const [head, ...body] = rows;
  if (!head) return <p className="p-4 text-sm text-slate-500">This file is empty.</p>;
  return (
    <div className="overflow-auto p-4">
      <table className="min-w-full border-collapse text-sm">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className="border border-slate-200 bg-slate-50 px-3 py-1.5 text-left font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} className="border border-slate-200 px-3 py-1.5">{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
