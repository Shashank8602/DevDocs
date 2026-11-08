// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SelectionPopover, useLineSelection } from "./SelectionAsk";

afterEach(cleanup);
// jsdom does not lay anything out: give ranges a position so the popover has somewhere to appear
Range.prototype.getBoundingClientRect = () => ({ left: 10, top: 10, bottom: 30, right: 50, width: 40, height: 20, x: 10, y: 10, toJSON: () => ({}) });

function Harness({ onAsk }: { onAsk: (q: string, s: { line_start: number; line_end: number }) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const { anchor, clear } = useLineSelection(ref, true);
  return (
    <>
      <div ref={ref}>
        {["alpha", "beta", "gamma"].map((t, i) => <div key={t} data-line={i + 1}><span>{i + 1}</span><span data-testid={t}>{t}</span></div>)}
      </div>
      {anchor && <SelectionPopover anchor={anchor} path="a/b.py" onClose={clear} onAsk={onAsk} />}
    </>
  );
}

function selectLines(from: string, to: string) {
  const range = document.createRange();
  range.setStart(screen.getByTestId(from).firstChild!, 0);
  range.setEnd(screen.getByTestId(to).firstChild!, 2);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
}

describe("select and ask", () => {
  it("reads the selected line range and sends a quick question with it", async () => {
    const onAsk = vi.fn();
    render(<Harness onAsk={onAsk} />);
    selectLines("alpha", "gamma");
    fireEvent.mouseUp(screen.getByTestId("alpha"));
    const dialog = await screen.findByRole("dialog", { name: /a\/b\.py, lines 1-3/ });
    expect(dialog).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Explain this" }));
    expect(onAsk).toHaveBeenCalledWith("Explain this", { line_start: 1, line_end: 3 });
  });

  it("sends a typed question and ignores a selection outside the code", async () => {
    const onAsk = vi.fn();
    render(<Harness onAsk={onAsk} />);
    selectLines("beta", "beta");
    fireEvent.mouseUp(screen.getByTestId("beta"));
    const input = await screen.findByLabelText(/your question/i);
    await userEvent.type(input, "why?{Enter}");
    expect(onAsk).toHaveBeenCalledWith("why?", { line_start: 2, line_end: 2 });

    window.getSelection()!.removeAllRanges();
    fireEvent.mouseUp(screen.getByTestId("beta"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
