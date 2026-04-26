"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  IconLoader2,
  IconMicrophone,
  IconMicrophoneOff,
  IconPaperclip,
  IconPlayerStop,
  IconSend,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useUploadAttachment } from "@/hooks/use-attachments";
import { cn } from "@/lib/utils";
import { AttachmentView, type AttachmentRef } from "./AttachmentView";

const DEFAULT_MAX_MB = 25;
const ALL_ACCEPT =
  "image/jpeg,image/png,image/gif,image/webp,audio/*,.pdf,.txt,.md,.csv,.json,.py,.js,.ts,.tsx,.html,.css,.yaml,.yml,.toml,.xml,.sql,.sh,.docx,.xlsx";

export interface ChatInputHandle {
  focus: () => void;
  clear: () => void;
}

interface ChatInputProps {
  /** Session id used to scope attachment uploads to a session. Optional. */
  sessionId?: string | null;
  /** True while the assistant is mid-stream — disables send + shows stop button. */
  isStreaming?: boolean;
  /** True when the websocket is reachable. Disables send when false. */
  isConnected?: boolean;
  /** Called with the trimmed text + (full) attachment refs on submit. */
  onSend: (text: string, attachments: AttachmentRef[]) => void;
  /** Optional cancel handler — wires to WS `cancel` frame. */
  onCancel?: () => void;
  className?: string;
}

/**
 * `ChatInput` — composer with autosize textarea, multi-file upload + voice
 * input. Mirrors the reference implementation's UX while staying inside the
 * SenHarness theme + attachment API (`POST /attachments`).
 */
export const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(
  function ChatInput(
    { sessionId, isStreaming, isConnected = true, onSend, onCancel, className },
    ref,
  ) {
    const t = useTranslations("chat");
    const upload = useUploadAttachment(sessionId);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

    const [message, setMessage] = useState("");
    const [pending, setPending] = useState<AttachmentRef[]>([]);
    const [isListening, setIsListening] = useState(false);

    const maxMb =
      parseInt(process.env.NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB ?? "", 10) ||
      DEFAULT_MAX_MB;

    useImperativeHandle(ref, () => ({
      focus: () => textareaRef.current?.focus(),
      clear: () => {
        setMessage("");
        setPending([]);
      },
    }));

    // Autosize the textarea to fit content (cap ~200px).
    useEffect(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    }, [message]);

    // Auto-focus when streaming finishes (so user can immediately type next prompt).
    useEffect(() => {
      if (!isStreaming && !upload.isPending) {
        textareaRef.current?.focus();
      }
    }, [isStreaming, upload.isPending]);

    const submit = useCallback(() => {
      const trimmed = message.trim();
      if ((!trimmed && pending.length === 0) || !isConnected || isStreaming) return;
      onSend(
        trimmed || (pending.length ? "Analyse the attached file(s)" : ""),
        pending,
      );
      setMessage("");
      setPending([]);
    }, [message, pending, onSend, isConnected, isStreaming]);

    const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        submit();
      }
    };

    // Multi-file upload with size validation + per-file error toast.
    const onFilesChosen = useCallback(
      async (files: FileList | null) => {
        if (!files || files.length === 0) return;
        for (const file of Array.from(files)) {
          if (file.size > maxMb * 1024 * 1024) {
            toast.error(`${file.name}: ${t("uploadFailed")}`);
            continue;
          }
          try {
            const att = await upload.mutateAsync(file);
            setPending((prev) => [
              ...prev,
              {
                id: att.id,
                filename: att.filename,
                mime_type: att.mime_type,
                kind: att.kind,
                size_bytes: att.size_bytes,
              },
            ]);
          } catch (err) {
            const msg = (err as Error).message ?? t("uploadFailed");
            toast.error(`${file.name}: ${msg}`);
          }
        }
        if (fileInputRef.current) fileInputRef.current.value = "";
      },
      [maxMb, t, upload],
    );

    const removePending = (id: string) =>
      setPending((prev) => prev.filter((a) => a.id !== id));

    // Voice input via Web Speech API. Chrome / Edge only; gracefully toasts
    // on unsupported browsers. Uses interim results for live feedback.
    const toggleMic = useCallback(() => {
      if (isListening) {
        recognitionRef.current?.stop();
        setIsListening(false);
        return;
      }
      const SR =
        (window as unknown as { SpeechRecognition?: SpeechRecognitionCtor })
          .SpeechRecognition ||
        (window as unknown as { webkitSpeechRecognition?: SpeechRecognitionCtor })
          .webkitSpeechRecognition;
      if (!SR) {
        toast.info("Voice input is only supported in Chromium browsers.");
        return;
      }
      const recognition = new SR();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = navigator.language || "en-US";

      let finalTranscript = message;
      recognition.onresult = (event: SpeechRecognitionEventLike) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (!result) continue;
          const transcript = result[0]?.transcript ?? "";
          if (result.isFinal) {
            finalTranscript += transcript;
          } else {
            interim += transcript;
          }
        }
        // Use a zero-width sentinel to mark interim segment so we can strip
        // it on `onend` — keeps the textarea reactive without flicker.
        setMessage(finalTranscript + (interim ? "\u200B" + interim : ""));
      };
      recognition.onend = () => {
        setIsListening(false);
        setMessage((prev) => prev.replace(/\u200B/g, ""));
      };
      recognition.onerror = () => {
        setIsListening(false);
        toast.error("Speech recognition error");
      };
      recognitionRef.current = recognition;
      recognition.start();
      setIsListening(true);
    }, [isListening, message]);

    const canSend =
      isConnected &&
      !isStreaming &&
      !upload.isPending &&
      (message.trim().length > 0 || pending.length > 0);

    return (
      <div className={cn("border-t p-3", className)}>
        <div className="mx-auto flex max-w-3xl flex-col gap-2">
          {(pending.length > 0 || upload.isPending) && (
            <div className="flex flex-wrap items-start gap-2">
              {pending.map((a) => (
                <AttachmentView
                  key={a.id}
                  att={a}
                  compact
                  onRemove={() => removePending(a.id)}
                />
              ))}
              {upload.isPending && (
                <div className="flex h-20 w-20 items-center justify-center rounded-md border border-dashed">
                  <IconLoader2 className="size-4 animate-spin sh-muted" />
                </div>
              )}
            </div>
          )}

          <div className="flex items-end gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ALL_ACCEPT}
              className="hidden"
              onChange={(e) => onFilesChosen(e.target.files)}
            />

            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t("attach")}
              title={t("attach")}
              onClick={() => fileInputRef.current?.click()}
              disabled={upload.isPending || !isConnected}
            >
              {upload.isPending ? (
                <IconLoader2 className="size-4 animate-spin" />
              ) : (
                <IconPaperclip className="size-4" />
              )}
            </Button>

            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={isListening ? "Stop voice input" : "Voice input"}
              title={isListening ? "Stop voice input" : "Voice input"}
              onClick={toggleMic}
              disabled={!isConnected}
            >
              {isListening ? (
                <IconMicrophoneOff className="size-4 animate-pulse text-red-500" />
              ) : (
                <IconMicrophone className="size-4 sh-muted" />
              )}
            </Button>

            <Textarea
              ref={textareaRef}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={t("inputPlaceholder")}
              className="min-h-[44px] max-h-[200px] flex-1 resize-none"
              data-testid="chat-input"
              rows={1}
              disabled={!isConnected}
            />

            {isStreaming && onCancel ? (
              <Button
                type="button"
                size="icon"
                variant="destructive"
                onClick={onCancel}
                aria-label="Stop generating"
                title="Stop generating"
                data-testid="chat-cancel"
              >
                <IconPlayerStop className="size-4" />
              </Button>
            ) : (
              <Button
                type="button"
                size="icon"
                onClick={submit}
                disabled={!canSend}
                aria-label="Send"
                data-testid="chat-send"
              >
                {upload.isPending ? (
                  <IconLoader2 className="size-4 animate-spin" />
                ) : (
                  <IconSend className="size-4" />
                )}
              </Button>
            )}
          </div>
        </div>
      </div>
    );
  },
);

// ────────────────────────────────────────────────────────────
// Light Web Speech API typings (stable across browsers).
// ────────────────────────────────────────────────────────────

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onend: (() => void) | null;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    [index: number]: { transcript: string };
  }>;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;
