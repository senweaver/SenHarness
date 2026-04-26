import { HeroPrompt } from "@/components/home/HeroPrompt";
import { QuickActions } from "@/components/home/QuickActions";
import { ExampleCards } from "@/components/home/ExampleCards";

export default function HomePage() {
  return (
    <div className="flex flex-1 flex-col">
      <HeroPrompt />
      <QuickActions />
      <ExampleCards />
    </div>
  );
}
