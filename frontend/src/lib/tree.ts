import type { ProjectFile } from "../api/files";

export interface TreeNode {
  name: string;
  path: string;
  file?: ProjectFile;
  children: TreeNode[];
}

export function buildTree(files: ProjectFile[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: [] };
  for (const file of files) {
    let node = root;
    const parts = file.file_path.split("/");
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      let child = node.children.find((c) => c.name === part && !!c.file === (i === parts.length - 1));
      if (!child) {
        child = { name: part, path, children: [], file: i === parts.length - 1 ? file : undefined };
        node.children.push(child);
      }
      node = child;
    });
  }
  const sort = (n: TreeNode) => {
    n.children.sort((a, b) => Number(!!a.file) - Number(!!b.file) || a.name.localeCompare(b.name));
    n.children.forEach(sort);
  };
  sort(root);
  return root;
}

/** Filename/path search: 2+ characters, case-insensitive. */
export function searchFiles(files: ProjectFile[], query: string): ProjectFile[] | null {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return null;
  return files.filter((f) => f.file_path.toLowerCase().includes(q));
}
