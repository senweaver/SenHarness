import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SenHarness",
  description: "Multi-agent operating system for enterprises",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return children;
}
