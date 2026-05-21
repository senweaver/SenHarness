"""Built-in agent template catalog.

Single source of truth for the **17 categories** the marketplace shows
in its sidebar plus the optional curated-tag overrides for individual
templates. Tags not listed here fall back to a deterministic derivation
from the template's slug (see ``loader.derive_tags``).

The data here is data-only — no I/O, no DB access — so it's safe to
import at module load time (e.g. from API routes that need the category
labels).
"""

from __future__ import annotations

CATEGORIES: list[dict[str, str]] = [
    {"slug": "academic", "name_cn": "学术研究", "name_en": "Academic"},
    {"slug": "design", "name_cn": "设计创意", "name_en": "Design"},
    {"slug": "engineering", "name_cn": "工程研发", "name_en": "Engineering"},
    {"slug": "finance", "name_cn": "财务金融", "name_en": "Finance"},
    {"slug": "game-development", "name_cn": "游戏开发", "name_en": "Game Development"},
    {"slug": "hr", "name_cn": "人力资源", "name_en": "Human Resources"},
    {"slug": "legal", "name_cn": "法务合规", "name_en": "Legal"},
    {"slug": "marketing", "name_cn": "市场营销", "name_en": "Marketing"},
    {"slug": "paid-media", "name_cn": "效果广告", "name_en": "Paid Media"},
    {"slug": "product", "name_cn": "产品管理", "name_en": "Product"},
    {"slug": "project-management", "name_cn": "项目管理", "name_en": "Project Management"},
    {"slug": "sales", "name_cn": "销售业务", "name_en": "Sales"},
    {"slug": "spatial-computing", "name_cn": "空间计算", "name_en": "Spatial Computing"},
    {"slug": "specialized", "name_cn": "行业专项", "name_en": "Specialized"},
    {"slug": "supply-chain", "name_cn": "供应链", "name_en": "Supply Chain"},
    {"slug": "support", "name_cn": "运营支持", "name_en": "Support"},
    {"slug": "testing", "name_cn": "测试品质", "name_en": "Testing"},
]

CATEGORY_BY_SLUG: dict[str, dict[str, str]] = {c["slug"]: c for c in CATEGORIES}

# Words to drop from auto-derived tags (mostly noise).
_TAG_STOPWORDS: frozenset[str] = frozenset(
    {
        "agent",
        "specialist",
        "expert",
        "engineer",
        "designer",
        "manager",
        "operator",
        "strategist",
        "architect",
        "developer",
        "consultant",
        "advisor",
        "coach",
        "analyst",
        "writer",
        "builder",
        "creator",
        "tracker",
        "checker",
        "reviewer",
        "responder",
        "scripter",
        "auditor",
        "tester",
        "specialized",
    }
)

# Curated tag overrides. Slugs not listed here fall back to
# ``loader.derive_tags`` (which splits the slug minus the category
# prefix, drops stopwords, and keeps up to 5 segments). Tags should be
# short, lowercase, and reusable across multiple agents — they power
# the ``?tag=`` filter chips.
TAGS_BY_SLUG: dict[str, list[str]] = {
    # ─── engineering ──────────────────────────────
    "engineering-frontend-developer": ["frontend", "react", "vue", "ui"],
    "engineering-backend-architect": ["backend", "architecture", "api"],
    "engineering-ai-engineer": ["ai", "llm", "ml"],
    "engineering-mobile-app-builder": ["mobile", "ios", "android"],
    "engineering-data-engineer": ["data", "etl", "pipeline"],
    "engineering-database-optimizer": ["database", "sql", "performance"],
    "engineering-devops-automator": ["devops", "ci-cd", "automation"],
    "engineering-sre": ["sre", "reliability", "ops"],
    "engineering-security-engineer": ["security", "appsec"],
    "engineering-threat-detection-engineer": ["security", "siem", "soc"],
    "engineering-incident-response-commander": ["incident", "ops", "sre"],
    "engineering-rapid-prototyper": ["prototype", "mvp", "speed"],
    "engineering-senior-developer": ["senior", "code-quality"],
    "engineering-software-architect": ["architecture", "design"],
    "engineering-code-reviewer": ["review", "quality", "code"],
    "engineering-codebase-onboarding-engineer": ["onboarding", "codebase"],
    "engineering-cms-developer": ["cms", "content"],
    "engineering-technical-writer": ["docs", "writing"],
    "engineering-git-workflow-master": ["git", "workflow"],
    "engineering-minimal-change-engineer": ["refactor", "minimal"],
    "engineering-iot-solution-architect": ["iot", "embedded"],
    "engineering-embedded-firmware-engineer": ["firmware", "embedded"],
    "engineering-embedded-linux-driver-engineer": ["embedded", "linux", "driver"],
    "engineering-fpga-digital-design-engineer": ["fpga", "hardware", "rtl"],
    "engineering-filament-optimization-specialist": ["3d-print", "filament"],
    "engineering-solidity-smart-contract-engineer": ["blockchain", "solidity", "web3"],
    "engineering-voice-ai-integration-engineer": ["voice", "ai", "asr"],
    "engineering-feishu-integration-developer": ["feishu", "integration"],
    "engineering-dingtalk-integration-developer": ["dingtalk", "integration"],
    "engineering-wechat-mini-program-developer": ["wechat", "mini-program"],
    "engineering-email-intelligence-engineer": ["email", "automation"],
    "engineering-ai-data-remediation-engineer": ["ai", "data-quality"],
    "engineering-autonomous-optimization-architect": ["autonomy", "optimization"],
    # ─── design ───────────────────────────────────
    "design-ui-designer": ["ui", "visual"],
    "design-ux-architect": ["ux", "architecture"],
    "design-ux-researcher": ["ux", "research"],
    "design-brand-guardian": ["brand", "guidelines"],
    "design-visual-storyteller": ["visual", "storytelling"],
    "design-whimsy-injector": ["delight", "microinteractions"],
    "design-image-prompt-engineer": ["image", "prompt", "ai"],
    "design-inclusive-visuals-specialist": ["inclusive", "a11y"],
    # ─── marketing ────────────────────────────────
    "marketing-seo-specialist": ["seo", "search"],
    "marketing-baidu-seo-specialist": ["seo", "baidu"],
    "marketing-content-creator": ["content"],
    "marketing-social-media-strategist": ["social"],
    "marketing-tiktok-strategist": ["tiktok", "social"],
    "marketing-douyin-strategist": ["douyin", "short-video"],
    "marketing-bilibili-strategist": ["bilibili", "video"],
    "marketing-kuaishou-strategist": ["kuaishou", "short-video"],
    "marketing-weibo-strategist": ["weibo", "social"],
    "marketing-xiaohongshu-operator": ["xiaohongshu", "social"],
    "marketing-xiaohongshu-specialist": ["xiaohongshu", "social"],
    "marketing-zhihu-strategist": ["zhihu", "qa"],
    "marketing-wechat-operator": ["wechat", "social"],
    "marketing-wechat-official-account": ["wechat", "publishing"],
    "marketing-weixin-channels-strategist": ["weixin-channels", "video"],
    "marketing-twitter-engager": ["twitter", "social"],
    "marketing-linkedin-content-creator": ["linkedin", "b2b"],
    "marketing-instagram-curator": ["instagram", "visual"],
    "marketing-reddit-community-builder": ["reddit", "community"],
    "marketing-podcast-strategist": ["podcast", "audio"],
    "marketing-app-store-optimizer": ["aso", "mobile"],
    "marketing-growth-hacker": ["growth"],
    "marketing-china-ecommerce-operator": ["ecommerce", "china"],
    "marketing-cross-border-ecommerce": ["ecommerce", "cross-border"],
    "marketing-ecommerce-operator": ["ecommerce"],
    "marketing-livestream-commerce-coach": ["livestream", "ecommerce"],
    "marketing-knowledge-commerce-strategist": ["knowledge", "ecommerce"],
    "marketing-private-domain-operator": ["private-domain", "crm"],
    "marketing-china-market-localization-strategist": ["china", "localization"],
    "marketing-agentic-search-optimizer": ["aeo", "ai-search"],
    "marketing-ai-citation-strategist": ["ai-search", "citation"],
    "marketing-carousel-growth-engine": ["carousel", "growth"],
    "marketing-short-video-editing-coach": ["short-video", "editing"],
    "marketing-video-optimization-specialist": ["video", "optimization"],
    "marketing-book-co-author": ["writing", "book"],
    # ─── product ──────────────────────────────────
    "product-manager": ["pm", "product"],
    "product-feedback-synthesizer": ["feedback", "research"],
    "product-trend-researcher": ["trend", "research"],
    "product-sprint-prioritizer": ["sprint", "prioritization"],
    "product-behavioral-nudge-engine": ["behavior", "nudge"],
    # ─── project-management ───────────────────────
    "project-manager-senior": ["pm", "senior"],
    "project-management-jira-workflow-steward": ["jira", "workflow"],
    "project-management-experiment-tracker": ["experiment", "ab-test"],
    "project-management-project-shepherd": ["project", "delivery"],
    "project-management-studio-operations": ["studio", "ops"],
    "project-management-studio-producer": ["studio", "producer"],
    # ─── finance ──────────────────────────────────
    "finance-bookkeeper-controller": ["bookkeeping", "controller"],
    "finance-financial-analyst": ["financial", "analysis"],
    "finance-financial-forecaster": ["forecast"],
    "finance-fpa-analyst": ["fpa"],
    "finance-fraud-detector": ["fraud", "risk"],
    "finance-investment-researcher": ["investment", "research"],
    "finance-invoice-manager": ["invoice", "ar"],
    "finance-tax-strategist": ["tax"],
    # ─── sales ────────────────────────────────────
    "sales-account-strategist": ["account"],
    "sales-coach": ["coach"],
    "sales-deal-strategist": ["deal", "negotiation"],
    "sales-discovery-coach": ["discovery"],
    "sales-engineer": ["se", "presales"],
    "sales-outbound-strategist": ["outbound", "prospecting"],
    "sales-pipeline-analyst": ["pipeline", "analysis"],
    "sales-proposal-strategist": ["proposal", "rfp"],
    # ─── paid-media ───────────────────────────────
    "paid-media-auditor": ["audit"],
    "paid-media-creative-strategist": ["creative"],
    "paid-media-paid-social-strategist": ["paid-social"],
    "paid-media-ppc-strategist": ["ppc", "search-ads"],
    "paid-media-programmatic-buyer": ["programmatic"],
    "paid-media-search-query-analyst": ["search", "query"],
    "paid-media-tracking-specialist": ["tracking", "analytics"],
    # ─── hr ───────────────────────────────────────
    "hr-recruiter": ["recruit", "hiring"],
    "hr-performance-reviewer": ["performance", "review"],
    # ─── legal ────────────────────────────────────
    "legal-contract-reviewer": ["contract", "review"],
    "legal-policy-writer": ["policy", "writing"],
    # ─── academic ─────────────────────────────────
    "academic-anthropologist": ["anthropology"],
    "academic-geographer": ["geography"],
    "academic-historian": ["history"],
    "academic-narratologist": ["narrative"],
    "academic-psychologist": ["psychology"],
    "academic-study-planner": ["study", "planner"],
    # ─── support ──────────────────────────────────
    "support-analytics-reporter": ["analytics", "reporting"],
    "support-executive-summary-generator": ["executive", "summary"],
    "support-finance-tracker": ["finance", "tracking"],
    "support-infrastructure-maintainer": ["infra", "maintenance"],
    "support-legal-compliance-checker": ["compliance"],
    "support-recruitment-specialist": ["recruit"],
    "support-supply-chain-strategist": ["supply-chain"],
    "support-support-responder": ["support", "responder"],
    # ─── testing ──────────────────────────────────
    "testing-accessibility-auditor": ["a11y", "audit"],
    "testing-api-tester": ["api", "test"],
    "testing-embedded-qa-engineer": ["embedded", "qa"],
    "testing-evidence-collector": ["evidence", "qa"],
    "testing-performance-benchmarker": ["performance", "benchmark"],
    "testing-reality-checker": ["sanity", "qa"],
    "testing-test-results-analyzer": ["test", "analysis"],
    "testing-tool-evaluator": ["tooling", "evaluation"],
    "testing-workflow-optimizer": ["workflow", "optimization"],
    # ─── supply-chain ─────────────────────────────
    "supply-chain-inventory-forecaster": ["inventory", "forecast"],
    "supply-chain-route-optimizer": ["route", "logistics"],
    "supply-chain-vendor-evaluator": ["vendor", "evaluation"],
    # ─── spatial-computing ────────────────────────
    "macos-spatial-metal-engineer": ["macos", "metal", "spatial"],
    "terminal-integration-specialist": ["terminal", "integration"],
    "visionos-spatial-engineer": ["visionos", "spatial"],
    "xr-cockpit-interaction-specialist": ["xr", "cockpit"],
    "xr-immersive-developer": ["xr", "immersive"],
    "xr-interface-architect": ["xr", "ui"],
    # ─── game-development ─────────────────────────
    "game-audio-engineer": ["game", "audio"],
    "game-designer": ["game", "design"],
    "level-designer": ["game", "level"],
    "narrative-designer": ["game", "narrative"],
    "technical-artist": ["game", "tech-art"],
    "blender-addon-engineer": ["blender", "3d"],
    "godot-gameplay-scripter": ["godot", "gameplay"],
    "godot-multiplayer-engineer": ["godot", "multiplayer"],
    "godot-shader-developer": ["godot", "shader"],
    "roblox-avatar-creator": ["roblox", "avatar"],
    "roblox-experience-designer": ["roblox", "experience"],
    "roblox-systems-scripter": ["roblox", "systems"],
    "unity-architect": ["unity", "architecture"],
    "unity-editor-tool-developer": ["unity", "editor", "tooling"],
    "unity-multiplayer-engineer": ["unity", "multiplayer"],
    "unity-shader-graph-artist": ["unity", "shader"],
    "unreal-multiplayer-architect": ["unreal", "multiplayer"],
    "unreal-systems-engineer": ["unreal", "systems"],
    "unreal-technical-artist": ["unreal", "tech-art"],
    "unreal-world-builder": ["unreal", "world"],
    # ─── specialized ──────────────────────────────
    "accounts-payable-agent": ["finance", "ap"],
    "agentic-identity-trust": ["identity", "trust"],
    "agents-orchestrator": ["orchestration", "multi-agent"],
    "automation-governance-architect": ["automation", "governance"],
    "blockchain-security-auditor": ["blockchain", "security"],
    "compliance-auditor": ["compliance", "audit"],
    "corporate-training-designer": ["training", "l-and-d"],
    "data-consolidation-agent": ["data"],
    "gaokao-college-advisor": ["education", "gaokao"],
    "government-digital-presales-consultant": ["government", "presales"],
    "healthcare-customer-service": ["healthcare", "cs"],
    "healthcare-marketing-compliance": ["healthcare", "compliance"],
    "hospitality-guest-services": ["hospitality"],
    "hr-onboarding": ["hr", "onboarding"],
    "identity-graph-operator": ["identity"],
    "language-translator": ["translation"],
    "legal-billing-time-tracking": ["legal", "billing"],
    "legal-client-intake": ["legal", "intake"],
    "legal-document-review": ["legal", "review"],
    "loan-officer-assistant": ["finance", "loan"],
    "lsp-index-engineer": ["lsp", "indexing"],
    "prompt-engineer": ["prompt", "ai"],
    "real-estate-buyer-seller": ["real-estate"],
    "recruitment-specialist": ["recruit"],
    "report-distribution-agent": ["reporting"],
    "retail-customer-returns": ["retail", "returns"],
    "sales-data-extraction-agent": ["sales", "data"],
    "specialized-ai-policy-writer": ["ai", "policy"],
    "specialized-chief-of-staff": ["chief-of-staff"],
    "specialized-civil-engineer": ["civil-engineering"],
    "specialized-cultural-intelligence-strategist": ["culture", "strategy"],
    "specialized-developer-advocate": ["devrel"],
    "specialized-document-generator": ["document"],
    "specialized-french-consulting-market": ["france", "consulting"],
    "specialized-korean-business-navigator": ["korea", "business"],
    "specialized-mcp-builder": ["mcp", "tooling"],
    "specialized-meeting-assistant": ["meeting"],
    "specialized-model-qa": ["llm", "qa"],
    "specialized-pricing-optimizer": ["pricing"],
    "specialized-risk-assessor": ["risk"],
    "specialized-salesforce-architect": ["salesforce"],
    "specialized-workflow-architect": ["workflow"],
    "study-abroad-advisor": ["education", "study-abroad"],
    "technical-translator-agent": ["translation", "technical"],
    "zk-steward": ["knowledge", "zettelkasten"],
}


def derive_tags(slug: str, category: str, *, max_tags: int = 5) -> list[str]:
    """Fallback when ``slug`` is not in :data:`TAGS_BY_SLUG`.

    Splits ``slug`` on ``-``, drops the category prefix segments, drops
    stopwords (see :data:`_TAG_STOPWORDS`), preserves order, and caps at
    ``max_tags``. Always returns lowercase strings.
    """
    parts = [p for p in slug.split("-") if p]
    cat_parts = category.split("-") if category else []
    if cat_parts and parts[: len(cat_parts)] == cat_parts:
        parts = parts[len(cat_parts) :]

    out: list[str] = []
    for p in parts:
        if p in _TAG_STOPWORDS:
            continue
        if p in out:
            continue
        out.append(p)
        if len(out) >= max_tags:
            break
    return out


def get_tags(slug: str, category: str) -> list[str]:
    """Curated tags first; fall back to derived ones."""
    if slug in TAGS_BY_SLUG:
        return TAGS_BY_SLUG[slug]
    return derive_tags(slug, category)
