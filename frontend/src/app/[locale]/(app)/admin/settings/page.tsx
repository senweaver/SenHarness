import { redirect } from "next/navigation";

export default async function AdminSettingsIndex({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect(`/${locale}/admin/settings/general`);
}
