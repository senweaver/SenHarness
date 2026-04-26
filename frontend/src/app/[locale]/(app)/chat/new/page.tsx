"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "@/lib/navigation"
import { useSearchParams } from "next/navigation";
import { IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useCreateSession } from "@/hooks/use-create-session";

/** Create a blank session with the given ?agent=... and forward to /chat/{id}. */
export default function NewChatRoute() {
  const router = useRouter();
  const search = useSearchParams();
  const create = useCreateSession();
  const t = useTranslations("chat");
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const agentId = search.get("agent");
    const squadId = search.get("squad");
    const payload = squadId
      ? { kind: "squad" as const, subject_id: squadId, title: null }
      : { kind: "p2p" as const, subject_id: agentId, title: null };
    create
      .mutateAsync(payload)
      .then((s) => router.replace(`/chat/${s.id}`))
      .catch((err: unknown) => {
        // V1 review fix: was a silent ``router.replace("/")`` so the
        // employee landed back on home with zero feedback. Now we surface
        // the API error code so they at least see ``agent_not_found`` or
        // ``forbidden`` and can ask for help.
        const code =
          (err as { code?: string; message?: string }).code ??
          (err as Error).message ??
          "create_session_failed";
        toast.error(t("createSessionFailed", { code }));
        router.replace("/");
      });
  }, [search, router, create, t]);

  return (
    <div className="flex h-full flex-1 items-center justify-center">
      <IconLoader2 className="size-5 animate-spin sh-muted" />
    </div>
  );
}
