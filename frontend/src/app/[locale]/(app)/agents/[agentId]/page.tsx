import { AgentDetailView } from "./AgentDetailView";

export default async function AgentDetailPage({
  params,
}: {
  params: Promise<{ agentId: string }>;
}) {
  const { agentId } = await params;
  return <AgentDetailView agentId={agentId} />;
}
