import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function MarkdownRenderer({ text, className = "mx-auto max-w-3xl px-6 py-5" }: { text: string; className?: string }) {
  return (
    <div className={`prose-dd selectable-text text-sm leading-6 ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer noopener">{children}</a> }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
