import { redirect } from "next/navigation";

/**
 * Plan §5 — `/agents/new` is no longer a standalone full-page form.
 * The new flow is the in-place `NewAgentDialog` mounted on `/agents`,
 * which renders a template gallery + Blank + Marketplace clone tabs.
 * Anyone landing here from a stale link / docs is bounced into the
 * dialog with `?new=1` so their intent is preserved.
 */
export default async function NewAgentRedirect({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect(`/${locale}/agents?new=1`);
}
