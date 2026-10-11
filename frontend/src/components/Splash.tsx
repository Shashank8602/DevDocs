import { useEffect, useState } from "react";

const SHOW_MS = 1000;
const FADE_MS = 500;

/** Logo splash: shown 1 s, fades out over 0.5 s, then calls onDone. */
export default function Splash({ onDone }: { onDone: () => void }) {
  const [fading, setFading] = useState(false);

  useEffect(() => {
    const fade = setTimeout(() => setFading(true), SHOW_MS);
    const done = setTimeout(onDone, SHOW_MS + FADE_MS);
    return () => {
      clearTimeout(fade);
      clearTimeout(done);
    };
  }, [onDone]);

  return (
    <div
      className={`fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-surface transition-opacity ${
        fading ? "opacity-0" : "opacity-100"
      }`}
      style={{ transitionDuration: `${FADE_MS}ms` }}
    >
      <img src="/logo.svg" alt="DevDocs" className="h-20 w-20" />
      <span className="text-2xl font-semibold tracking-tight">DevDocs</span>
    </div>
  );
}
