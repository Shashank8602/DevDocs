import { AccessFile, AccessRule, Visibility } from "../api/access";

/** Display-side mirror of the server's resolver (the server stays the source of truth for every real check). */
export function governingRule(rules: AccessRule[], path: string): AccessRule | null {
  const byPath = new Map(rules.map((r) => [r.path, r]));
  const parts = path.split("/").filter(Boolean);
  for (let end = parts.length; end > 0; end--) {
    const rule = byPath.get(parts.slice(0, end).join("/"));
    if (rule) return rule;
  }
  return byPath.get("") ?? null;
}

export function canRead(rule: AccessRule | null, userId: string): boolean {
  if (!rule || rule.visibility === "admin") return false;
  return rule.visibility === "everyone" || rule.user_ids.includes(userId);
}

export interface TreeNode {
  path: string;
  name: string;
  isFile: boolean;
  files: AccessFile[]; // every file at or below this node
  children: TreeNode[];
}

/** Folders and files derived from the project's real paths. Nothing about names is assumed. */
export function buildTree(files: AccessFile[]): TreeNode {
  const root: TreeNode = { path: "", name: "Whole project", isFile: false, files: [], children: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    node.files.push(file);
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      const isFile = i === parts.length - 1;
      let child = node.children.find((c) => c.path === path);
      if (!child) {
        child = { path, name: part, isFile, files: [], children: [] };
        node.children.push(child);
      }
      if (!isFile) child.files.push(file);
      else child.files = [file];
      node = child;
    });
  }
  const sort = (n: TreeNode) => {
    n.children.sort((a, b) => Number(a.isFile) - Number(b.isFile) || a.name.localeCompare(b.name));
    n.children.forEach(sort);
  };
  sort(root);
  return root;
}

export function findNode(root: TreeNode, path: string): TreeNode | null {
  if (path === "") return root;
  let node: TreeNode = root;
  for (const part of path.split("/")) {
    const next = node.children.find((c) => c.name === part);
    if (!next) return null;
    node = next;
  }
  return node;
}

/** How many of a node's files a person can read: none, some (partial) or all. */
export function coverage(node: TreeNode, rules: AccessRule[], userId: string): "none" | "partial" | "all" {
  if (node.files.length === 0) return "none";
  const readable = node.files.filter((f) => canRead(governingRule(rules, f.path), userId)).length;
  return readable === 0 ? "none" : readable === node.files.length ? "all" : "partial";
}

/** Who can read a path through the rule that governs it (members only; admins always can). */
export function readers(rule: AccessRule | null, memberIds: string[]): string[] {
  if (!rule || rule.visibility === "admin") return [];
  return rule.visibility === "everyone" ? memberIds : rule.user_ids.filter((id) => memberIds.includes(id));
}

export function ruleFor(path: string, ids: string[]): AccessRule {
  const visibility: Visibility = ids.length === 0 ? "admin" : "people";
  return { path, visibility, user_ids: ids };
}
