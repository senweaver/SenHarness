"use client";

/**
 * ChatInput — composer surface.
 *
 * Composes the AI Elements `PromptInput*` primitives with SenHarness-specific
 * tools: attachments, voice input, mode selector, slash command palette,
 * and "@" mention palette.
 *
 * The component is "controlled-from-outside": the parent owns the value /
 * status / submit handler. We deliberately avoid making it pull from
 * ``useChat`` directly so the same composer can be reused on the new-chat
 * draft surface (where ``useChat`` is not mounted yet).
 */

import {
  IconChevronDown,
  IconLoader2,
  IconMicrophone,
  IconMicrophoneOff,
  IconPaperclip,
  IconPuzzle,
  IconWand,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import {
  MentionPalette,
  ModelSelector,
  PromptInput,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
  PromptInputTools,
  SlashCommandPalette,
  type MentionItem,
  type PaletteHandle,
  type PromptInputStatus,
  type SlashItem,
} from "@/components/ai-elements";
import { ComposerOverlay } from "@/components/chat/ComposerOverlay";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  useAgentModels,
  useChatModelPrefs,
  useSetChatModelPref,
} from "@/hooks/use-agent-models";
import { useAgentSkills } from "@/hooks/use-agent-skills";
import { useAgents } from "@/hooks/use-agents";
import { useUploadAttachment } from "@/hooks/use-attachments";
import { useCollections } from "@/hooks/use-knowledge";
import type { ChatMode } from "@/lib/ws";
import { cn } from "@/lib/utils";
import { AttachmentView, type AttachmentRef } from "./AttachmentView";

const DEFAULT_MAX_MB = 25;
const ALL_ACCEPT =
  "image/jpeg,image/png,image/gif,image/webp,audio/*,.pdf,.txt,.md,.csv,.json,.py,.js,.ts,.tsx,.html,.css,.yaml,.yml,.toml,.xml,.sql,.sh,.docx,.xlsx";

const MODES: { id: ChatMode; labelKey: string; descKey: string }[] = [
  { id: "flash", labelKey: "modeFlash", descKey: "modeFlashDesc" },
  { id: "thinking", labelKey: "modeThinking", descKey: "modeThinkingDesc" },
  { id: "plan", labelKey: "modePlan", descKey: "modePlanDesc" },
  { id: "subagent", labelKey: "modeSubagent", descKey: "modeSubagentDesc" },
];

export interface ChatInputHandle {
  focus: () => void;
  clear: () => void;
  /** Current composer mode (for draft surfaces that submit outside the input). */
  getMode: () => ChatMode;
  /** Current ``provider:model`` selection (or null = agent default). */
  getModel: () => string | null;
}

export interface ChatInputSubmission {
  text: string;
  attachments: AttachmentRef[];
  mode: ChatMode;
  /**
   * Per-turn ``provider:model`` override. ``null`` means "use the agent's
   * default" — the backend then falls back to the user's saved preference.
   */
  model: string | null;
}

interface ChatInputProps {
  sessionId?: string | null;
  /** Active agent for the session — drives slash menu (skills) + mention list. */
  agentId?: string | null;
  /** AI SDK status: "ready" | "submitted" | "streaming" | "error". */
  status?: PromptInputStatus;
  /** True when the websocket is reachable. Disables send when false. */
  isConnected?: boolean;
  /** Submit handler. Receives the trimmed text + selected mode + attachments. */
  onSend: (submission: ChatInputSubmission) => void;
  /** Cancel handler — called when the user clicks Stop while streaming. */
  onCancel?: () => void;
  /** Re-run the previous user turn. Bound to ``useChat().regenerate``
   *  by the chat page; surfaces in the slash palette as ``/regenerate``
   *  under the Commands group. Optional — composer remains usable on
   *  the new-chat draft surface where there is no turn to regenerate. */
  onRegenerate?: () => void;
  className?: string;
  /** Initial mode; defaults to ``flash``. */
  initialMode?: ChatMode;
}

export const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(
  function ChatInput(
    {
      sessionId,
      agentId,
      status = "ready",
      isConnected = true,
      onSend,
      onCancel,
      onRegenerate,
      className,
      initialMode = "flash",
    },
    ref,
  ) {
    const t = useTranslations("chat");
    const tCompose = useTranslations("chat.compose");
    const upload = useUploadAttachment(sessionId ?? null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

    const [message, setMessage] = useState("");
    const [pending, setPending] = useState<AttachmentRef[]>([]);
    const [isListening, setIsListening] = useState(false);
    const [mode, setMode] = useState<ChatMode>(initialMode);
    // Mirrored from the textarea so the inline highlight overlay
    // (``ComposerOverlay``) stays glyph-aligned during long edits that
    // cause internal scrolling.
    const [scrollOffset, setScrollOffset] = useState({ top: 0, left: 0 });
    // Per-agent model override. ``null`` means "use whatever the user saved
    // (or the agent default)". We seed this from the saved prefs once
    // they load and again whenever ``agentId`` changes.
    const [model, setModel] = useState<string | null>(null);
    const prefsQuery = useChatModelPrefs();
    const modelsQuery = useAgentModels(agentId ?? null);
    const setPrefMutation = useSetChatModelPref();
    const prefsSeededForAgentRef = useRef<string | null>(null);
    useEffect(() => {
      if (!agentId) {
        setModel(null);
        prefsSeededForAgentRef.current = null;
        return;
      }
      if (prefsQuery.isLoading || modelsQuery.isLoading) return;
      if (prefsSeededForAgentRef.current === agentId) return;
      prefsSeededForAgentRef.current = agentId;
      const prefs = prefsQuery.data?.prefs ?? {};
      const fromPrefs = prefs[agentId] ?? prefs["default"] ?? null;
      if (fromPrefs) {
        setModel(fromPrefs);
        return;
      }
      const catalogDefault = modelsQuery.data?.default_model;
      setModel(catalogDefault ?? null);
    }, [
      agentId,
      prefsQuery.isLoading,
      prefsQuery.data,
      modelsQuery.isLoading,
      modelsQuery.data?.default_model,
    ]);

    const onPickModel = useCallback(
      (next: string | null) => {
        setModel(next);
        // Persist to user prefs so the next visit pre-selects the same row.
        // Fire-and-forget — the dropdown stays usable even if the backend
        // is briefly unavailable.
        if (agentId) {
          setPrefMutation.mutate({ agentId, model: next });
        }
      },
      [agentId, setPrefMutation],
    );

    // ─── Slash / mention palette state ──────────────────────
    // ``trigger`` records *which* palette is open and where in the
    // textarea the user typed the trigger character so we can replace
    // the partial token cleanly when an item is picked.
    const [trigger, setTrigger] = useState<
      | null
      | {
          kind: "slash" | "mention";
          /** Cursor index where the trigger char (``/`` or ``@``) lives. */
          start: number;
          /** Search query — characters between the trigger and the cursor. */
          query: string;
        }
    >(null);
    // Highlighted row id is owned here (parent-controlled palette) so
    // the textarea's onKeyDown can drive ↑/↓/Tab/Enter without focus
    // ever leaving the editor. Null means "first item is implicitly
    // highlighted" — Tab/Enter still accepts in that case via
    // ``acceptHighlighted()`` below.
    const [highlightedId, setHighlightedId] = useState<string | null>(null);
    const slashPaletteRef = useRef<PaletteHandle | null>(null);
    const mentionPaletteRef = useRef<PaletteHandle | null>(null);

    // Pull data the palettes need; both hooks gracefully handle missing ids.
    const skillsQuery = useAgentSkills(agentId ?? null);
    const agentsQuery = useAgents();
    const collectionsQuery = useCollections();

    const isStreaming = status === "streaming" || status === "submitted";
    const maxMb =
      parseInt(process.env.NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB ?? "", 10) ||
      DEFAULT_MAX_MB;

    useImperativeHandle(
      ref,
      () => ({
        focus: () => textareaRef.current?.focus(),
        clear: () => {
          setMessage("");
          setPending([]);
          setTrigger(null);
        },
        getMode: () => mode,
        getModel: () => model,
      }),
      [mode, model],
    );

    useEffect(() => {
      if (!isStreaming && !upload.isPending) {
        textareaRef.current?.focus();
      }
    }, [isStreaming, upload.isPending]);

    // Whenever the palette closes or the trigger query changes, reset
    // the highlight so the next opening starts clean. Without this the
    // popover would re-open with a stale "selected" row that may have
    // scrolled out of the filtered list.
    useEffect(() => {
      setHighlightedId(null);
    }, [trigger?.kind, trigger?.query]);

    /** Recompute palette state from the current text + caret. Called from
     *  ``onChange`` and ``onSelect`` of the textarea so the popover tracks
     *  the cursor.
     */
    const recomputeTrigger = useCallback(
      (value: string, caret: number) => {
        const before = value.slice(0, caret);
        // Slash trigger: must sit at the very start of the message OR after
        // a newline (we don't pop the palette mid-sentence).
        const slashMatch = /(^|\n)\/([^\s/@]*)$/.exec(before);
        if (slashMatch) {
          setTrigger({
            kind: "slash",
            start: caret - slashMatch[2]!.length - 1,
            query: slashMatch[2] ?? "",
          });
          return;
        }
        // Mention trigger: ``@`` after whitespace or at the start.
        const mentionMatch = /(^|\s)@([^\s@/]*)$/.exec(before);
        if (mentionMatch) {
          setTrigger({
            kind: "mention",
            start: caret - mentionMatch[2]!.length - 1,
            query: mentionMatch[2] ?? "",
          });
          return;
        }
        setTrigger(null);
      },
      [],
    );

    /** Replace the trigger token (``/x`` or ``@y``) with ``token`` plus a
     *  trailing space so the user can keep typing right after.
     */
    const replaceTrigger = useCallback(
      (replacement: string) => {
        const ta = textareaRef.current;
        if (!ta || !trigger) return;
        const caret = ta.selectionStart ?? message.length;
        const next =
          message.slice(0, trigger.start) +
          replacement +
          (message[caret] === " " ? "" : " ") +
          message.slice(caret);
        setMessage(next);
        setTrigger(null);
        // Restore caret right after the inserted token.
        const nextCaret = trigger.start + replacement.length + 1;
        // ``setSelectionRange`` must happen after the value lands.
        requestAnimationFrame(() => {
          ta.focus();
          try {
            ta.setSelectionRange(nextCaret, nextCaret);
          } catch {
            /* selection lost — non-fatal */
          }
        });
      },
      [message, trigger],
    );

    /** Build the slash palette list — Skills + Commands.
     *
     *  ``/plan`` and ``/research`` were intentionally dropped from the
     *  palette: they're really *mode* shortcuts and live on the Mode
     *  dropdown, not the slash menu. ``/clear`` and ``/regenerate``
     *  stay because they *do* act on the composer / current turn and
     *  benefit from keyboard discoverability via ``/``. */
    const slashItems = useMemo<SlashItem[]>(() => {
      const out: SlashItem[] = [];
      for (const s of skillsQuery.data ?? []) {
        out.push({
          id: `skill:${s.source}:${s.slug}`,
          token: s.slug,
          label: s.name,
          description: s.description,
          kind:
            s.source === "workspace" ? "skill_workspace" : "skill_bundled",
        });
      }
      out.push({
        id: "command:clear",
        token: "clear",
        label: tCompose("commandClear"),
        description: tCompose("commandClearDesc"),
        kind: "command",
      });
      // Only surface ``/regenerate`` when there's actually a turn to
      // re-run. The new-chat draft surface (no sessionId, no
      // ``onRegenerate``) hides it so the palette doesn't ship a
      // dead-on-arrival action.
      if (onRegenerate) {
        out.push({
          id: "command:regenerate",
          token: "regenerate",
          label: tCompose("commandRegenerate"),
          description: tCompose("commandRegenerateDesc"),
          kind: "command",
        });
      }
      // ``/insights`` only appears once the chat is bound to a real
      // session — the slash is parsed by the backend WS handler and
      // queues an ARQ task that writes back into ``sessionId``.
      if (sessionId) {
        out.push({
          id: "command:insights",
          token: "insights",
          label: tCompose("commandInsights"),
          description: tCompose("commandInsightsDesc"),
          kind: "command",
        });
        // ``/goal`` is parsed in the same backend WS handler — it
        // attaches a session-level goal that drives the alignment dots.
        // Only useful when a session exists.
        out.push({
          id: "command:goal",
          token: "goal",
          label: tCompose("commandGoal"),
          description: tCompose("commandGoalDesc"),
          kind: "command",
        });
      }
      return out;
    }, [skillsQuery.data, tCompose, onRegenerate, sessionId]);

    /** Mention items: agents + knowledge collections. (Files are added by
     *  the caller via attachments; we don't surface scratch files here yet.) */
    const mentionItems = useMemo<MentionItem[]>(() => {
      const out: MentionItem[] = [];
      for (const a of agentsQuery.data ?? []) {
        out.push({
          id: `agent:${a.id}`,
          token: a.name.replace(/\s+/g, "-").toLowerCase(),
          label: a.name,
          description: a.description ?? "",
          group: "agent",
          avatarUrl: a.avatar_url ?? null,
        });
      }
      for (const c of collectionsQuery.data ?? []) {
        out.push({
          id: `kb:${c.id}`,
          token: c.name.replace(/\s+/g, "-").toLowerCase(),
          label: c.name,
          description: c.description ?? "",
          group: "knowledge",
        });
      }
      return out;
    }, [agentsQuery.data, collectionsQuery.data]);

    /** Token whitelists for ``ComposerOverlay`` — only paint a pill
     *  behind ``/x`` / ``@y`` when the slug actually matches a known
     *  skill / agent / knowledge collection. Re-derived from the same
     *  palette items so we never need an extra query. */
    const slashTokenSet = useMemo(
      () => new Set(slashItems.map((i) => i.token)),
      [slashItems],
    );
    const mentionTokenSet = useMemo(
      () => new Set(mentionItems.map((i) => i.token)),
      [mentionItems],
    );

    const onPickSlash = useCallback(
      (item: SlashItem) => {
        if (item.kind === "command" || item.kind === "quick") {
          // Commands act on the composer / chat surface immediately
          // — they're never inserted as text. Drop the ``/cmd`` token
          // the user typed (replaceTrigger("") clears the partial),
          // then dispatch.
          if (item.token === "clear") {
            setMessage("");
            setPending([]);
            setTrigger(null);
            requestAnimationFrame(() => textareaRef.current?.focus());
            return;
          }
          if (item.token === "regenerate") {
            replaceTrigger("");
            onRegenerate?.();
            return;
          }
          // ``/goal`` is the slash command syntax the backend WS handler
          // parses out before any LLM round-trip. Insert the literal
          // ``/goal `` so the user immediately types the goal text after
          // it; no further client-side handling needed.
          // ``/insights`` follows the same pattern (parsed server-side).
          // Unknown command tokens also fall through to text insertion.
          replaceTrigger(`/${item.token}`);
          return;
        }
        // Skills are inserted as ``/<slug>`` so the model + harness can
        // pick them up downstream (the SkillsCapability scans message
        // text for ``/skill_slug`` usages).
        replaceTrigger(`/${item.token}`);
      },
      [replaceTrigger, onRegenerate],
    );

    const onPickMention = useCallback(
      (item: MentionItem) => {
        replaceTrigger(`@${item.token}`);
      },
      [replaceTrigger],
    );

    /** Insert ``/`` at the caret so the existing ``SlashCommandPalette``
     *  pops up — same effect as the user typing ``/`` themselves. Mid-
     *  sentence the slash trigger only fires after a newline, so we prepend
     *  one when the caret isn't sitting at the start of a line. This keeps
     *  the button behaviour identical to the keyboard path. */
    const triggerSlashAtCaret = useCallback(() => {
      const ta = textareaRef.current;
      if (!ta) return;
      const caret = ta.selectionStart ?? message.length;
      const head = message.slice(0, caret);
      const tail = message.slice(caret);
      const needsNewline = head.length > 0 && !head.endsWith("\n");
      const insert = (needsNewline ? "\n/" : "/");
      const next = head + insert + tail;
      setMessage(next);
      const nextCaret = head.length + insert.length;
      requestAnimationFrame(() => {
        ta.focus();
        try {
          ta.setSelectionRange(nextCaret, nextCaret);
        } catch {
          /* selection lost — non-fatal */
        }
        recomputeTrigger(next, nextCaret);
      });
    }, [message, recomputeTrigger]);

    const submit = useCallback(() => {
      const trimmed = message.trim();
      if ((!trimmed && pending.length === 0) || !isConnected || isStreaming) {
        return;
      }
      onSend({
        text: trimmed || (pending.length ? "Analyse the attached file(s)" : ""),
        attachments: pending,
        mode,
        model,
      });
      setMessage("");
      setPending([]);
      setTrigger(null);
    }, [message, pending, mode, model, onSend, isConnected, isStreaming]);

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

    // Web Speech API — Chromium-only, gracefully toasts otherwise. Reused
    // from the previous composer to keep the voice-input affordance.
    //
    // Errors are mapped to localised, actionable copy so users know whether
    // they need to grant a permission, plug in a mic, or come back online.
    const toggleMic = useCallback(() => {
      if (isListening) {
        recognitionRef.current?.stop();
        setIsListening(false);
        return;
      }
      const SR =
        (window as unknown as { SpeechRecognition?: SpeechRecognitionCtor })
          .SpeechRecognition ||
        (window as unknown as {
          webkitSpeechRecognition?: SpeechRecognitionCtor;
        }).webkitSpeechRecognition;
      if (!SR) {
        toast.info(tCompose("voiceUnsupported"));
        return;
      }
      const recognition = new SR();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = navigator.language || "en-US";

      let finalTranscript = message;
      recognition.onresult = (event) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (!result) continue;
          const transcript = result[0]?.transcript ?? "";
          if (result.isFinal) finalTranscript += transcript;
          else interim += transcript;
        }
        setMessage(finalTranscript + (interim ? "\u200B" + interim : ""));
      };
      recognition.onend = () => {
        setIsListening(false);
        setMessage((prev) => prev.replace(/\u200B/g, ""));
      };
      recognition.onerror = (event) => {
        setIsListening(false);
        const code = (event as { error?: string }).error ?? "unknown";
        // ``aborted`` fires when we manually stop() the recognition — that's
        // a user action, not an error, so don't pop a toast.
        if (code === "aborted") return;
        const map: Record<string, string> = {
          "not-allowed": "voiceErrorNotAllowed",
          "service-not-allowed": "voiceErrorNotAllowed",
          "no-speech": "voiceErrorNoSpeech",
          "audio-capture": "voiceErrorAudioCapture",
          network: "voiceErrorNetwork",
          "language-not-supported": "voiceErrorLanguage",
        };
        const key = map[code];
        toast.error(
          key
            ? tCompose(key)
            : tCompose("voiceErrorGeneric", { error: code }),
        );
      };
      recognitionRef.current = recognition;
      try {
        recognition.start();
      } catch (err) {
        // ``InvalidStateError`` fires if start() is called while a previous
        // recognition is still tearing down — treat as a transient hiccup
        // so the button doesn't end up stuck in the listening state.
        setIsListening(false);
        toast.error(
          tCompose("voiceErrorGeneric", {
            error: (err as Error).name ?? "unknown",
          }),
        );
        return;
      }
      setIsListening(true);
    }, [isListening, message, tCompose]);

    const canSubmit =
      isConnected &&
      !isStreaming &&
      !upload.isPending &&
      (message.trim().length > 0 || pending.length > 0);

    return (
      <div className={cn("border-t p-3", className)}>
        <div className="mx-auto max-w-3xl">
          {(pending.length > 0 || upload.isPending) && (
            <div className="mb-2 flex flex-wrap items-start gap-2">
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

          <PromptInput
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ALL_ACCEPT}
              className="hidden"
              onChange={(e) => onFilesChosen(e.target.files)}
            />
            <div className="relative">
              <PromptInputTextarea
                ref={textareaRef}
                value={message}
                onChange={(e) => {
                  setMessage(e.target.value);
                  recomputeTrigger(
                    e.target.value,
                    e.target.selectionStart ?? e.target.value.length,
                  );
                }}
                onKeyDown={(e) => {
                  // Palette key model: ↑/↓ navigate, Tab / Enter accept
                  // the highlighted row, Esc dismiss. We intercept here
                  // so focus stays on the textarea (the palette has no
                  // focused element of its own — it's purely visual,
                  // controlled via the imperative refs below).
                  if (!trigger) return;
                  const handle =
                    trigger.kind === "slash"
                      ? slashPaletteRef.current
                      : mentionPaletteRef.current;
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setTrigger(null);
                    return;
                  }
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    handle?.next();
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    handle?.prev();
                    return;
                  }
                  if (e.key === "Tab") {
                    // Only swallow Tab when the palette can actually
                    // accept something — otherwise let Tab move focus
                    // out of the textarea like normal.
                    if (handle?.acceptHighlighted()) {
                      e.preventDefault();
                    }
                    return;
                  }
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.ctrlKey &&
                    !e.metaKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    // Enter would otherwise submit the form via
                    // PromptInputTextarea's default handler. While the
                    // palette is open, Enter accepts the highlighted
                    // row instead — submission resumes after the user
                    // dismisses the palette or types Enter again.
                    if (handle?.acceptHighlighted()) {
                      e.preventDefault();
                      e.stopPropagation();
                    }
                  }
                }}
                onSelect={(e) => {
                  const ta = e.currentTarget;
                  recomputeTrigger(ta.value, ta.selectionStart ?? 0);
                }}
                onScroll={(e) => {
                  // Mirror scroll into the inline-highlight overlay so the
                  // pills stay glued to their glyphs while a long message
                  // overflows the textarea's max-height.
                  const ta = e.currentTarget;
                  setScrollOffset({
                    top: ta.scrollTop,
                    left: ta.scrollLeft,
                  });
                }}
                placeholder={t("inputPlaceholder")}
                disabled={!isConnected}
              />
              <ComposerOverlay
                value={message}
                slashTokens={slashTokenSet}
                mentionTokens={mentionTokenSet}
                scrollTop={scrollOffset.top}
                scrollLeft={scrollOffset.left}
              />
              {trigger?.kind === "slash" ? (
                <div className="absolute bottom-full left-0 mb-1 w-72">
                  <SlashCommandPalette
                    ref={slashPaletteRef}
                    open
                    query={trigger.query}
                    items={slashItems}
                    onPick={onPickSlash}
                    emptyHint={tCompose("slashEmpty")}
                    kbdHint={tCompose("paletteKbdHint")}
                    highlightedId={highlightedId}
                    onHighlightChange={setHighlightedId}
                    headings={{
                      skills: tCompose("slashGroupSkills"),
                      commands: tCompose("slashGroupCommands"),
                    }}
                    noSkillsHint={{
                      title: tCompose("slashEmptyNoSkills"),
                      description: tCompose("slashEmptyNoSkillsHint"),
                    }}
                  />
                </div>
              ) : null}
              {trigger?.kind === "mention" ? (
                <div className="absolute bottom-full left-0 mb-1 w-72">
                  <MentionPalette
                    ref={mentionPaletteRef}
                    open
                    query={trigger.query}
                    items={mentionItems}
                    onPick={onPickMention}
                    emptyHint={tCompose("mentionEmpty")}
                    kbdHint={tCompose("paletteKbdHint")}
                    highlightedId={highlightedId}
                    onHighlightChange={setHighlightedId}
                  />
                </div>
              ) : null}
            </div>
            <PromptInputToolbar>
              <PromptInputTools>
                <ModeSelect value={mode} onChange={setMode} t={tCompose} />
                <ModelSelector
                  agentId={agentId}
                  value={model}
                  onChange={onPickModel}
                />
                <SlashTriggerButton
                  disabled={!isConnected}
                  loading={skillsQuery.isLoading}
                  t={tCompose}
                  onTrigger={triggerSlashAtCaret}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  aria-label={t("attach")}
                  title={t("attach")}
                  onClick={() => fileInputRef.current?.click()}
                  disabled={upload.isPending || !isConnected}
                >
                  {upload.isPending ? (
                    <IconLoader2 className="size-4 animate-spin" />
                  ) : (
                    <IconPaperclip className="size-3.5" />
                  )}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  aria-label={
                    isListening ? "Stop voice input" : "Voice input"
                  }
                  title={
                    isListening ? "Stop voice input" : "Voice input"
                  }
                  onClick={toggleMic}
                  disabled={!isConnected}
                >
                  {isListening ? (
                    <IconMicrophoneOff className="size-3.5 animate-pulse text-red-500" />
                  ) : (
                    <IconMicrophone className="size-3.5 sh-muted" />
                  )}
                </Button>
              </PromptInputTools>
              <PromptInputSubmit
                status={status}
                disabled={!canSubmit}
                onClick={(e) => {
                  if (isStreaming) {
                    e.preventDefault();
                    onCancel?.();
                  }
                }}
              />
            </PromptInputToolbar>
          </PromptInput>
        </div>
      </div>
    );
  },
);

interface SlashTriggerButtonProps {
  disabled: boolean;
  /** When the per-agent skills query is still in flight we render a tiny
   *  spinner inside the button so users know the palette will fill in
   *  shortly — the click still works, the palette just won't have
   *  workspace skills until the query resolves. */
  loading: boolean;
  t: ReturnType<typeof useTranslations>;
  /** Inserts ``/`` at the textarea caret, which makes the existing
   *  ``recomputeTrigger`` regex match and pops the SlashCommandPalette.
   *  This is intentionally a thin shim over the keyboard path so the
   *  two trigger surfaces (button click vs. typing ``/``) share one
   *  code path and one keyboard model (Tab to accept, Esc to dismiss). */
  onTrigger: () => void;
}

/** Compact slash-command trigger — a single visible button that opens
 *  the same palette the user gets by typing ``/`` at the start of a
 *  line. Replaces the older ``SkillsMenu`` dropdown that listed skills
 *  in a separate Radix surface; consolidating to one palette removes a
 *  parallel-but-different rendering of the same data and gives users a
 *  single, discoverable keyboard model. */
function SlashTriggerButton({
  disabled,
  loading,
  t,
  onTrigger,
}: SlashTriggerButtonProps) {
  // Icon-only — the visible "Commands" label was visually competing with
  // the Mode and Model selectors next to it without adding information
  // (the icon + tooltip already convey "open the command/skill palette").
  // Kept as a square ghost button so it lines up with the attach / mic
  // icon-only siblings further down the toolbar.
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className="size-7"
      data-testid="chat-slash-trigger"
      aria-label={t("slashButtonAria")}
      title={t("slashKbdHint")}
      disabled={disabled}
      onClick={(e) => {
        e.preventDefault();
        onTrigger();
      }}
    >
      {loading ? (
        <IconLoader2 className="size-4 animate-spin sh-muted" />
      ) : (
        // Skills icon — same glyph + same ``size-4`` rendering as the
        // SiderNav `/skills` entry, so the chat composer's
        // skill button reads as the same concept at the same weight.
        <IconPuzzle className="size-4" />
      )}
    </Button>
  );
}

interface ModeSelectProps {
  value: ChatMode;
  onChange: (m: ChatMode) => void;
  t: ReturnType<typeof useTranslations>;
}

function ModeSelect({ value, onChange, t }: ModeSelectProps) {
  const active = MODES.find((m) => m.id === value) ?? MODES[0]!;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 gap-1 px-2 text-xs"
          data-testid="chat-mode"
          data-mode={value}
        >
          <IconWand className="size-3" />
          {t(active.labelKey)}
          <IconChevronDown className="size-3 sh-muted" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel>{t("modeFlash")}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {MODES.map((m) => (
          <DropdownMenuItem
            key={m.id}
            onSelect={() => onChange(m.id)}
            data-active={value === m.id}
            className="flex flex-col items-start"
          >
            <span className="text-xs font-medium">{t(m.labelKey)}</span>
            <span className="text-[10px] sh-muted">{t(m.descKey)}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ────────────────────────────────────────────────────────────
// Web Speech API typings
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
