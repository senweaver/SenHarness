import { ActiveAgentsList } from "@/components/dashboard/ActiveAgentsList";
import { MetricsGrid } from "@/components/dashboard/MetricsGrid";
import { RecentSessionsCard } from "@/components/dashboard/RecentSessionsCard";
import { WelcomeBanner } from "@/components/dashboard/WelcomeBanner";
import { ProvidersOnboardingBanner } from "@/components/onboarding/ProvidersOnboardingBanner";

export default function DashboardPage() {
  return (
    <div className="mx-auto w-full max-w-[1440px] flex-1 p-4 sm:p-6">
      <ProvidersOnboardingBanner />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-12 md:gap-6">
        <div className="md:col-span-12 xl:col-span-8">
          <WelcomeBanner />
        </div>
        <div className="md:col-span-12 xl:col-span-4">
          <MetricsGrid />
        </div>
        <div className="md:col-span-12 lg:col-span-6">
          <ActiveAgentsList />
        </div>
        <div className="md:col-span-12 lg:col-span-6">
          <RecentSessionsCard />
        </div>
      </div>
    </div>
  );
}
