"""Static catalog of models commonly available per provider.

Used to populate the chat composer's ``ModelSelector`` dropdown without
requiring a live API call to every supported provider (OpenAI's ``/models``
endpoint requires auth; Anthropic doesn't expose one at all).

The list is deliberately *opinionated*: we surface the SKUs we have pricing
data for in :mod:`app.core.pricing` plus the obvious frontier picks. Operators
who need more (custom fine-tunes, just-released models) can:

  - extend ``CATALOG`` directly here, or
  - rely on ``model_override`` ("provider:model") which never consults this
    list — it goes straight into the ``RunRequest``.

Each entry exposes ``id`` (full ``provider:model`` selector — what the
frontend forwards to the WS), ``name`` (human label), ``family`` (rough
grouping for UI badges) and ``recommended`` (a single default per provider).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One row in the model dropdown."""

    provider: str
    model: str
    name: str
    family: str
    recommended: bool = False
    description: str = ""
    category: str = "chat"  # chat | image | video | embedding | asr | tts
    capabilities: tuple[str, ...] = ()  # e.g. ("vision", "tools", "reasoning")
    context_window: int | None = None
    pricing: tuple[float, float] | None = None  # (input_$/MTok, output_$/MTok)

    @property
    def id(self) -> str:
        """``provider:model`` token the frontend forwards to ``RunRequest.model_override``."""
        return f"{self.provider}:{self.model}"


# Keys are provider kinds matching ``ResolvedModel.provider_kind`` /
# ``ProviderKind`` enum values.
CATALOG: dict[str, list[CatalogModel]] = {
    # ─── International flagship ─────────────────────────
    "openai": [
        CatalogModel(
            "openai",
            "gpt-5.5",
            "GPT-5.5",
            "frontier",
            description="Latest frontier reasoning + tools.",
        ),
        CatalogModel("openai", "gpt-5.4", "GPT-5.4", "frontier"),
        CatalogModel(
            "openai",
            "gpt-5.4-mini",
            "GPT-5.4 Mini",
            "balanced",
            recommended=True,
            description="Cheap and fast — default.",
        ),
        CatalogModel("openai", "gpt-5-mini", "GPT-5 Mini", "balanced"),
        CatalogModel("openai", "gpt-5.3-codex", "GPT-5.3 Codex", "coding"),
        CatalogModel("openai", "gpt-4.1", "GPT-4.1", "frontier"),
        CatalogModel("openai", "gpt-4o", "GPT-4o", "frontier"),
        CatalogModel(
            "openai", "o3", "o3", "reasoning", description="Best at multi-step reasoning."
        ),
        CatalogModel("openai", "o4-mini", "o4 Mini", "reasoning"),
        CatalogModel(
            "openai",
            "text-embedding-3-small",
            "Text Embedding 3 Small",
            "embedding",
            recommended=True,
            category="embedding",
            description="1536-d embeddings — default cost/quality tradeoff.",
        ),
        CatalogModel(
            "openai",
            "text-embedding-3-large",
            "Text Embedding 3 Large",
            "embedding",
            category="embedding",
            description="3072-d embeddings — higher quality.",
        ),
    ],
    "anthropic": [
        CatalogModel(
            "anthropic",
            "claude-opus-4.7",
            "Claude Opus 4.7",
            "frontier",
            description="Top-tier writing + analysis.",
        ),
        CatalogModel("anthropic", "claude-opus-4.6", "Claude Opus 4.6", "frontier"),
        CatalogModel(
            "anthropic", "claude-sonnet-4.6", "Claude Sonnet 4.6", "balanced", recommended=True
        ),
        CatalogModel("anthropic", "claude-sonnet-4.5", "Claude Sonnet 4.5", "balanced"),
        CatalogModel("anthropic", "claude-haiku-4.5", "Claude Haiku 4.5", "fast"),
        CatalogModel("anthropic", "claude-3-5-haiku", "Claude 3.5 Haiku", "fast"),
    ],
    "google": [
        CatalogModel("google", "gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "frontier"),
        CatalogModel("google", "gemini-3-pro-preview", "Gemini 3 Pro Preview", "frontier"),
        CatalogModel(
            "google",
            "gemini-3-flash-preview",
            "Gemini 3 Flash Preview",
            "balanced",
            recommended=True,
        ),
        CatalogModel("google", "gemini-2.5-pro", "Gemini 2.5 Pro", "frontier"),
        CatalogModel("google", "gemini-2.5-flash", "Gemini 2.5 Flash", "balanced"),
    ],
    "xai": [
        CatalogModel(
            "xai", "grok-4.20-reasoning", "Grok 4.20 Reasoning", "reasoning", recommended=True
        ),
        CatalogModel("xai", "grok-4-1-fast-reasoning", "Grok 4.1 Fast Reasoning", "reasoning"),
    ],
    # ─── International gateway / cloud ──────────────────
    "openrouter": [],
    "azure_openai": [
        CatalogModel("azure_openai", "gpt-5.4", "GPT-5.4 (Azure)", "frontier", recommended=True),
        CatalogModel("azure_openai", "gpt-5.4-mini", "GPT-5.4 Mini (Azure)", "balanced"),
        CatalogModel("azure_openai", "gpt-4.1", "GPT-4.1 (Azure)", "frontier"),
        CatalogModel("azure_openai", "gpt-4o", "GPT-4o (Azure)", "frontier"),
        CatalogModel(
            "azure_openai",
            "text-embedding-3-small",
            "Text Embedding 3 Small (Azure)",
            "embedding",
            recommended=True,
            category="embedding",
            description="Azure deployment name must match this id.",
        ),
        CatalogModel(
            "azure_openai",
            "text-embedding-3-large",
            "Text Embedding 3 Large (Azure)",
            "embedding",
            category="embedding",
        ),
    ],
    "huggingface": [
        CatalogModel(
            "huggingface",
            "Qwen/Qwen3.5-397B-A17B",
            "Qwen3.5 397B-A17B",
            "frontier",
            recommended=True,
        ),
        CatalogModel("huggingface", "deepseek-ai/DeepSeek-V3.2", "DeepSeek V3.2", "balanced"),
        CatalogModel("huggingface", "moonshotai/Kimi-K2.5", "Kimi K2.5", "balanced"),
        CatalogModel("huggingface", "MiniMaxAI/MiniMax-M2.5", "MiniMax M2.5", "balanced"),
        CatalogModel("huggingface", "zai-org/GLM-5", "GLM-5", "balanced"),
        CatalogModel("huggingface", "XiaomiMiMo/MiMo-V2-Flash", "MiMo V2 Flash", "fast"),
    ],
    # ─── China — DeepSeek ───────────────────────────────
    "deepseek": [
        CatalogModel("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro", "frontier"),
        CatalogModel(
            "deepseek", "deepseek-v4-flash", "DeepSeek V4 Flash", "balanced", recommended=True
        ),
        CatalogModel("deepseek", "deepseek-chat", "DeepSeek Chat", "balanced"),
        CatalogModel("deepseek", "deepseek-reasoner", "DeepSeek Reasoner", "reasoning"),
    ],
    # ─── China — DashScope / Bailian / Kimi / Zhipu / … ─
    "dashscope": [
        CatalogModel(
            "dashscope",
            "qwen3.6-plus",
            "Qwen 3.6 Plus",
            "frontier",
            recommended=True,
            capabilities=("chat", "vision", "reasoning", "tools"),
        ),
        CatalogModel(
            "dashscope",
            "qwen3.5-plus",
            "Qwen 3.5 Plus",
            "frontier",
            capabilities=("chat", "vision", "reasoning", "tools"),
        ),
        CatalogModel(
            "dashscope",
            "qwen3-max-2026-01-23",
            "Qwen3 Max (2026-01-23)",
            "frontier",
            capabilities=("chat", "reasoning", "tools"),
        ),
        CatalogModel(
            "dashscope",
            "qwen3-coder-plus",
            "Qwen3 Coder Plus",
            "coding",
            capabilities=("chat", "coding", "tools"),
        ),
        CatalogModel(
            "dashscope",
            "qwen3-coder-next",
            "Qwen3 Coder Next",
            "coding",
            capabilities=("chat", "coding", "tools"),
        ),
        CatalogModel(
            "dashscope",
            "glm-5",
            "GLM-5 (DashScope)",
            "balanced",
            capabilities=("chat", "reasoning", "tools"),
        ),
        CatalogModel(
            "dashscope",
            "kimi-k2.5",
            "Kimi K2.5 (DashScope)",
            "balanced",
            capabilities=("chat", "vision", "reasoning", "tools"),
        ),
        CatalogModel(
            "dashscope",
            "MiniMax-M2.5",
            "MiniMax M2.5 (DashScope)",
            "balanced",
            capabilities=("chat", "reasoning", "tools"),
        ),
        CatalogModel(
            "dashscope",
            "text-embedding-v3",
            "Tongyi Embedding v3",
            "embedding",
            recommended=True,
            category="embedding",
            description="1024-d 通义嵌入 v3.",
        ),
        CatalogModel(
            "dashscope",
            "text-embedding-v2",
            "Tongyi Embedding v2",
            "embedding",
            category="embedding",
        ),
    ],
    "bailian_token": [
        CatalogModel(
            "bailian_token",
            "qwen3.6-plus",
            "Qwen 3.6 Plus",
            "frontier",
            recommended=True,
            capabilities=("chat", "vision", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_token",
            "qwen3.5-plus",
            "Qwen 3.5 Plus",
            "frontier",
            capabilities=("chat", "vision", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_token",
            "qwen3-coder-plus",
            "Qwen3 Coder Plus",
            "coding",
            capabilities=("chat", "coding", "tools"),
        ),
        CatalogModel(
            "bailian_token",
            "qwen3-max-2026-01-23",
            "Qwen3 Max (2026-01-23)",
            "frontier",
            capabilities=("chat", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_token",
            "qwen-long",
            "Qwen Long",
            "balanced",
            description="Long-context bundle SKU.",
            capabilities=("chat", "tools"),
            context_window=10_000_000,
        ),
        CatalogModel(
            "bailian_token",
            "text-embedding-v3",
            "通义嵌入 v3",
            "embedding",
            recommended=True,
            category="embedding",
        ),
    ],
    "bailian_coding": [
        CatalogModel(
            "bailian_coding",
            "qwen3.6-plus",
            "Qwen 3.6 Plus",
            "frontier",
            recommended=True,
            capabilities=("chat", "vision", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_coding",
            "qwen3.5-plus",
            "Qwen 3.5 Plus",
            "frontier",
            capabilities=("chat", "vision", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_coding",
            "qwen3-max-2026-01-23",
            "Qwen3 Max (2026-01-23)",
            "frontier",
            capabilities=("chat", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_coding",
            "qwen3-coder-next",
            "Qwen3 Coder Next",
            "coding",
            capabilities=("chat", "coding", "tools"),
        ),
        CatalogModel(
            "bailian_coding",
            "qwen3-coder-plus",
            "Qwen3 Coder Plus",
            "coding",
            capabilities=("chat", "coding", "tools"),
        ),
        CatalogModel(
            "bailian_coding",
            "glm-5",
            "GLM-5",
            "balanced",
            capabilities=("chat", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_coding",
            "glm-4.7",
            "GLM-4.7",
            "balanced",
            capabilities=("chat", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_coding",
            "kimi-k2.5",
            "Kimi K2.5",
            "balanced",
            capabilities=("chat", "vision", "reasoning", "tools"),
        ),
        CatalogModel(
            "bailian_coding",
            "MiniMax-M2.5",
            "MiniMax M2.5",
            "balanced",
            capabilities=("chat", "reasoning", "tools"),
        ),
    ],
    # ─── China — Kimi (Moonshot) family ─────────────────
    "moonshot": [
        CatalogModel("moonshot", "kimi-k2.6", "Kimi K2.6", "frontier"),
        CatalogModel("moonshot", "kimi-k2.5", "Kimi K2.5", "balanced", recommended=True),
        CatalogModel("moonshot", "kimi-k2-thinking", "Kimi K2 Thinking", "reasoning"),
        CatalogModel("moonshot", "kimi-k2-turbo-preview", "Kimi K2 Turbo Preview", "fast"),
        CatalogModel("moonshot", "kimi-k2-0905-preview", "Kimi K2 0905 Preview", "balanced"),
    ],
    "kimi_code": [
        CatalogModel("kimi_code", "kimi-for-coding", "Kimi for Coding", "coding", recommended=True),
        CatalogModel("kimi_code", "kimi-k2.6", "Kimi K2.6", "frontier"),
        CatalogModel("kimi_code", "kimi-k2.5", "Kimi K2.5", "balanced"),
        CatalogModel("kimi_code", "kimi-k2-thinking", "Kimi K2 Thinking", "reasoning"),
        CatalogModel("kimi_code", "kimi-k2-turbo-preview", "Kimi K2 Turbo Preview", "fast"),
        CatalogModel("kimi_code", "kimi-k2-0905-preview", "Kimi K2 0905 Preview", "balanced"),
    ],
    "zhipu": [
        CatalogModel("zhipu", "glm-5.1", "GLM-5.1", "frontier"),
        CatalogModel("zhipu", "glm-5", "GLM-5", "balanced", recommended=True),
        CatalogModel(
            "zhipu", "glm-5v-turbo", "GLM-5V Turbo", "balanced", description="Vision multimodal."
        ),
        CatalogModel("zhipu", "glm-5-turbo", "GLM-5 Turbo", "fast"),
        CatalogModel("zhipu", "glm-4.7", "GLM-4.7", "balanced"),
        CatalogModel("zhipu", "glm-4.5", "GLM-4.5", "balanced"),
        CatalogModel("zhipu", "glm-4.5-flash", "GLM-4.5 Flash", "fast"),
        CatalogModel(
            "zhipu",
            "embedding-3",
            "Zhipu Embedding 3",
            "embedding",
            recommended=True,
            category="embedding",
            description="2048-d embedding v3.",
        ),
        CatalogModel(
            "zhipu",
            "embedding-2",
            "Zhipu Embedding 2",
            "embedding",
            category="embedding",
        ),
    ],
    "siliconflow": [
        CatalogModel(
            "siliconflow",
            "deepseek-ai/DeepSeek-V3.2",
            "DeepSeek V3.2",
            "balanced",
            recommended=True,
        ),
        CatalogModel("siliconflow", "Qwen/Qwen3.5-397B-A17B", "Qwen 3.5 397B-A17B", "frontier"),
        CatalogModel("siliconflow", "moonshotai/Kimi-K2.5", "Kimi K2.5", "balanced"),
        CatalogModel("siliconflow", "MiniMaxAI/MiniMax-M2.5", "MiniMax M2.5", "balanced"),
        CatalogModel(
            "siliconflow", "meta-llama/Llama-3.3-70B-Instruct", "Llama 3.3 70B", "balanced"
        ),
        CatalogModel(
            "siliconflow",
            "BAAI/bge-m3",
            "BGE M3 (multi-lingual)",
            "embedding",
            recommended=True,
            category="embedding",
            description="1024-d multilingual embeddings.",
        ),
        CatalogModel(
            "siliconflow",
            "BAAI/bge-large-zh-v1.5",
            "BGE Large Chinese v1.5",
            "embedding",
            category="embedding",
        ),
    ],
    "minimax": [
        CatalogModel("minimax", "MiniMax-M2.7", "MiniMax M2.7", "frontier"),
        CatalogModel("minimax", "MiniMax-M2.5", "MiniMax M2.5", "balanced", recommended=True),
        CatalogModel("minimax", "MiniMax-M2.1", "MiniMax M2.1", "balanced"),
        CatalogModel("minimax", "MiniMax-M2-highspeed", "MiniMax M2 Highspeed", "fast"),
    ],
    # ─── Local / private ────────────────────────────────
    "ollama": [
        CatalogModel("ollama", "llama4.1", "Llama 4.1 (local)", "local"),
        CatalogModel(
            "ollama",
            "qwen3-coder-30b-a3b",
            "Qwen3 Coder 30B-A3B (local)",
            "coding",
            recommended=True,
        ),
        CatalogModel("ollama", "deepseek-v3-distill", "DeepSeek V3 Distill (local)", "local"),
        CatalogModel("ollama", "mistral", "Mistral (local)", "local"),
        CatalogModel(
            "ollama",
            "nomic-embed-text",
            "Nomic Embed Text (local)",
            "embedding",
            recommended=True,
            category="embedding",
            description="768-d open embedder via Ollama native /api/embeddings.",
        ),
        CatalogModel(
            "ollama",
            "mxbai-embed-large",
            "MixedBread Embed Large (local)",
            "embedding",
            category="embedding",
        ),
    ],
    "vllm": [],
    "custom": [],
}


def list_models_for_provider(provider_kind: str) -> list[CatalogModel]:
    """Return the catalog rows for ``provider_kind``.

    Unknown providers return ``[]`` — the frontend hides the dropdown when
    the list is empty (the user can still rely on the workspace default).
    """
    from app.agents.kernels.provider_catalog import get_entry

    rows: list[CatalogModel] = list(CATALOG.get(provider_kind, []))
    if rows:
        return rows
    entry = get_entry(provider_kind)
    if entry is not None:
        if entry.kind != provider_kind:
            rows = list(CATALOG.get(entry.kind, []))
        if not rows and entry.pydantic_ai_kind:
            rows = list(CATALOG.get(entry.pydantic_ai_kind, []))
    return rows


def known_provider_kinds() -> list[str]:
    """All provider kinds with at least one catalog entry."""
    return [k for k, v in CATALOG.items() if v]


def list_embedding_models_for_provider(provider_kind: str) -> list[CatalogModel]:
    """Return only the ``category="embedding"`` rows for ``provider_kind``."""
    return [row for row in list_models_for_provider(provider_kind) if row.category == "embedding"]


def provider_supports_embeddings(provider_kind: str) -> bool:
    """Whether the catalog declares at least one embedding SKU for this kind.

    The embedder uses this as the gate before issuing any HTTP probe — it
    avoids the historical 404 storm against providers that only serve chat
    (DeepSeek, Moonshot/Kimi, Aliyun coding plan, xAI, MiniMax, ...).
    """
    return bool(list_embedding_models_for_provider(provider_kind))


def default_embedding_model_for_provider(provider_kind: str) -> str | None:
    """Pick the recommended embedding model name for ``provider_kind``.

    Falls back to the first embedding row when no row is flagged
    ``recommended``. Returns ``None`` when the provider declares none.
    """
    rows = list_embedding_models_for_provider(provider_kind)
    for row in rows:
        if row.recommended:
            return row.model
    return rows[0].model if rows else None


def embedding_capable_provider_kinds() -> list[str]:
    """All provider kinds whose catalog declares at least one embedding SKU."""
    return [kind for kind in CATALOG if provider_supports_embeddings(kind)]
