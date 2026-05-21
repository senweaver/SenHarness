"use client";

import { IconLeaf } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function EnvOverrideBadge() {
  const t = useTranslations("platformSettings");
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1 rounded-md bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
            <IconLeaf className="size-3" />
            .env
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          {t("envOverrideTooltip")}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
