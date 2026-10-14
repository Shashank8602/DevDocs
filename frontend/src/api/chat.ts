const BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export interface SourceRef {
  file_id: string;
  file_path: string;
  file_type: "doc" | "source" | "other" | null;
  ranges: [number, number][];
  label: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources_json: SourceRef[] | null;
  gen_state: "pending" | "answered" | "failed" | "stopped" | "dismissed" | null;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  project_id: string;
  messages: ChatMessage[];
}

/** Events of the answer stream: `conversation` -> [`waiting`] `token`* -> `sources` -> `done` | `no_context` | `error`. */
export type ChatEvent =
  | { type: "conversation"; conversation_id: string; title: string; user_message_id: string }
  | { type: "waiting"; seconds: number }
  | { type: "token"; text: string }
  | { type: "sources"; sources: SourceRef[] }
  | { type: "done"; message_id: string }
  | { type: "no_context"; message_id: string; content: string }
  | { type: "error"; message: string };

export interface ContextInspection {
  turn: {
    question: string;
    retrieval_query: string;
    messages: { role: string; content: string }[];
    chunks: { file_path: string; line_start: number | null; line_end: number | null; tokens: number; vector_score: number | null }[];
    summary_used: boolean;
    prompt_tokens_estimate: number;
    created_at: string;
  } | null;
  summary: string | null;
  recent_token_estimate: number;
  summary_threshold_tokens: number;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error("Something went wrong. Please try again.");
  return res.json();
}

/** POSTs and yields the SSE events as they arrive. Throws on a non-2xx response; abort with `signal`. */
export async function* streamPost(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(String(res.status));
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const name = /^event: (.+)$/m.exec(block)?.[1];
        const data = /^data: (.+)$/m.exec(block)?.[1];
        if (name && data) yield { type: name, ...JSON.parse(data) } as ChatEvent;
      }
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

export const chatApi = {
  conversations: (projectId: string) => getJson<ConversationSummary[]>(`/projects/${projectId}/conversations`),
  conversation: (id: string) => getJson<ConversationDetail>(`/conversations/${id}`),
  context: (id: string) => getJson<ContextInspection>(`/conversations/${id}/context`),
  start: (projectId: string, content: string, signal?: AbortSignal) =>
    streamPost(`/projects/${projectId}/conversations`, { content }, signal),
  send: (conversationId: string, content: string, signal?: AbortSignal) =>
    streamPost(`/conversations/${conversationId}/messages`, { content }, signal),
  retry: (messageId: string, signal?: AbortSignal) => streamPost(`/messages/${messageId}/retry`, undefined, signal),
  dismiss: async (messageId: string) => {
    await fetch(`${BASE}/messages/${messageId}/dismiss`, { method: "POST" });
  },
  remove: async (conversationId: string) => {
    const res = await fetch(`${BASE}/conversations/${conversationId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Couldn't delete the conversation.");
  },
};
