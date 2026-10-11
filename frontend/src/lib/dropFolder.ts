import type { UploadRules } from "../api/files";
import type { Selected } from "./ignore";

/** Reads a dropped folder. Ignored directories (e.g. node_modules) are not descended into. */
export async function readDropped(items: DataTransferItemList, rules: UploadRules) {
  const ignored = new Set(rules.ignored_names);
  const selected: Selected[] = [];
  const prunedDirs: string[] = [];

  const readAll = (reader: FileSystemDirectoryReader) =>
    new Promise<FileSystemEntry[]>((resolve, reject) => {
      const out: FileSystemEntry[] = [];
      const next = () =>
        reader.readEntries((batch) => {
          if (batch.length === 0) resolve(out);
          else { out.push(...batch); next(); }
        }, reject);
      next();
    });

  const walk = async (entry: FileSystemEntry) => {
    const path = entry.fullPath.replace(/^\//, "");
    if (entry.isDirectory) {
      if (ignored.has(entry.name)) { prunedDirs.push(path); return; }
      for (const child of await readAll((entry as FileSystemDirectoryEntry).createReader())) await walk(child);
    } else {
      const file = await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej));
      selected.push({ file, path });
    }
  };

  // Entries must be collected synchronously; the list is cleared after the event handler returns.
  const entries = Array.from(items).map((i) => i.webkitGetAsEntry()).filter((e): e is FileSystemEntry => !!e);
  for (const e of entries) await walk(e);
  return { selected, prunedDirs };
}
