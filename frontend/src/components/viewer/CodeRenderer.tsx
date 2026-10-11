import hljs from "highlight.js/lib/common";
import { useMemo } from "react";

export default function CodeRenderer({ text, language }: { text: string; language: string | null }) {
  const lines = useMemo(() => {
    const html =
      language && hljs.getLanguage(language)
        ? hljs.highlight(text, { language, ignoreIllegals: true }).value
        : hljs.highlightAuto(text).value;
    return html.split("\n");
  }, [text, language]);

  return (
    <pre className="hljs overflow-auto bg-surface text-[13px] leading-5">
      <code className="block min-w-max">
        {lines.map((line, i) => (
          <div key={i} className="flex">
            <span className="w-12 shrink-0 select-none pr-3 text-right text-slate-400">{i + 1}</span>
            <span className="whitespace-pre pr-4" dangerouslySetInnerHTML={{ __html: line || " " }} />
          </div>
        ))}
      </code>
    </pre>
  );
}
