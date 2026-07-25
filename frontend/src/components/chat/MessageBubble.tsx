import { AlertTriangle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "../../lib/cn";
import type { Message } from "../../lib/types";

interface MessageBubbleProps {
  message: Message;
  /** Text arriving right now, before the finished message is persisted. */
  streamingText?: string;
}

/**
 * One turn.
 *
 * Assistant turns show their provenance — provider, model, tokens, latency —
 * because the model can change between turns, so "which model said this" is
 * per-message information that nothing else in the UI can answer.
 *
 * Markdown is rendered without raw HTML: this is model output, and the one
 * thing it must never do is inject markup into the page.
 */
export function MessageBubble({ message, streamingText }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const body = streamingText ?? message.content;
  const failed = Boolean(message.error);

  return (
    <article
      aria-label={`${message.role} message`}
      className={cn("flex flex-col gap-1.5 px-4 py-3", isUser && "items-end")}
    >
      <div
        className={cn(
          "max-w-[46rem] rounded-lg px-3.5 py-2.5 text-sm leading-relaxed",
          isUser
            ? "bg-accent/10 text-text-primary"
            : "bg-surface-2 text-text-primary",
          failed && "border border-negative/40 bg-negative/5",
        )}
      >
        {failed ? (
          <p className="flex items-start gap-2 text-negative">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>{message.error}</span>
          </p>
        ) : body ? (
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
          </div>
        ) : (
          <span className="text-text-secondary italic" aria-label="Thinking">
            …
          </span>
        )}
      </div>

      {!isUser && !streamingText && (message.model || message.output_tokens > 0) && (
        <Provenance message={message} />
      )}
    </article>
  );
}

function Provenance({ message }: { message: Message }) {
  const parts: string[] = [];
  if (message.model) parts.push(message.model);
  if (message.output_tokens > 0) {
    parts.push(`${message.input_tokens} in / ${message.output_tokens} out`);
  }
  if (message.latency_ms > 0) parts.push(`${(message.latency_ms / 1000).toFixed(1)}s`);

  if (parts.length === 0) return null;

  return (
    <p className="font-mono text-[11px] text-text-secondary" data-testid="provenance">
      {parts.join(" · ")}
    </p>
  );
}
