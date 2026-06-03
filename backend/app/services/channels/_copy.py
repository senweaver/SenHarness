"""Backend-templated outbound copy for channel routing (P0).

Channel-facing strings (welcome, switch receipt, not-bound, not-open, …)
are rendered here, in the backend, following the existing "正在思考…"
precedent in :mod:`app.services.channel_dispatch` — they are *not* part of
the frontend ``messages/<locale>.json`` i18n surface (that's for the web
UI). Language is chosen per recipient:

    identity.profile_json.locale  →  workspace branding language  →  zh

Ships zh + en. The agent common-noun (``{term}``) is always the workspace
``branding.agent_term`` (default ``"agent"``) — never a hardcoded
"智能体" / "Agent" (root AGENTS.md rule #1).
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_LANG = "zh"
_SUPPORTED = ("zh", "en")


def pick_lang(*, identity_locale: str | None, workspace_lang: str | None) -> str:
    """Resolve the outbound language: identity → workspace → zh."""
    for candidate in (identity_locale, workspace_lang):
        if not candidate:
            continue
        head = str(candidate).strip().lower().replace("_", "-").split("-")[0]
        if head in _SUPPORTED:
            return head
        if head:  # a real but unsupported locale → fall through to next
            continue
    return DEFAULT_LANG


def _fmt_options(options: Sequence[tuple[int, str, str | None]]) -> str:
    lines = []
    for idx, name, desc in options:
        if desc:
            lines.append(f"{idx}. {name} · {desc}")
        else:
            lines.append(f"{idx}. {name}")
    return "\n".join(lines)


def welcome(
    *,
    lang: str,
    term: str,
    current_name: str,
    options: Sequence[tuple[int, str, str | None]],
    team: str | None = None,
) -> str:
    """Onboarding / ``/help`` copy: who you're talking to + numbered list.

    When ``team`` is set (squad scope, P2) the listing is framed as the
    team's members so the user knows they're talking to one team.
    """
    listing = _fmt_options(options)
    if lang == "en":
        intro = (
            f"👋 I'm \u201c{current_name}\u201d on the {term} team \u201c{team}\u201d."
            if team
            else f"👋 I'm the {term} \u201c{current_name}\u201d."
        )
        members_label = "Team members:" if team else f"Available {term}s:"
        return (
            f"{intro}\n\n"
            f"{members_label}\n{listing}\n\n"
            f"· Switch: reply a number (e.g. 2) or @name\n"
            f"· All: /agents   Help: /help"
        )
    intro = (
        f"👋 我是{term}团队「{team}」的「{current_name}」。"
        if team
        else f"👋 我是{term}「{current_name}」。"
    )
    members_label = "团队成员:" if team else f"你可使用的{term}:"
    return (
        f"{intro}\n\n"
        f"{members_label}\n{listing}\n\n"
        f"· 切换:回数字(如 2)或 @名称\n"
        f"· 全部:/agents   帮助:/help"
    )


def agents_list(
    *,
    lang: str,
    term: str,
    options: Sequence[tuple[int, str, str | None]],
    total: int | None = None,
    team: str | None = None,
) -> str:
    listing = _fmt_options(options)
    more = ""
    if total is not None and total > len(options):
        more = (
            f"\n\nTotal {total}. Send /agents to page."
            if lang == "en"
            else f"\n\n共 {total} 个,发 /agents 翻页。"
        )
    if lang == "en":
        label = f"Members of team \u201c{team}\u201d:" if team else f"Available {term}s:"
        return f"{label}\n{listing}{more}"
    label = f"团队「{team}」成员:" if team else f"你可使用的{term}:"
    return f"{label}\n{listing}{more}"


def switch_receipt(*, lang: str, name: str, team: str | None = None) -> str:
    if lang == "en":
        if team:
            return f"Switched to \u201c{name}\u201d on team \u201c{team}\u201d. How can it help?"
        return f"Switched to \u201c{name}\u201d. How can it help?"
    if team:
        return f"已切到团队「{team}」的「{name}」,请说说你的需求。"
    return f"已切到「{name}」,请说说你的需求。"


def whoami(*, lang: str, term: str, name: str, team: str | None = None) -> str:
    if lang == "en":
        if team:
            return f"You're talking to \u201c{name}\u201d on the {term} team \u201c{team}\u201d."
        return f"You're talking to the {term} \u201c{name}\u201d."
    if team:
        return f"你正在与{term}团队「{team}」的「{name}」对话。"
    return f"你正在与{term}「{name}」对话。"


def reset_done(*, lang: str) -> str:
    if lang == "en":
        return "Conversation reset. Back to the default."
    return "已重置会话,回到默认。"


def not_bound(*, lang: str) -> str:
    if lang == "en":
        return (
            "Your account isn't linked yet. Generate a code under "
            "\u201cChannels → Bind\u201d on the web, then reply /bind 123456."
        )
    return "尚未绑定账号。请在 Web 端「渠道 → 绑定」生成验证码,回复 /bind 123456 完成。"


def not_open(*, lang: str) -> str:
    if lang == "en":
        return "This conversation isn't open. Please ask an admin to enable it."
    return "当前未开放对话,请联系管理员开通。"


def bind_ok(*, lang: str) -> str:
    if lang == "en":
        return "Account linked. You can now switch and chat freely."
    return "绑定成功,现在可以自由切换与对话了。"


def bind_failed(*, lang: str) -> str:
    if lang == "en":
        return "That code is invalid or expired. Generate a new one and try again."
    return "验证码无效或已过期,请重新生成后再试。"


def unknown_command(*, lang: str) -> str:
    if lang == "en":
        return "Unknown command. Send /help to see what's available."
    return "无法识别的命令,发 /help 查看可用指令。"


def not_found(*, lang: str, term: str) -> str:
    if lang == "en":
        return f"No matching {term}. Send /agents to see the list."
    return f"没有匹配的{term},发 /agents 查看列表。"


def handoff_offer(*, lang: str, name: str) -> str:
    """Proactive handoff proposal (P1 ``mode=suggest`` keyword router)."""
    if lang == "en":
        return (
            f"Looks like \u201c{name}\u201d can help with this — "
            f"want me to transfer you? Reply @{name} or the menu number."
        )
    return f"看起来「{name}」更适合处理这个问题,要不要帮你转接?回复 @{name} 或菜单编号即可。"


def footer(*, lang: str) -> str:
    """Occasional lightweight hint appended to a normal reply."""
    if lang == "en":
        return "(send /agents to switch)"
    return "(发 /agents 可换助手)"


__all__ = [
    "DEFAULT_LANG",
    "agents_list",
    "bind_failed",
    "bind_ok",
    "footer",
    "handoff_offer",
    "not_bound",
    "not_found",
    "not_open",
    "pick_lang",
    "reset_done",
    "switch_receipt",
    "welcome",
    "whoami",
]
