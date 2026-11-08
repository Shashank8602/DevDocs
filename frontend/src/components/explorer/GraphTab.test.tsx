// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import GraphTab, { fit } from "./GraphTab";

const paths = [
  "proj/backend/app/main.py", "proj/backend/app/models.py", "proj/backend/app/db.py",
  "proj/backend/tests/test_main.py", "proj/frontend/src/App.tsx", "proj/frontend/src/api.ts",
];
const nodes = paths.map((path, i) => ({ id: `f${i}`, path, language: null, imports: 0, imported_by: 0 }));
const edge = (a: number, b: number) => ({ source: `f${a}`, target: `f${b}` });

vi.mock("../../api/insights", () => ({
  insightsApi: {
    graph: vi.fn(async () => ({
      nodes, edges: [edge(0, 1), edge(1, 2), edge(0, 2), edge(3, 0), edge(4, 5)], files_without_links: 2, note: "Static imports only.",
    })),
    impact: vi.fn(async () => ({
      path: paths[1], imports: [paths[2]], imported_by: [paths[0]], affected: [paths[0], paths[3]],
    })),
  },
}));

afterEach(cleanup);
const mount = (onOpenFile = vi.fn(), onAsk = vi.fn()) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <GraphTab projectId="p" onAsk={onAsk} onOpenFile={onOpenFile} />
    </QueryClientProvider>,
  );

describe("Map", () => {
  it("starts at folder level, hides tests, and drills into a folder", async () => {
    const user = userEvent.setup();
    mount();
    const svg = await screen.findByRole("img", { name: /import map/i });
    // folders, not 6 files; the test folder is hidden by default
    expect(within(svg).getByRole("button", { name: /backend\/app/ })).toBeTruthy();
    expect(within(svg).queryByRole("button", { name: /tests/ })).toBeNull();

    await user.click(within(svg).getByRole("button", { name: /backend\/app/ }));
    expect(screen.getByRole("heading", { name: "backend/app" })).toBeTruthy(); // side panel header
    await user.click(screen.getByRole("button", { name: /show files in this folder/i }));
    const files = await screen.findByRole("img", { name: /import map/i });
    expect(within(files).getByRole("button", { name: /main\.py/ })).toBeTruthy();
    expect(within(files).getByRole("button", { name: /models\.py/ })).toBeTruthy();
  });

  it("shows imports, imported-by and may-break as bulleted lists for a file", async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    mount(onOpenFile);
    await user.click(await screen.findByRole("button", { name: /backend\/app/ }));
    await user.click(screen.getByRole("button", { name: /show files in this folder/i }));
    await user.click(await screen.findByRole("button", { name: /models\.py/ }));
    // Imports, Imported by, Might break: real bullet lists, not lines of text
    await waitFor(() => expect(document.querySelectorAll("aside ul.list-disc").length).toBe(3));
    await user.click(screen.getByRole("button", { name: "Open file" }));
    expect(onOpenFile).toHaveBeenCalledWith("f1");
  });

  it("double-clicking a folder shows its files, and double-clicking a file opens it", async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    mount(onOpenFile);
    await user.dblClick(await screen.findByRole("button", { name: /backend\/app/ }));
    const files = await screen.findByRole("img", { name: /import map/i });
    expect(within(files).getByRole("button", { name: /models\.py/ })).toBeTruthy(); // now at file level
    expect(onOpenFile).not.toHaveBeenCalled();

    await user.dblClick(within(files).getByRole("button", { name: /models\.py/ }));
    expect(onOpenFile).toHaveBeenCalledWith("f1");
    // the second click of the double-click must not have undone the selection the first one made
    expect(screen.getByRole("heading", { name: "models.py" })).toBeTruthy();
  });

  it("can show test files again", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("img", { name: /import map/i });
    await user.click(screen.getByLabelText(/hide test files/i));
    expect(await screen.findByRole("button", { name: /backend\/tests/ })).toBeTruthy();
  });
});

describe("fit (spreads a small map over its card)", () => {
  const n = (key: string, x: number, y: number) => ({ key, label: key, sub: "", group: "g", paths: [key], x, y });
  // 2 columns (210 wide, 120 apart) and 2 rows in the first column: a natural size of about 600 x 100
  const nodes = [n("a", 16, 16), n("b", 16, 56), n("c", 346, 16)];
  const natural = { width: 572, height: 102 };

  it("grows into a big card: wider gaps, taller rows, centred columns, some zoom", () => {
    const out = fit(nodes, natural, { w: 1200, h: 700 });
    expect(out.scale).toBeGreaterThan(1);
    expect(out.scale).toBeLessThanOrEqual(1.3);
    expect(out.nodes[2].x - out.nodes[0].x).toBeGreaterThan(346 - 16); // columns moved apart
    expect(out.nodes[1].y - out.nodes[0].y).toBeGreaterThan(40); // rows spread out
    expect(out.nodes[2].y).toBeGreaterThan(100); // the one-node column is centred, not stuck at the top
    expect(out.width).toBeGreaterThanOrEqual(1200);
    expect(out.height).toBeGreaterThanOrEqual(700);
  });

  it("leaves a map that is already bigger than the card alone, and does nothing before it is measured", () => {
    const small = fit(nodes, natural, { w: 300, h: 60 });
    expect(small.scale).toBe(1);
    expect(small.nodes[2].x - small.nodes[0].x).toBe(346 - 16); // gaps are not stretched or squeezed
    expect(small.width).toBeGreaterThanOrEqual(natural.width);
    expect(fit(nodes, natural, { w: 0, h: 0 }).nodes).toBe(nodes);
  });
});
