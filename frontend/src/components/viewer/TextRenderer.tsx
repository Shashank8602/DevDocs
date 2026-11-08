export default function TextRenderer({ text }: { text: string }) {
  return <pre className="selectable-text overflow-auto whitespace-pre-wrap p-4 text-sm leading-6 text-slate-800">{text}</pre>;
}
