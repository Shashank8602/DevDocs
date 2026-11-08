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
    <pre className="hljs selectable-text overflow-auto bg-surface py-2 text-[13px] leading-[1.35rem]">
      <code className="block min-w-max">
        {lines.map((line, i) => {
          const on = marked(i + 1);
          return (
            <div
              key={i}
              data-line={i + 1}
              ref={i + 1 === firstLine ? first : undefined}
              className={`flex ${on ? "bg-amber-500/15" : "hover:bg-slate-100/60"}`}
            >
              <span className={`w-14 shrink-0 cursor-default select-none pr-4 text-right tabular-nums ${on ? "font-medium text-amber-700" : "text-slate-400"}`}>{i + 1}</span>
              <span className="whitespace-pre pr-6" dangerouslySetInnerHTML={{ __html: line || " " }} />
            </div>
          );
        })}
      </code>
    </pre>
  );
}
