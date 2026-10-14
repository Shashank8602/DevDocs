import hljs from "highlight.js/lib/common";
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CopyButton } from "../ui";

function CodeBlock({ language, text }: { language: string | undefined; text: string }) {
  const known = language && hljs.getLanguage(language);
  const html = known
    ? hljs.highlight(text, { language, ignoreIllegals: true }).value
    : hljs.highlightAuto(text).value;
  return (
    <div className="my-3 overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-1 text-xs text-slate-500">
        <span className="font-mono">{known ? language : "code"}</span>
        <CopyButton text={text} className="text-slate-500" />
      </div>
      <pre className="hljs overflow-auto bg-transparent p-3 text-[13px] leading-5">
        <code dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}

/** Memoised: while an answer streams, only the growing last message re-renders. */
const AssistantMarkdown = memo(function AssistantMarkdown({ text }: { text: string }) {
  return (
    <div
      className="space-y-3 text-[15px] leading-7 text-slate-800
        [&_a]:text-brand-link [&_a]:underline [&_a]:underline-offset-2
        [&_blockquote]:border-l-2 [&_blockquote]:border-slate-300 [&_blockquote]:pl-3 [&_blockquote]:text-slate-600
        [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:font-semibold
        [&_hr]:border-slate-200 [&_li]:my-0.5 [&_li]:ml-5 [&_ol]:list-decimal [&_ul]:list-disc
        [&_table]:my-2 [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:border-collapse [&_table]:text-sm
        [&_td]:border [&_td]:border-slate-200 [&_td]:px-2.5 [&_td]:py-1.5
        [&_th]:border [&_th]:border-slate-200 [&_th]:bg-slate-100 [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-left"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Fenced blocks arrive as <pre><code class="language-x">; inline code has no className.
          pre: ({ children }) => <>{children}</>,
          code: ({ className, children }) => {
            const body = String(children ?? "");
            const language = /language-([\w+-]+)/.exec(className ?? "")?.[1];
            if (!language && !body.includes("\n")) {
              return <code className="rounded-md bg-slate-200/70 px-1.5 py-0.5 text-[13px]">{children}</code>;
            }
            return <CodeBlock language={language} text={body.replace(/\n$/, "")} />;
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

export default AssistantMarkdown;
