import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { SecretName, settingsApi, SettingsPatch, SettingsValues, TestTarget } from "../api/settings";
import { Badge, Icon, Skeleton, Spinner, useToast } from "../components/ui";

type Typed = Partial<Record<SecretName, string>>;
type Result = { ok: boolean; message: string } | null;

function Section({ title, description, children, footer }: {
  title: string; description: string; children: ReactNode; footer?: ReactNode;
}) {
  return (
    <section className="card animate-slide-up p-6">
      <h2 className="text-base font-semibold text-slate-900">{title}</h2>
      <p className="mt-0.5 text-sm text-slate-500">{description}</p>
      <div className="mt-5 grid gap-5 sm:grid-cols-2">{children}</div>
      {footer && <div className="mt-5 border-t border-slate-200 pt-4">{footer}</div>}
    </section>
  );
}

function Field({ label, hint, wide, children }: { label: string; hint?: string; wide?: boolean; children: ReactNode }) {
  return (
    <label className={`block ${wide ? "sm:col-span-2" : ""}`}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="hint block">{hint}</span>}
    </label>
  );
}

function SecretField({
  label, saved, masked, value, onChange, hint,
}: { label: string; saved: boolean; masked: string; value: string; onChange: (v: string) => void; hint?: string }) {
  const [show, setShow] = useState(false);
  return (
    <Field label={label} hint={hint ?? (saved ? "A key is saved. Type a new one to replace it." : "No key saved yet.")}>
      <div className="relative">
        <input
          type={show && value ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={saved ? masked : "Paste your API key"}
          autoComplete="off"
          spellCheck={false}
          className="input pr-10 font-mono"
        />
        {/* The saved key is never sent to the browser, so there is nothing to reveal unless it was just typed. */}
        <button
          type="button"
          disabled={!value}
          onClick={() => setShow((s) => !s)}
          aria-label={show ? "Hide key" : "Show key"}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-500 transition hover:text-slate-800 disabled:opacity-30"
        >
          <Icon name={show ? "eye-off" : "eye"} />
        </button>
      </div>
    </Field>
  );
}

function TestButton({ onClick, result, busy }: { onClick: () => void; result: Result; busy: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <button type="button" onClick={onClick} disabled={busy} className="btn-secondary">
        {busy ? <Spinner /> : <Icon name="refresh" />} Test Connection
      </button>
      {result && (
        <span role="status" className={`animate-fade-in text-sm ${result.ok ? "text-emerald-700" : "text-red-600"}`}>
          {result.message}
        </span>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading, isError } = useQuery({ queryKey: ["settings"], queryFn: settingsApi.get });
  const [form, setForm] = useState<SettingsValues | null>(null);
  const [typed, setTyped] = useState<Typed>({});
  const [results, setResults] = useState<Partial<Record<TestTarget, Result>>>({});
  const [testing, setTesting] = useState<TestTarget | null>(null);

  // Unsaved edits live only in this component's state, so leaving the page discards them.
  useEffect(() => {
    if (data && !form) setForm(data.values);
  }, [data, form]);

  const changed = useMemo(() => {
    if (!data || !form) return [] as (keyof SettingsValues)[];
    return (Object.keys(form) as (keyof SettingsValues)[]).filter((k) => form[k] !== data.values[k]);
  }, [data, form]);
  const typedKeys = (Object.keys(typed) as SecretName[]).filter((k) => (typed[k] ?? "").trim());
  const dirty = changed.length > 0 || typedKeys.length > 0;
  const embeddingChanged = changed.includes("embedding_model") || changed.includes("embedding_dims");

  const save = useMutation({
    mutationFn: () => {
      const patch: SettingsPatch = {};
      for (const k of changed) (patch as Record<string, unknown>)[k] = form![k];
      for (const k of typedKeys) patch[k] = typed[k]!.trim();
      return settingsApi.save(patch);
    },
    onSuccess: (res) => {
      qc.setQueryData(["settings"], { values: res.values, defaults: res.defaults, secrets: res.secrets });
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["config"] });
      setForm(res.values);
      setTyped({});
      setResults({});
      toast(
        res.needs_reindex > 0
          ? `Saved. ${res.needs_reindex} project${res.needs_reindex === 1 ? " needs" : "s need"} a re-index.`
          : "Settings saved. They apply from your next question.",
        "success",
      );
    },
    onError: (e: Error) => toast(e.message, "error"),
  });

  async function test(target: TestTarget) {
    if (!form) return;
    setTesting(target);
    try {
      const res = await settingsApi.test(target, {
        llm_provider: form.llm_provider, groq_llm_model: form.groq_llm_model, gemini_llm_model: form.gemini_llm_model,
        embedding_model: form.embedding_model, embedding_dims: form.embedding_dims,
        jev_base_url: form.jev_base_url, jev_model: form.jev_model,
        ...typed,
      });
      setResults((r) => ({ ...r, [target]: res }));
    } catch {
      setResults((r) => ({ ...r, [target]: { ok: false, message: "Couldn't connect. Check the API key and settings, then try again." } }));
    } finally {
      setTesting(null);
    }
  }

  if (isError) return <p className="text-red-600">Couldn't load settings. Is the backend running?</p>;
  if (isLoading || !data || !form) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        <Skeleton className="h-8 w-40" /><Skeleton className="h-48" /><Skeleton className="h-48" />
      </div>
    );
  }

  const set = <K extends keyof SettingsValues>(k: K, v: SettingsValues[K]) => setForm({ ...form, [k]: v });
  const num = (k: keyof SettingsValues) => (e: { target: { value: string } }) =>
    set(k, (e.target.value === "" ? 0 : Number(e.target.value)) as never);
  const reset = (keys: (keyof SettingsValues)[]) => setForm({ ...form, ...Object.fromEntries(keys.map((k) => [k, data.defaults[k]])) });
  const secrets = data.secrets;
  const groq = form.llm_provider === "groq";

  return (
    <div className="mx-auto max-w-3xl pb-24">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="mt-1 text-sm text-slate-500">
            Changes to the model, reranker and retrieval apply from the next question. Keys are stored encrypted.
          </p>
        </div>
      </div>

      <div className="mt-6 space-y-5">
        <Section title="General" description="Developer tools are for testing and debugging the pipeline.">
          <label className="flex cursor-pointer items-center gap-4 sm:col-span-2">
            <input
              type="checkbox" role="switch" checked={form.dev_mode}
              onChange={(e) => set("dev_mode", e.target.checked)}
              className="peer sr-only"
            />
            <span
              aria-hidden="true"
              className={`inline-flex h-6 w-11 shrink-0 items-center rounded-full p-0.5 transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-brand-500 ${
                form.dev_mode ? "bg-brand-600" : "bg-slate-300"
              }`}
            >
              <span className={`h-5 w-5 rounded-full bg-white shadow transition-transform ${form.dev_mode ? "translate-x-5" : "translate-x-0"}`} />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium leading-5 text-slate-800">Developer mode</span>
              <span className="mt-0.5 block text-xs leading-5 text-slate-500">
                Shows the Chunk Inspector, ES Inspector (with vector peek), Retrieval Playground and the chat Context
                inspector, and enables the FAULT_LLM / FAULT_RETRIEVAL failure flags. Applies immediately.
              </span>
            </span>
          </label>
        </Section>

        <Section
          title="LLM"
          description="The model that writes answers."
          footer={<TestButton onClick={() => test("llm")} result={results.llm ?? null} busy={testing === "llm"} />}
        >
          <Field label="Provider">
            <select className="input" value={form.llm_provider} onChange={(e) => set("llm_provider", e.target.value as never)}>
              <option value="groq">Groq</option>
              <option value="gemini">Gemini</option>
            </select>
          </Field>
          <Field label="Model">
            <input
              className="input font-mono"
              value={groq ? form.groq_llm_model : form.gemini_llm_model}
              onChange={(e) => set(groq ? "groq_llm_model" : "gemini_llm_model", e.target.value)}
            />
          </Field>
          {groq && (
            <Field
              label="Fallback model"
              hint="Used only when the main model's per-minute budget would make a question wait. Empty turns it off."
            >
              <input
                className="input font-mono" value={form.groq_fallback_model}
                onChange={(e) => set("groq_fallback_model", e.target.value)}
              />
            </Field>
          )}
          <SecretField
            label="Groq API key" saved={secrets.groq_api_key.set} masked={secrets.groq_api_key.masked}
            value={typed.groq_api_key ?? ""} onChange={(v) => setTyped({ ...typed, groq_api_key: v })}
          />
          <SecretField
            label="Gemini API key" saved={secrets.gemini_api_key.set} masked={secrets.gemini_api_key.masked}
            value={typed.gemini_api_key ?? ""} onChange={(v) => setTyped({ ...typed, gemini_api_key: v })}
            hint={undefined}
          />
          <Field label={`Temperature: ${form.llm_temperature.toFixed(2)}`} hint="Lower is more literal and consistent.">
            <input
              type="range" min={0} max={1} step={0.05} value={form.llm_temperature}
              onChange={(e) => set("llm_temperature", Number(e.target.value))}
              className="w-full accent-[rgb(var(--brand-600))]"
            />
          </Field>
          <Field label="Reasoning effort" hint="Higher thinks longer before answering (slower first token).">
            <select className="input" value={form.llm_reasoning_effort} onChange={(e) => set("llm_reasoning_effort", e.target.value as never)}>
              <option value="low">Low (fastest)</option><option value="medium">Medium</option><option value="high">High</option>
            </select>
          </Field>
          <Field label="Max output tokens" hint="A ceiling, not a target length.">
            <input type="number" min={64} max={8192} step={64} className="input" value={form.llm_max_output_tokens} onChange={num("llm_max_output_tokens")} />
          </Field>
        </Section>

        <Section
          title="Embeddings"
          description="How files and questions are turned into vectors."
          footer={
            <div className="space-y-3">
              {embeddingChanged && (
                <p role="alert" className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-50 px-3 py-2 text-sm text-amber-700">
                  <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
                  Saving this change marks your projects “Re-index required” and pauses chat for them until they are re-indexed.
                  Re-indexing re-embeds stored chunks (no re-upload) and uses embedding quota.
                </p>
              )}
              <TestButton onClick={() => test("embedding")} result={results.embedding ?? null} busy={testing === "embedding"} />
            </div>
          }
        >
          <Field label="Model">
            <input className="input font-mono" value={form.embedding_model} onChange={(e) => set("embedding_model", e.target.value)} />
          </Field>
          <SecretField
            label="Embedding API key" saved={secrets.embedding_api_key.set} masked={secrets.embedding_api_key.masked}
            value={typed.embedding_api_key ?? ""} onChange={(v) => setTyped({ ...typed, embedding_api_key: v })}
            hint={secrets.embedding_api_key.set ? "A key is saved. Type a new one to replace it." : "Optional. Leave empty to use the shared key above."}
          />
          <Field label="Dimensions" hint="Must match what the model supports.">
            <input type="number" min={64} max={3072} className="input" value={form.embedding_dims} onChange={num("embedding_dims")} />
          </Field>
        </Section>

        <Section
          title="Reranker"
          description="An optional second pass that reorders candidates. Off by default: it costs seconds per question and did not improve accuracy."
          footer={form.reranker === "jev" ? <TestButton onClick={() => test("jev")} result={results.jev ?? null} busy={testing === "jev"} /> : undefined}
        >
          <Field label="Reranker">
            <select className="input" value={form.reranker} onChange={(e) => set("reranker", e.target.value as never)}>
              <option value="cross_encoder">Cross-Encoder (local)</option>
              <option value="jev">Jev (System One)</option>
            </select>
          </Field>
          <Field label="Candidates to rerank" hint="0 turns the reranker off.">
            <input type="number" min={0} max={50} className="input" value={form.rerank_top_n} onChange={num("rerank_top_n")} />
          </Field>
          {form.reranker === "cross_encoder" ? (
            <Field label="Model" wide>
              <input className="input font-mono" value={form.reranker_model} onChange={(e) => set("reranker_model", e.target.value)} />
            </Field>
          ) : (
            <>
              <Field label="Jev base URL"><input className="input font-mono" value={form.jev_base_url} onChange={(e) => set("jev_base_url", e.target.value)} /></Field>
              <Field label="Jev model"><input className="input font-mono" value={form.jev_model} onChange={(e) => set("jev_model", e.target.value)} /></Field>
              <SecretField
                label="Jev API key" saved={secrets.jev_api_key.set} masked={secrets.jev_api_key.masked}
                value={typed.jev_api_key ?? ""} onChange={(v) => setTyped({ ...typed, jev_api_key: v })}
              />
            </>
          )}
        </Section>

        <Section
          title="Advanced RAG"
          description="Retrieval tuning. The defaults were chosen from the evaluation set; change them with care."
        >
          <Field label="Candidates per search" hint="Top-K asked of keyword search and vector search each.">
            <input type="number" min={1} max={50} className="input" value={form.retrieval_leg_k} onChange={num("retrieval_leg_k")} />
          </Field>
          <Field label="Chunks sent to the model" hint="Final Top-K after filtering.">
            <input type="number" min={1} max={20} className="input" value={form.retrieval_final_top_k} onChange={num("retrieval_final_top_k")} />
          </Field>
          <Field label="Context budget (tokens)" hint="Keep it modest: free-tier models are limited per minute.">
            <input type="number" min={500} max={8000} step={100} className="input" value={form.retrieval_context_tokens} onChange={num("retrieval_context_tokens")} />
          </Field>
          <Field label="RRF constant (k)">
            <input type="number" min={1} max={200} className="input" value={form.rrf_k} onChange={num("rrf_k")} />
          </Field>
          <Field label="Relevance gate" hint="Questions whose best match scores below this are not answered. Tied to the embedding model.">
            <input type="number" min={0} max={1} step={0.01} className="input" value={form.gate_vector_threshold} onChange={num("gate_vector_threshold")} />
          </Field>
          <Field label="Precision cut-off" hint="Drop chunks this far below the best match. 0 turns it off.">
            <input type="number" min={0} max={0.5} step={0.01} className="input" value={form.relative_cutoff} onChange={num("relative_cutoff")} />
          </Field>
          <Field label="Protected keyword hits" hint="Best keyword matches that are never filtered out.">
            <input type="number" min={0} max={10} className="input" value={form.bm25_protect} onChange={num("bm25_protect")} />
          </Field>
          <Field label="Test-file weight" hint="Multiplier for chunks from test files (1 = no preference).">
            <input type="number" min={0.1} max={1} step={0.05} className="input" value={form.retrieval_test_path_factor} onChange={num("retrieval_test_path_factor")} />
          </Field>
        </Section>

        <div className="flex justify-end">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => reset([
              "retrieval_leg_k", "rrf_k", "retrieval_final_top_k", "retrieval_context_tokens", "gate_vector_threshold",
              "relative_cutoff", "bm25_protect", "retrieval_test_path_factor",
            ])}
          >
            <Icon name="refresh" /> Reset Advanced RAG to defaults
          </button>
        </div>
      </div>

      <div
        className={`fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-surface/90 backdrop-blur transition-transform duration-200 ${
          dirty ? "translate-y-0" : "translate-y-full"
        }`}
      >
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-6 py-3">
          <span className="flex items-center gap-2 text-sm text-slate-600">
            <Badge tone="warning" dot>Unsaved changes</Badge>
            <span className="hidden sm:inline">Leaving this page discards them.</span>
          </span>
          <span className="flex gap-2">
            <button className="btn-secondary" onClick={() => { setForm(data.values); setTyped({}); }}>Discard</button>
            <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending && <Spinner />} Save changes
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}
