import hljs from "highlight.js/lib/common";
import { useEffect, useMemo, useRef } from "react";

interface Props {
  text: string;
  language: string | null;
  /** Cited line ranges (1-based, inclusive). The first one is scrolled into view; all are highlighted. */
  highlight?: [number, number][];
}

export default function CodeRenderer({ text, language, highlight = [] }: Props) {
  const lines = useMemo(() => {
    const html =
      language && hljs.getLanguage(language)
        ? hljs.highlight(text, { language, ignoreIllegals: true }).value
        : hljs.highlightAuto(text).value;
    return html.split("\n");
  }, [text, language]);
  const first = useRef<HTMLDivElement>(null);
  const firstLine = highlight.length ? highlight[0][0] : null;
  const marked = (n: number) => highlight.some(([a, b]) => n >= a && n <= b);

  useEffect(() => {
    first.current?.scrollIntoView({ block: "center" });
  }, [firstLine, lines]);

  return (
    <pre className="hljs overflow-auto bg-surface text-[13px] leading-5">
      <code className="block min-w-max">
        {lines.map((line, i) => (
          <div
            key={i}
            ref={i + 1 === firstLine ? first : undefined}
            className={`flex ${marked(i + 1) ? "bg-amber-500/15 shadow-[inset_3px_0_0_rgb(var(--amber-500))]" : ""}`}
          >
            <span className="w-12 shrink-0 select-none pr-3 text-right text-slate-400">{i + 1}</span>
            <span className="whitespace-pre pr-4" dangerouslySetInnerHTML={{ __html: line || " " }} />
          </div>
        ))}
      </code>
    </pre>
  );
}
