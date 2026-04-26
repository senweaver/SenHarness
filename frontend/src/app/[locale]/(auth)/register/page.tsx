"use client";

import { useState } from "react";
import { Link } from "@/lib/navigation";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

export default function RegisterPage() {
  const t = useTranslations();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.post("/api/v1/auth/register", { email, name, password }, { skipAuth: true });
      router.push("/login");
    } catch (err: unknown) {
      setError((err as { code?: string; message?: string }).message ?? "register_failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-md sh-primary text-sm font-bold">
            S
          </div>
          <h1 className="text-xl font-semibold">{t("auth.registerTitle")}</h1>
          <p className="mt-1 text-sm sh-muted">{t("auth.registerSubtitle")}</p>
        </div>

        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs sh-muted">{t("common.email")}</label>
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </div>
          <div className="space-y-1">
            <label className="text-xs sh-muted">{t("common.name")}</label>
            <Input value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="space-y-1">
            <label className="text-xs sh-muted">{t("common.password")}</label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
            />
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? t("common.loading") : t("common.signUp")}
          </Button>

          <div className="text-center text-xs sh-muted">
            <Link href="/login" className="hover:underline">
              {t("common.signIn")}
            </Link>
          </div>
        </form>
      </div>
    </main>
  );
}
