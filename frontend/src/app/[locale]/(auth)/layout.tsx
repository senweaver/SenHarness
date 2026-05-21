import { AuthLocaleSwitcher } from "@/components/layout/AuthLocaleSwitcher";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative min-h-screen">
      <div className="absolute right-4 top-4 z-10">
        <AuthLocaleSwitcher />
      </div>
      {children}
    </div>
  );
}
