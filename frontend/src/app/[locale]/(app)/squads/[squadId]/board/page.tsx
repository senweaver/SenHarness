"use client";

import { use } from "react";

import { SquadBoardBody } from "@/components/squads/SquadBoardBody";

export default function SquadBoardPage({
  params,
}: {
  params: Promise<{ squadId: string }>;
}) {
  const { squadId } = use(params);
  return (
    <div className="p-6">
      <SquadBoardBody squadId={squadId} />
    </div>
  );
}
