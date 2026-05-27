/**
 * Smoke test for BlankAgentDialog:
 *
 *   1. Create is disabled while the name field is empty.
 *   2. Typing a name enables Create.
 *   3. Clicking Create fires the create-agent mutation with the typed
 *      payload, closes the dialog, and navigates to the new agent.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const hoisted = vi.hoisted(() => ({
  push: vi.fn(),
  mutateAsync: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/lib/navigation", () => ({
  useRouter: () => ({ push: hoisted.push }),
}));

vi.mock("@/hooks/use-agent-mutations", () => ({
  useCreateAgent: () => ({
    mutateAsync: hoisted.mutateAsync,
    isPending: false,
  }),
}));

vi.mock("@/hooks/use-agent-models", () => ({
  useWorkspaceModelOptions: () => ({ data: [] }),
}));

vi.mock("@/hooks/use-runtimes", () => ({
  useRegisteredRuntimes: () => ({
    data: [
      {
        kind: "native",
        display_name: "Native (built-in)",
        description: "",
        docs_url: "",
        requires_adapter: false,
        capabilities: {
          supports_streaming: true,
          supports_parallel_tools: false,
          supports_thinking: false,
          supports_native_mcp: false,
          supports_vision: false,
          max_context_tokens: null,
          notes: "",
        },
      },
    ],
  }),
}));

vi.mock("@/hooks/use-backend-adapters", () => ({
  useBackendAdapters: () => ({ data: [] }),
}));

vi.mock("sonner", () => ({
  toast: {
    success: hoisted.toastSuccess,
    error: hoisted.toastError,
  },
}));

import { BlankAgentDialog } from "@/components/agents/BlankAgentDialog";

type ProviderMessages = ComponentProps<typeof NextIntlClientProvider>["messages"];

const messages = {
  common: { cancel: "Cancel" },
  newAgent: {
    created: "Agent created",
    createFailed: "Create failed",
    missingName: "Name is required.",
    blank: {
      title: "Create custom agent",
      description: "Fill the essentials; refine the rest later.",
      nameLabel: "Name",
      namePlaceholder: "Customer support",
      descLabel: "Role description",
      descPlaceholder: "What does this agent do?",
      modelLabel: "Default model",
      modelEmpty: "Workspace default",
      visibility: {
        label: "Visibility",
        workspace: "Anyone in workspace",
        workspaceHint: "Everyone in the workspace can use it.",
        private: "Only me",
        privateHint: "Just you for now; share later in settings.",
      },
      runtimeLabel: "Runtime",
      adapterLabel: "Backend adapter",
      adapterEmpty: "Pick adapter...",
      adapterMissingHint: "No adapters yet.",
      remoteModelHint: "The remote worker chooses its own model.",
      create: "Create",
    },
  },
} as unknown as ProviderMessages;

function renderDialog(onOpenChange: (open: boolean) => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="en-US" messages={messages}>
        <BlankAgentDialog open onOpenChange={onOpenChange} />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

describe("BlankAgentDialog", () => {
  beforeEach(() => {
    hoisted.push.mockReset();
    hoisted.mutateAsync.mockReset();
    hoisted.toastSuccess.mockReset();
    hoisted.toastError.mockReset();
  });

  it("disables Create until a name is typed", async () => {
    const user = userEvent.setup();
    renderDialog(() => undefined);

    const createBtn = screen.getByTestId("blank-agent-create");
    expect(createBtn).toBeDisabled();

    await user.type(screen.getByTestId("blank-agent-name"), "Helper");
    expect(createBtn).not.toBeDisabled();
  });

  it("submits the typed payload and navigates on success", async () => {
    const user = userEvent.setup();
    hoisted.mutateAsync.mockResolvedValue({ id: "ag-1" });
    const onOpenChange = vi.fn();
    renderDialog(onOpenChange);

    await user.type(screen.getByTestId("blank-agent-name"), "Helper");
    await user.click(screen.getByTestId("blank-agent-create"));

    expect(hoisted.mutateAsync).toHaveBeenCalledWith({
      name: "Helper",
      description: null,
      default_model: null,
      visibility: "private",
      backend_kind: "native",
      backend_adapter_id: null,
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(hoisted.push).toHaveBeenCalledWith("/agents/ag-1");
  });
});
