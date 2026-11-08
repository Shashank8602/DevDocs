import { describe, expect, it } from "vitest";
import { AccessFile, AccessRule } from "../api/access";
import { buildTree, canRead, coverage, findNode, governingRule, readers, ruleFor } from "./accessTree";

const rule = (path: string, visibility: AccessRule["visibility"], user_ids: string[] = []): AccessRule => ({ path, visibility, user_ids });
const file = (path: string): AccessFile => ({ id: path, path, status: "ready" });

describe("governingRule", () => {
  const rules = [rule("docs", "everyone"), rule("docs/private", "admin"), rule("docs/private/ok.md", "people", ["u1"])];

  it("picks the most specific rule, so a file overrides its folder", () => {
    expect(governingRule(rules, "docs/readme.md")?.path).toBe("docs");
    expect(governingRule(rules, "docs/private/x.md")?.path).toBe("docs/private");
    expect(governingRule(rules, "docs/private/ok.md")?.path).toBe("docs/private/ok.md");
  });

  it("returns null when no rule covers a path (admin only)", () => {
    expect(governingRule(rules, "other/file.md")).toBeNull();
  });

  it("does not treat a sibling folder with the same prefix as inside the folder", () => {
    expect(governingRule(rules, "docs-private/a.md")).toBeNull();
  });

  it("falls back to a rule on the whole project", () => {
    expect(governingRule([rule("", "everyone")], "any/where.md")?.path).toBe("");
  });
});

describe("canRead", () => {
  it("follows the visibility of the rule", () => {
    expect(canRead(rule("a", "everyone"), "u1")).toBe(true);
    expect(canRead(rule("a", "admin"), "u1")).toBe(false);
    expect(canRead(rule("a", "people", ["u1"]), "u1")).toBe(true);
    expect(canRead(rule("a", "people", ["u2"]), "u1")).toBe(false);
    expect(canRead(null, "u1")).toBe(false);
  });
});

describe("buildTree", () => {
  const files = ["a/x.py", "a/b/y.py", "a/b/z.py", "top.md"].map(file);
  const tree = buildTree(files);

  it("derives folders and files from the real paths only", () => {
    expect(tree.children.map((c) => c.name)).toEqual(["a", "top.md"]); // folders first
    expect(findNode(tree, "a/b")?.files.map((f) => f.path)).toEqual(["a/b/y.py", "a/b/z.py"]);
    expect(findNode(tree, "a")?.files).toHaveLength(3);
    expect(findNode(tree, "missing")).toBeNull();
    expect(findNode(tree, "")?.files).toHaveLength(4);
  });
});

describe("coverage", () => {
  const tree = buildTree(["pub/a.md", "pub/b.md", "hr/c.md"].map(file));
  const rules = [rule("pub", "people", ["u1"]), rule("pub/b.md", "admin")];

  it("is all, partial or none depending on the files inside", () => {
    expect(coverage(findNode(tree, "pub/a.md")!, rules, "u1")).toBe("all");
    expect(coverage(findNode(tree, "pub")!, rules, "u1")).toBe("partial"); // b.md has its own rule
    expect(coverage(findNode(tree, "hr")!, rules, "u1")).toBe("none");
    expect(coverage(findNode(tree, "pub")!, rules, "u2")).toBe("none");
  });
});

describe("readers and ruleFor (what a Matrix click writes)", () => {
  it("lists members who can read through the governing rule", () => {
    expect(readers(rule("a", "everyone"), ["u1", "u2"])).toEqual(["u1", "u2"]);
    expect(readers(rule("a", "people", ["u1", "gone"]), ["u1", "u2"])).toEqual(["u1"]);
    expect(readers(rule("a", "admin"), ["u1"])).toEqual([]);
    expect(readers(null, ["u1"])).toEqual([]);
  });

  it("writes an admin-only rule when nobody is left", () => {
    expect(ruleFor("a", [])).toEqual({ path: "a", visibility: "admin", user_ids: [] });
    expect(ruleFor("a", ["u1"])).toEqual({ path: "a", visibility: "people", user_ids: ["u1"] });
  });
});
