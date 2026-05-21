/**
 * Barrel export for the chat surface.
 *
 * Streaming Markdown rendering is now provided by ``ai-elements/<Response>``
 * (streamdown under the hood). The legacy ``MarkdownContent`` /
 * ``MessageList`` / ``MessageItem`` trio has been removed — the chat session
 * page composes ``Conversation`` + ``Message`` + ``Response`` + ``Tool``
 * + ``Reasoning`` directly.
 */

export { AttachmentView, type AttachmentRef } from "./AttachmentView";
export { ApprovalCard, type ApprovalStatus } from "./ApprovalCard";
export { ChatInput, type ChatInputHandle } from "./ChatInput";
export { CopyButton } from "./CopyButton";
export { RatingButtons } from "./RatingButtons";
export { SessionList } from "./SessionList";
export { ShareDialog } from "./ShareDialog";
export { ToolCallCard, type ToolCall, type ToolStatus } from "./ToolCallCard";
