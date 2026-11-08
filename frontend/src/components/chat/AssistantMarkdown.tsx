import hljs from "highlight.js/lib/common";
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CopyButton, Icon } from "../ui";

function CodeBlock({ language, text }: { language: string | undefined; text: string }) {
  const known = language && hljs.getLanguage(language);
  const html = known
    ? hljs.highlight(text, { language, ignoreIllegals: true }).value
    : hljs.highlightAuto(text).value;
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
      <div className="flex cursor-default items-center justify-between border-b border-slate-200 bg-slate-100/60 py-1 pl-3 pr-1.5 text-xs text-slate-500">
        <span className="flex items-center gap-1.5 font-mono"><Icon name="code" className="h-3.5 w-3.5" />{known ? language : "code"}</span>
        <CopyButton text={text} className="text-slate-500" />
      </div>
      <pre className="hljs m-0 overflow-auto rounded-none border-0 bg-transparent p-3.5 text-[13px] leading-5">
        <code dangerouslySetInnerHTML={{ __html: html }} />
      </pre>
    </div>
  );
}

/** Memoised: while an answer streams, only the growing last message re-renders. */
const AssistantMarkdown = memo(function AssistantMarkdown({ text }: { text: string }) {
  return (
    <div className="prose-dd selectable-text text-[15px] leading-7">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Fenced blocks arrive as <pre><code class="language-x">; inline code has no className.
          pre: ({ children }) => <>{children}</>,
          code: ({ className, children }) => {
            const body = String(children ?? "");
            const language = /language-([\w+-]+)/.exec(className ?? "")?.[1];
            if (!language && !body.includes("\n")) return <code>{children}</code>;
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
