import { redirect } from "next/navigation";

/**
 * Plan §6 — `/settings/profile/soul` is now a Tab inside
 * `/settings/profile`. Direct links are forwarded so any deep link
 * (the original Memory tab `Open SOUL.md` action, the welcome tour,
 * docs) keeps working without a 404.
 */
export default async function SoulRedirect({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect(`/${locale}/settings/profile?tab=soul`);
}
