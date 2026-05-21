"use client";

import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { FlowTestResult } from "@/hooks/use-flows";

interface FlowTestArgs {
  override?: Record<string, unknown> | null;
}

export function useFlowTestScript(flowId: string) {
  return useMutation<FlowTestResult, unknown, FlowTestArgs | undefined>({
    mutationFn: (args) =>
      api.post<FlowTestResult>(
        `/api/v1/flows/${flowId}/test-script`,
        args?.override ?? null,
      ),
  });
}

export function useFlowTestHttp(flowId: string) {
  return useMutation<FlowTestResult, unknown, FlowTestArgs | undefined>({
    mutationFn: (args) =>
      api.post<FlowTestResult>(
        `/api/v1/flows/${flowId}/test-http`,
        args?.override ?? null,
      ),
  });
}
