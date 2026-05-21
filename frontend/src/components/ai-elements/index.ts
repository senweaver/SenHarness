/**
 * Adapted from Vercel AI SDK AI Elements.
 *
 * Barrel export for the SenHarness chat surface's component primitives.
 * Each module documents its upstream provenance; this file is
 * import-ergonomics only.
 */

export { Conversation, ConversationContent, ConversationScrollButton } from "./conversation";
export { Message, MessageContent } from "./message";
export { Response } from "./response";
export { Reasoning } from "./reasoning";
export { Tool } from "./tool";
export { Action, Actions } from "./actions";
export {
  PromptInput,
  PromptInputTextarea,
  PromptInputToolbar,
  PromptInputTools,
  PromptInputSubmit,
  COMPOSER_TEXT_CLASS,
  type PromptInputStatus,
} from "./prompt-input";
export { Suggestion, Suggestions } from "./suggestion";
export {
  SlashCommandPalette,
  MentionPalette,
  type SlashItem,
  type SlashItemKind,
  type MentionItem,
  type MentionGroup,
  type PaletteHandle,
} from "./command-palette";
export { ModelSelector, type ModelSelectorProps } from "./model-selector";

// ── Extended primitives (v2) ───────────────────────────────
// These are not yet wired into the default chat transcript but are kept in
// the barrel so feature work (artifact preview, RAG citations, follow-up
// pages, multi-branch regenerate UX) can compose them without re-deriving
// the upstream code each time.
export { Loader, type LoaderProps } from "./loader";
export {
  CodeBlock,
  CodeBlockCopyButton,
  type CodeBlockProps,
  type CodeBlockCopyButtonProps,
} from "./code-block";
export { Image, type ImageProps, type GeneratedImagePart } from "./image";
export {
  Sources,
  SourcesTrigger,
  SourcesContent,
  Source,
  type SourcesProps,
  type SourcesTriggerProps,
  type SourcesContentProps,
  type SourceProps,
} from "./sources";
export {
  Task,
  TaskTrigger,
  TaskContent,
  TaskItem,
  TaskItemFile,
  type TaskProps,
  type TaskTriggerProps,
  type TaskContentProps,
  type TaskItemProps,
  type TaskItemFileProps,
} from "./task";
export {
  Branch,
  BranchMessages,
  BranchSelector,
  BranchPrevious,
  BranchNext,
  BranchPage,
  type BranchProps,
  type BranchMessagesProps,
  type BranchSelectorProps,
  type BranchPreviousProps,
  type BranchNextProps,
  type BranchPageProps,
} from "./branch";
export {
  InlineCitation,
  InlineCitationText,
  InlineCitationCard,
  InlineCitationSource,
  InlineCitationQuote,
  type InlineCitationProps,
  type InlineCitationTextProps,
  type InlineCitationCardProps,
  type InlineCitationSourceProps,
  type InlineCitationQuoteProps,
} from "./inline-citation";
export {
  WebPreview,
  WebPreviewNavigation,
  WebPreviewUrl,
  WebPreviewBody,
  type WebPreviewProps,
  type WebPreviewNavigationProps,
  type WebPreviewUrlProps,
  type WebPreviewBodyProps,
} from "./web-preview";
