// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatEvent, chatApi } from "../../api/chat";
import SelectionAnswer from "./SelectionAnswer";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function* events(...list: ChatEvent[]) { for (const e of list) yield e; }

const props = { fileId: "f1", path: "a/b.py", question: "Explain this", line_start: 2, line_end: 4, left: 10, top: 10, onClose: () => undefined };

describe("select and ask answer window", () => {
  it("streams the answer for the selected lines without saving anything", async () => {
    const ask = vi.spyOn(chatApi, "askLines").mockReturnValue(events(
      { type: "token", text: "It validates " }, { type: "token", text: "the token." },
      { type: "sources", sources: [] }, { type: "done" },
    ));
    render(<SelectionAnswer {...props} />);
    expect(await screen.findByText(/It validates the token\./)).toBeTruthy();
    expect(ask).toHaveBeenCalledWith("f1", "Explain this", { line_start: 2, line_end: 4 }, expect.anything());
    expect(screen.getByRole("dialog", { name: /a\/b\.py, lines 2-4/ })).toBeTruthy();
  });

  it("asks a follow-up about the same lines and offers Open in chat", async () => {
    const ask = vi.spyOn(chatApi, "askLines").mockImplementation(() => events({ type: "token", text: "ok" }, { type: "done" }));
    const openInChat = vi.fn();
    render(<SelectionAnswer {...props} onOpenInChat={openInChat} />);
    await screen.findByText("ok");
    await userEvent.type(screen.getByLabelText(/another question/i), "and why?{Enter}");
    await waitFor(() => expect(ask).toHaveBeenLastCalledWith("f1", "and why?", { line_start: 2, line_end: 4 }, expect.anything()));
    await userEvent.click(screen.getByRole("button", { name: /open in chat/i }));
    expect(openInChat).toHaveBeenCalledWith("and why?");
  });

  it("shows a generic error with Retry", async () => {
    vi.spyOn(chatApi, "askLines").mockReturnValue(events({ type: "error", message: "x" }));
    render(<SelectionAnswer {...props} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});
