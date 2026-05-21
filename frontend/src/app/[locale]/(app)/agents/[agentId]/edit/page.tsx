import { redirect } from "next/navigation";

/**
 * Plan §3 — agent editing is now an inline action inside the
 * `Overview` tab of `/agents/[id]`. We redirect to the same place
 * with `edit=1` so any direct link still lands the user on the
 * editable persona / runtime panel.
 */
export default async function EditAgentRedirect({
  params,
}: {
  params: Promise<{ locale: string; agentId: string }>;
}) {
  const { locale, agentId } = await params;
  redirect(`/${locale}/agents/${agentId}?tab=overview&edit=1`);
}
