import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowUp, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { streamChat } from "../../lib/streamChat";
import type { Message } from "../../lib/types";
import { MessageBubble } from "./MessageBubble";
import { ModelPicker, type ModelSelection } from "./ModelPicker";

interface ConversationProps {
  chatId: string;
}

/**
 * A chat transcript plus its composer.
 *
 * Two pieces of state exist only while a reply is arriving: the text streamed
 * so far, and the user's turn rendered optimistically. Both are cleared the
 * moment the server's persisted messages land, so the transcript on screen is
 * always the server's, never a local reconstruction that could drift from it.
 */
export function Conversation({ chatId }: ConversationProps) {
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<ModelSelection | null>(null);
  const [draft, setDraft] = useState("");
  const [pendingUser, setPendingUser] = useState<Message | null>(null);
  const [streamed, setStreamed] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const messages = useQuery({
    queryKey: ["chats", chatId, "messages"],
    queryFn: () => api.listMessages(chatId),
  });

  const streaming = streamed !== null;
  const canSend = draft.trim().length > 0 && Boolean(selection?.model) && !streaming;

  // Follow the tail as tokens arrive, the way a terminal does.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages.data, streamed]);

  // A pending turn belongs to one chat; switching away must not carry it over.
  useEffect(() => {
    abortRef.current?.abort();
    setPendingUser(null);
    setStreamed(null);
    setError(null);
  }, [chatId]);

  const send = useCallback(async () => {
    if (!canSend || !selection) return;

    const content = draft.trim();
    setDraft("");
    setError(null);
    setStreamed("");

    const controller = new AbortController();
    abortRef.current = controller;

    await streamChat(
      chatId,
      { content, provider: selection.provider, model: selection.model },
      {
        signal: controller.signal,
        onStart: (userMessage) => setPendingUser(userMessage),
        onDelta: (text) => setStreamed((current) => (current ?? "") + text),
        onError: (detail) => setError(detail),
      },
    );

    abortRef.current = null;
    setStreamed(null);
    setPendingUser(null);
    // The server is the source of truth for the transcript, including the
    // failed-turn row, so refetch rather than splicing the reply in locally.
    await queryClient.invalidateQueries({ queryKey: ["chats", chatId, "messages"] });
  }, [canSend, chatId, draft, queryClient, selection]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const history = messages.data ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto" data-testid="transcript">
        {history.length === 0 && !pendingUser && (
          <p className="px-4 py-6 text-sm text-text-secondary">
            Ask for an analysis and the results build up in the canvas.
          </p>
        )}

        {history.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {pendingUser && <MessageBubble key={pendingUser.id} message={pendingUser} />}

        {streaming && (
          <MessageBubble
            message={placeholderAssistant(chatId, selection)}
            streamingText={streamed ?? ""}
          />
        )}

        <div ref={bottomRef} />
      </div>

      {error && (
        <p
          role="alert"
          className="mx-4 mb-2 flex items-start gap-2 rounded border border-negative/40 bg-negative/5 px-3 py-2 text-xs text-negative"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {error}
        </p>
      )}

      <form
        className="border-t border-border bg-surface-0 p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <ModelPicker value={selection} onChange={setSelection} disabled={streaming} />
        </div>

        <div className="flex items-end gap-2">
          <textarea
            aria-label="Message"
            rows={2}
            value={draft}
            disabled={streaming}
            placeholder="Test whether Bitcoin follows a random walk…"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter breaks the line — the convention
              // every chat interface has trained people to expect.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            className={cn(
              "scroll-thin min-h-0 flex-1 resize-none rounded border border-border",
              "bg-surface-1 px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary",
              "focus:border-accent focus:outline-none disabled:opacity-60",
            )}
          />

          {streaming ? (
            <button
              type="button"
              onClick={stop}
              aria-label="Stop generating"
              className="flex size-9 shrink-0 items-center justify-center rounded bg-surface-2 text-text-primary hover:bg-surface-2/70"
            >
              <Square className="size-3.5 fill-current" aria-hidden />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!canSend}
              aria-label="Send message"
              className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded",
                "bg-accent text-white disabled:cursor-not-allowed disabled:opacity-40",
              )}
            >
              <ArrowUp className="size-4" aria-hidden />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

/** A stand-in for the assistant turn while its text is still arriving. */
function placeholderAssistant(chatId: string, selection: ModelSelection | null): Message {
  return {
    id: "streaming",
    chat_id: chatId,
    seq: Number.MAX_SAFE_INTEGER,
    role: "assistant",
    content: "",
    provider: selection?.provider ?? null,
    model: selection?.model ?? null,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    latency_ms: 0,
    stop_reason: null,
    error: null,
    created_at: new Date().toISOString(),
  };
}
