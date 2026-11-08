import { FormEvent, useState } from "react";
import { Icon, Spinner } from "../components/ui";
import { useAuth } from "../auth/AuthContext";

/** First run: create the admin. Afterwards: plain email and password sign-in. */
export default function LoginPage({ firstRun }: { firstRun: boolean }) {
  const { signIn, setup } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (firstRun) await setup(email.trim(), password, name.trim());
      else await signIn(email.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen">
      <aside
        className="relative hidden w-[44%] max-w-xl flex-col justify-between overflow-hidden p-10 text-white lg:flex"
        style={{ backgroundImage: "linear-gradient(150deg, rgb(var(--brand-500)), rgb(var(--brand-600)) 55%, rgb(49 46 129))" }}
      >
        {/* dotted grid + soft glow, purely decorative */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 opacity-[0.18]"
          style={{ backgroundImage: "radial-gradient(rgb(255 255 255) 1px, transparent 1px)", backgroundSize: "22px 22px" }}
        />
        <div aria-hidden="true" className="pointer-events-none absolute -bottom-32 -right-24 h-96 w-96 rounded-full bg-white/10 blur-3xl" />
        <div className="relative flex items-center gap-3">
          <img src="/logo.svg" alt="" className="h-10 w-10 rounded-xl ring-1 ring-white/30" />
          <span className="text-xl font-semibold tracking-tight">DevDocs</span>
        </div>
        <div className="relative">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight">Ask your codebase anything.</h2>
          <p className="mt-3 max-w-sm text-base leading-7 text-white/80">
            Upload a project, then chat with it. Every answer points to the exact files and lines it came from.
          </p>
          <ul className="mt-8 space-y-3 text-sm text-white/90">
            {([
              ["code", "Answers cite file paths and line ranges"],
              ["refresh", "Re-uploads only process what changed"],
              ["lock", "People see only the folders shared with them"],
            ] as const).map(([icon, text]) => (
              <li key={text} className="flex items-center gap-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/15 ring-1 ring-white/20">
                  <Icon name={icon} className="h-4 w-4" />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-white/60">Project-scoped RAG for software teams</p>
      </aside>

      <div className="flex flex-1 items-center justify-center p-4 sm:p-8">
      <form
        onSubmit={submit}
        className="w-full max-w-sm animate-scale-in space-y-5"
        aria-label={firstRun ? "Create the admin account" : "Sign in"}
      >
        <div className="flex items-center gap-2.5 lg:hidden">
          <img src="/logo.svg" alt="" className="h-9 w-9" />
          <span className="text-lg font-semibold tracking-tight">DevDocs</span>
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{firstRun ? "Create the admin account" : "Welcome back"}</h1>
          <p className="mt-1 text-sm text-slate-500">
            {firstRun
              ? "This is the first run. The admin can read every file and adds everyone else."
              : "Use the email and password your admin gave you."}
          </p>
        </div>
        {firstRun && (
          <label className="block">
            <span className="label">Name <span className="font-normal text-slate-400">(optional)</span></span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} autoComplete="name" className="input" />
          </label>
        )}
        <label className="block">
          <span className="label">Email</span>
          <input
            type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)}
            autoComplete="username" className="input"
          />
        </label>
        <label className="block">
          <span className="label">Password</span>
          <input
            type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
            minLength={firstRun ? 8 : undefined} autoComplete={firstRun ? "new-password" : "current-password"} className="input"
          />
          {firstRun && <span className="hint">At least 8 characters.</span>}
        </label>
        {error && (
          <p role="alert" className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />{error}
          </p>
        )}
        <button type="submit" disabled={busy || !email || !password} className="btn-primary w-full py-2.5">
          {busy && <Spinner />} {firstRun ? "Create admin and continue" : "Sign in"}
        </button>
      </form>
      </div>
    </div>
  );
}
