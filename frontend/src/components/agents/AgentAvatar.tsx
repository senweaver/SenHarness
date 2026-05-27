"use client";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";

interface AgentAvatarProps {
  name: string | null | undefined;
  avatarUrl?: string | null;
  className?: string;
  fallbackClassName?: string;
}

export function AgentAvatar({
  name,
  avatarUrl,
  className,
  fallbackClassName,
}: AgentAvatarProps) {
  const initial = (name ?? "?").trim().charAt(0).toUpperCase() || "?";
  return (
    <Avatar className={cn("size-8 shrink-0", className)}>
      {avatarUrl ? <AvatarImage src={avatarUrl} alt={name ?? "Agent"} /> : null}
      <AvatarFallback
        className={cn(
          "bg-[rgb(var(--color-primary)/0.12)] font-semibold text-[rgb(var(--color-primary))]",
          fallbackClassName,
        )}
      >
        {initial}
      </AvatarFallback>
    </Avatar>
  );
}
