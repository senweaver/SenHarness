"""Provider catalog: SenHarness-side metadata layered on top of pydantic-ai providers.

Single source of truth for "what providers does SenHarness support, and how do we
display them?". The actual provider class implementation (default base_url,
auth, model_profile) is owned by `pydantic_ai.providers`; this file only adds
display-layer metadata that is not appropriate to ship inside pydantic-ai:

  - Chinese display name (`display_name_zh`)
  - Country / region tag (used to group "China" providers in onboarding)
  - Protocol family (decides discover strategy + which Model class to use)
  - Default credential type (`api_key` / `oauth_token`)
  - One-line marketing description for onboarding
  - Whether this kind supports remote `/v1/models` discovery

The catalog is the curated short list operators actually configure: 17
mainstream providers (international flagships, merged China endpoints,
plus Ollama / vLLM for local) plus a hidden ``custom`` slot for any
OpenAI-compatible endpoint reachable through "+ Add custom provider".

The order of ``_ENTRIES`` is the authoritative sort order shown in the UI:
international flagship → international gateway / cloud → China → local /
private. Same-family entries stay adjacent (Kimi rows next to each other,
Zhipu rows next to each other, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.agents.kernels.model_catalog import CATALOG, CatalogModel

ProviderFamily = Literal[
    "openai-compatible",
    "anthropic",
    "google",
    "huggingface",
    "embedding",
]

CredentialType = Literal["api_key", "oauth_token", "custom_headers"]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    kind: str
    display_name: str
    display_name_zh: str
    family: ProviderFamily
    country_code: str | None
    credential_type: CredentialType
    description: str
    description_zh: str
    pydantic_ai_kind: str | None = None
    aliases: tuple[str, ...] = ()
    notes: str = ""
    signup_url: str = ""  # public page where the operator can mint a fresh key


@dataclass(slots=True)
class CatalogPayload:
    """The serialised view sent to the frontend."""

    kind: str
    display_name: str
    display_name_zh: str
    family: ProviderFamily
    country_code: str | None
    credential_type: CredentialType
    description: str
    description_zh: str
    default_base_url: str | None
    api_key_env: str | None
    supports_discover: bool
    signup_url: str = ""
    aliases: list[str] = field(default_factory=list)
    builtin_models: list[dict[str, object]] = field(default_factory=list)


_OPENAI_COMPATIBLE_KINDS: frozenset[str] = frozenset(
    {
        "openai",
        "azure_openai",
        "openrouter",
        "huggingface",
        "xai",
        "deepseek",
        "dashscope",
        "bailian_token",
        "bailian_coding",
        "moonshot",
        "kimi_code",
        "zhipu",
        "siliconflow",
        "minimax",
        "ollama",
        "vllm",
        "custom",
    }
)


_ENTRIES: tuple[CatalogEntry, ...] = (
    # ─── International flagship ─────────────────────────
    CatalogEntry(
        kind="openai",
        display_name="OpenAI",
        display_name_zh="OpenAI",
        family="openai-compatible",
        country_code="US",
        credential_type="api_key",
        description="GPT-5.x, GPT-4.1, o3, o4 — frontier and reasoning models from OpenAI.",
        description_zh="GPT-5.x、GPT-4.1、o3、o4 — OpenAI 旗舰与推理模型。",
        signup_url="https://platform.openai.com/api-keys",
    ),
    CatalogEntry(
        kind="anthropic",
        display_name="Anthropic Claude",
        display_name_zh="Anthropic Claude",
        family="anthropic",
        country_code="US",
        credential_type="api_key",
        description="Claude 4 Opus / Sonnet / Haiku — top-tier writing & reasoning.",
        description_zh="Claude 4 Opus/Sonnet/Haiku — 顶级写作与推理。",
        signup_url="https://console.anthropic.com/settings/keys",
    ),
    CatalogEntry(
        kind="google",
        display_name="Google Gemini",
        display_name_zh="Google Gemini",
        family="google",
        country_code="US",
        credential_type="api_key",
        description="Gemini 3.x / 2.5 — multimodal frontier from Google.",
        description_zh="Gemini 3.x / 2.5 — Google 多模态旗舰。",
        pydantic_ai_kind="google-gla",
        aliases=("google-gla",),
        signup_url="https://aistudio.google.com/app/apikey",
    ),
    CatalogEntry(
        kind="xai",
        display_name="xAI Grok",
        display_name_zh="xAI Grok",
        family="openai-compatible",
        country_code="US",
        credential_type="api_key",
        description="xAI flagship Grok models.",
        description_zh="xAI 旗舰 Grok 系列模型。",
        signup_url="https://console.x.ai/team/default/api-keys",
    ),
    # ─── International gateway / cloud ──────────────────
    CatalogEntry(
        kind="openrouter",
        display_name="OpenRouter",
        display_name_zh="OpenRouter",
        family="openai-compatible",
        country_code="US",
        credential_type="api_key",
        description="Single endpoint, 100+ models from OpenAI/Anthropic/Google/Meta/etc.",
        description_zh="一个端点接入 OpenAI/Anthropic/Google/Meta 等 100+ 模型。",
        signup_url="https://openrouter.ai/keys",
    ),
    CatalogEntry(
        kind="azure_openai",
        display_name="Azure OpenAI",
        display_name_zh="Azure OpenAI",
        family="openai-compatible",
        country_code="US",
        credential_type="api_key",
        description="OpenAI models hosted on Microsoft Azure with enterprise SLA.",
        description_zh="OpenAI 模型，托管于微软 Azure，提供企业级 SLA。",
        pydantic_ai_kind="azure",
        signup_url="https://portal.azure.com/#create/Microsoft.CognitiveServicesOpenAI",
    ),
    CatalogEntry(
        kind="huggingface",
        display_name="Hugging Face",
        display_name_zh="Hugging Face",
        family="huggingface",
        country_code="US",
        credential_type="api_key",
        description="Hosted serverless inference for community models.",
        description_zh="社区模型的无服务器推理托管。",
        signup_url="https://huggingface.co/settings/tokens",
    ),
    # ─── China — DeepSeek (standalone, very popular) ────
    CatalogEntry(
        kind="deepseek",
        display_name="DeepSeek",
        display_name_zh="DeepSeek 深度求索",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="DeepSeek V4 / V3.2 — strong reasoning at low cost.",
        description_zh="DeepSeek V4 / V3.2 — 高性价比强推理。",
        signup_url="https://platform.deepseek.com/api_keys",
    ),
    # ─── China — DashScope / Bailian / Kimi / Zhipu / … ─
    CatalogEntry(
        kind="dashscope",
        display_name="DashScope",
        display_name_zh="DashScope 通义千问",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="Qwen 3.5 / Qwen3-Coder via DashScope compatible-mode API.",
        description_zh="通义千问 Qwen 3.5 / Qwen3-Coder，DashScope 兼容模式接入。",
        pydantic_ai_kind="alibaba",
        aliases=("alibaba", "alibaba_cn"),
        signup_url="https://bailian.console.aliyun.com/?apiKey=1#/api-key",
    ),
    CatalogEntry(
        kind="bailian_token",
        display_name="Bailian Token",
        display_name_zh="百炼 Token",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="Alibaba Bailian prepaid token plan (MAAS compatible-mode).",
        description_zh="阿里百炼预付费 Token Plan（MAAS 兼容模式）。",
        pydantic_ai_kind="alibaba",
        aliases=("dashscope_token_plan",),
        signup_url="https://bailian.console.aliyun.com/",
    ),
    CatalogEntry(
        kind="bailian_coding",
        display_name="Bailian Coding",
        display_name_zh="百炼 Coding",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="DashScope coding-plan endpoint (Qwen + aggregated GLM/Kimi/MiniMax).",
        description_zh="DashScope Coding Plan 端点（Qwen 及聚合 GLM/Kimi/MiniMax）。",
        pydantic_ai_kind="alibaba",
        aliases=("aliyun_coding_cn", "aliyun_coding_intl"),
        signup_url="https://dashscope.aliyun.com/",
    ),
    CatalogEntry(
        kind="moonshot",
        display_name="Moonshot",
        display_name_zh="Moonshot 月之暗面",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="Moonshot Kimi K2 / K2.5 / K2.6 — long-context chat models.",
        description_zh="Moonshot Kimi K2 / K2.5 / K2.6 超长上下文模型。",
        pydantic_ai_kind="moonshotai",
        aliases=("moonshotai", "kimi_intl"),
        signup_url="https://platform.moonshot.cn/console/api-keys",
    ),
    CatalogEntry(
        kind="kimi_code",
        display_name="Kimi Code",
        display_name_zh="Kimi Code",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="Moonshot coding subscription endpoint with Kimi K2 / Turbo SKUs.",
        description_zh="Moonshot 代码订阅端点，提供 Kimi K2 / Turbo 系列。",
        signup_url="https://platform.moonshot.cn/coding",
    ),
    CatalogEntry(
        kind="zhipu",
        display_name="Zhipu",
        display_name_zh="智谱",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="GLM-4.5 / GLM-5 / GLM-5.1 via BigModel OpenAI-compatible API.",
        description_zh="GLM-4.5 / GLM-5 / GLM-5.1，BigModel OpenAI 兼容接口。",
        aliases=(
            "zhipu_cn",
            "zhipu_intl",
            "zhipu_coding_bigmodel",
            "zhipu_coding_zai",
        ),
        signup_url="https://open.bigmodel.cn/usercenter/apikeys",
    ),
    CatalogEntry(
        kind="siliconflow",
        display_name="SiliconFlow",
        display_name_zh="硅基流动",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="Aggregated open-source inference (Qwen / DeepSeek / Llama).",
        description_zh="开源模型聚合推理（Qwen / DeepSeek / Llama）。",
        aliases=("siliconflow_cn", "siliconflow_intl"),
        signup_url="https://cloud.siliconflow.cn/account/ak",
    ),
    CatalogEntry(
        kind="minimax",
        display_name="MiniMax",
        display_name_zh="MiniMax",
        family="openai-compatible",
        country_code="CN",
        credential_type="api_key",
        description="MiniMax M-series chat models.",
        description_zh="MiniMax M 系列对话模型。",
        aliases=("minimax_cn", "minimax_intl"),
        signup_url="https://www.minimaxi.com/user-center/basic-information/interface-key",
    ),
    # ─── Local / private ────────────────────────────────
    CatalogEntry(
        kind="ollama",
        display_name="Ollama",
        display_name_zh="Ollama",
        family="openai-compatible",
        country_code=None,
        credential_type="api_key",
        description="Local OpenAI-compatible server for Llama/Qwen/Mistral/DeepSeek.",
        description_zh="本地 OpenAI 兼容服务，运行 Llama/Qwen/Mistral/DeepSeek。",
        signup_url="https://ollama.com/download",
    ),
    CatalogEntry(
        kind="vllm",
        display_name="vLLM",
        display_name_zh="vLLM",
        family="openai-compatible",
        country_code=None,
        credential_type="api_key",
        description="Self-hosted vLLM server with OpenAI-compatible API.",
        description_zh="自建 vLLM 服务，OpenAI 兼容协议。",
        signup_url="https://docs.vllm.ai/en/latest/getting_started/installation.html",
    ),
    # ─── Custom slot (hidden from sidebar; reachable via the +-button only) ─
    CatalogEntry(
        kind="custom",
        display_name="Custom (OpenAI-compatible)",
        display_name_zh="自定义（OpenAI 兼容）",
        family="openai-compatible",
        country_code=None,
        credential_type="api_key",
        description="Any OpenAI-compatible endpoint — fill in base_url + key.",
        description_zh="任意 OpenAI 兼容端点 — 填入 base_url 与 key 即可。",
    ),
)


_BY_KIND: dict[str, CatalogEntry] = {}
for _entry in _ENTRIES:
    _BY_KIND[_entry.kind] = _entry
    for _alias in _entry.aliases:
        _BY_KIND[_alias] = _entry


_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "azure_openai": "",
    "openrouter": "https://openrouter.ai/api/v1",
    "huggingface": "https://router.huggingface.co/v1",
    "xai": "https://api.x.ai/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "bailian_token": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "bailian_coding": "https://coding.dashscope.aliyuncs.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "kimi_code": "https://api.moonshot.cn/coding/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "minimax": "https://api.minimaxi.com/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": "",
    "custom": "",
}


_API_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "huggingface": "HF_TOKEN",
    "xai": "XAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "bailian_token": "DASHSCOPE_TOKEN_PLAN_KEY",
    "bailian_coding": "ALIYUN_CODING_API_KEY",
    "moonshot": "MOONSHOTAI_API_KEY",
    "kimi_code": "KIMI_CODE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


def family_of(kind: str) -> ProviderFamily:
    """Return the protocol family for ``kind`` (defaults to ``openai-compatible``)."""
    entry = _BY_KIND.get(kind)
    if entry is not None:
        return entry.family
    if kind in _OPENAI_COMPATIBLE_KINDS:
        return "openai-compatible"
    return "openai-compatible"


def supports_discover(kind: str) -> bool:
    """Whether ``kind`` exposes a remote OpenAI-compatible ``/v1/models`` endpoint."""
    return family_of(kind) == "openai-compatible"


def get_entry(kind: str) -> CatalogEntry | None:
    """Look up the SenHarness display metadata for ``kind`` (incl. aliases)."""
    return _BY_KIND.get(kind)


def canonical_kind(kind: str) -> str:
    """Map ``kind`` or a legacy alias to the catalog canonical ``kind``."""
    entry = get_entry(kind)
    return entry.kind if entry is not None else kind


def default_base_url_for(kind: str) -> str | None:
    """Return SenHarness-known default base_url for ``kind`` or ``None``."""
    entry = get_entry(kind)
    key = entry.kind if entry is not None else kind
    val = _DEFAULT_BASE_URLS.get(key, "")
    return val or None


def api_key_env_for(kind: str) -> str | None:
    """The conventional ``*_API_KEY`` env var name for ``kind`` (display only)."""
    entry = get_entry(kind)
    key = entry.kind if entry is not None else kind
    val = _API_KEY_ENV.get(key, "")
    return val or None


def _builtin_models_for(kind: str) -> list[dict[str, object]]:
    """Static recommended model list pulled from `model_catalog.CATALOG`."""
    out: list[dict[str, object]] = []
    rows: list[CatalogModel] = list(CATALOG.get(kind, []))
    if not rows:
        entry = _BY_KIND.get(kind)
        if entry is not None:
            if entry.kind != kind:
                rows = list(CATALOG.get(entry.kind, []))
            if not rows and entry.pydantic_ai_kind:
                rows = list(CATALOG.get(entry.pydantic_ai_kind, []))
    for row in rows:
        out.append(
            {
                "model": row.model,
                "name": row.name,
                "family": row.family,
                "recommended": row.recommended,
                "description": row.description,
                "category": row.category,
                "capabilities": list(row.capabilities),
                "context_window": row.context_window,
                "pricing": list(row.pricing) if row.pricing else None,
            }
        )
    return out


def iter_catalog() -> list[CatalogPayload]:
    """Serialise every catalog entry for the frontend `/provider-catalog` endpoint."""
    payloads: list[CatalogPayload] = []
    for entry in _ENTRIES:
        payloads.append(
            CatalogPayload(
                kind=entry.kind,
                display_name=entry.display_name,
                display_name_zh=entry.display_name_zh,
                family=entry.family,
                country_code=entry.country_code,
                credential_type=entry.credential_type,
                description=entry.description,
                description_zh=entry.description_zh,
                default_base_url=default_base_url_for(entry.kind),
                api_key_env=api_key_env_for(entry.kind),
                supports_discover=supports_discover(entry.kind),
                signup_url=entry.signup_url,
                aliases=list(entry.aliases),
                builtin_models=_builtin_models_for(entry.kind),
            )
        )
    return payloads


def known_kinds() -> list[str]:
    """All catalog `kind` values (no aliases). Used for service-layer validation."""
    return [e.kind for e in _ENTRIES]


def is_known_kind(kind: str) -> bool:
    """Return whether ``kind`` (or one of its aliases) is in the catalog."""
    return kind in _BY_KIND


# Snapshot of the bundled catalog kinds. Plugin contributions land in
# a sibling set so :func:`is_plugin_kind` can tell built-in from
# user-installed without rescanning the catalog.
_BUILTIN_KINDS: frozenset[str] = frozenset(_BY_KIND.keys())
_PLUGIN_REGISTERED_KINDS: set[str] = set()


def register_kind_from_plugin(kind: str, entry: CatalogEntry) -> None:
    """Install a plugin-contributed catalog entry (M3.5).

    Refuses to override a built-in kind so model traffic accounting
    stays auditable against a stable catalog. ``entry.kind`` must
    equal ``kind`` — the loader keeps the two in sync via the
    factory contract on :meth:`PluginContext.register_model_provider`.
    """
    if not isinstance(entry, CatalogEntry):
        raise TypeError(
            f"register_kind_from_plugin expected CatalogEntry; got {type(entry).__name__}"
        )
    if entry.kind != kind:
        raise ValueError(
            f"plugin model provider mismatch: factory returned kind="
            f"{entry.kind!r}, register_model_provider argument was {kind!r}"
        )
    if kind in _BUILTIN_KINDS:
        raise ValueError(f"plugin cannot override builtin model provider kind: {kind!r}")
    if kind in _PLUGIN_REGISTERED_KINDS:
        raise ValueError(
            f"plugin model provider kind {kind!r} already registered; reload "
            "the plugin via the admin console to install a fresh instance"
        )
    _BY_KIND[kind] = entry
    for alias in entry.aliases:
        if alias in _BUILTIN_KINDS:
            raise ValueError(f"plugin model provider alias {alias!r} collides with a builtin kind")
        _BY_KIND[alias] = entry
    _PLUGIN_REGISTERED_KINDS.add(kind)


def is_plugin_kind(kind: str) -> bool:
    """Whether ``kind`` was contributed by a plugin (vs. bundled)."""
    return kind in _PLUGIN_REGISTERED_KINDS
