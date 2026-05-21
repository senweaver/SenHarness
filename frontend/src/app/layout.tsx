import type { Metadata } from "next";
import "./globals.css";
import { defaultLocale } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "SenHarness",
  description: "Multi-agent operating system for enterprises",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang={defaultLocale} suppressHydrationWarning>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
