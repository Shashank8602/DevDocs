import type { UploadRules } from "../api/files";

/** Client-side filtering. The rules themselves come from the backend (GET /upload-rules); none are hard-coded here. */
export interface Selected {
  file: File;
  path: string;
}

export interface FilterResult {
  accepted: Selected[];
  skipped: { path: string; reason: string }[];
}

export const REASON_LABEL: Record<string, string> = {
  ignored: "ignored folder/file",
  unsupported_type: "unsupported file type",
  too_large: "over the per-file size limit",
  total_limit_exceeded: "over the total size limit",
  invalid_path: "invalid path",
};

export function fromInput(files: File[]): Selected[] {
  return files.map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

export function filterSelection(items: Selected[], rules: UploadRules): FilterResult {
  const ignored = new Set(rules.ignored_names);
  const accepted: Selected[] = [];
  const skipped: FilterResult["skipped"] = [];
  let total = 0;

  for (const { file, path } of items) {
    const dot = file.name.lastIndexOf(".");
    const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
    let reason: string | null = null;
    if (path.split("/").some((part) => ignored.has(part))) reason = "ignored";
    else if (!(ext in rules.extensions)) reason = "unsupported_type";
    else if (file.size > rules.max_file_bytes) reason = "too_large";
    else if (total + file.size > rules.max_total_bytes) reason = "total_limit_exceeded";

    if (reason) skipped.push({ path, reason });
    else {
      total += file.size;
      accepted.push({ file, path });
    }
  }
  return { accepted, skipped };
}
