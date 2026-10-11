import { FormEvent, useState } from "react";
import Modal from "./Modal";

const input =
  "w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500";
const primary = "rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50";
const secondary = "rounded-md border border-slate-300 px-4 py-2 text-sm hover:bg-slate-50";

interface FormProps {
  title: string;
  submitLabel: string;
  initialName?: string;
  initialDescription?: string;
  busy: boolean;
  error?: string;
  onSubmit: (values: { name: string; description: string }) => void;
  onClose: () => void;
}

export function ProjectFormDialog({
  title, submitLabel, initialName = "", initialDescription = "", busy, error, onSubmit, onClose,
}: FormProps) {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription);
  const valid = name.trim().length > 0 && name.trim().length <= 100;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid && !busy) onSubmit({ name: name.trim(), description: description.trim() });
  };

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Project name</span>
          <input autoFocus value={name} maxLength={100} onChange={(e) => setName(e.target.value)} className={input} />
        </label>
        <label className="block text-sm">
            <span className="mb-1 block font-medium">Description <span className="font-normal text-slate-400">(optional)</span></span>
            <textarea value={description} maxLength={500} rows={3} onChange={(e) => setDescription(e.target.value)} className={input} />
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondary}>Cancel</button>
          <button type="submit" disabled={!valid || busy} className={primary}>{submitLabel}</button>
        </div>
      </form>
    </Modal>
  );
}

interface DeleteProps {
  name: string;
  busy: boolean;
  error?: string;
  onConfirm: () => void;
  onClose: () => void;
}

export function DeleteProjectDialog({ name, busy, error, onConfirm, onClose }: DeleteProps) {
  return (
    <Modal title="Delete project?" onClose={onClose}>
      <p className="text-sm text-slate-600">
        <strong>{name}</strong> and everything in it (files, indexed content and chats) will be permanently deleted.
        This can't be undone.
      </p>
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      <div className="mt-6 flex justify-end gap-2">
        <button onClick={onClose} className={secondary}>Cancel</button>
        <button
          onClick={onConfirm}
          disabled={busy}
          className="rounded-md bg-danger px-4 py-2 text-sm font-medium text-white hover:bg-danger-hover disabled:opacity-50"
        >
          Delete
        </button>
      </div>
    </Modal>
  );
}
