"""In-chat command parsing for channel multi-agent routing (P0).

Commands carry a **stable internal code** (``agents.list``, ``agents.use``,
…) and a set of **localized trigger words** (zh + en), mirroring the
project's error-code-localization convention: the dispatcher only ever
switches on ``code`` while operators/users type whatever reads naturally.

Rules:
* The prefix is ``/`` (case-insensitive, leading/trailing space trimmed).
* A bare ``?`` and the greetings ``hi`` / ``help`` / ``帮助`` trigger the
  welcome/help flow (onboarding).
* A leading ``@<alias>`` is a *mention*: it selects an agent and the
  remainder of the line is forwarded to that agent as the actual message.
* An unrecognised ``/x`` resolves to :data:`CMD_UNKNOWN` so the dispatcher
  can nudge the user toward ``/help`` instead of feeding ``/x`` to an agent.

Commands never enter the agent — the dispatcher answers them through the
presenter. The exception is a mention that carries trailing text, where
the switch happens here and the trailing text is run by the agent.
"""

from __future__ import annotations

from dataclasses import dataclass

# ─── Stable internal command codes ───────────────────────────
CMD_AGENTS_LIST = "agents.list"
CMD_AGENTS_USE = "agents.use"
CMD_WHOAMI = "agents.whoami"
CMD_RESET = "session.reset"
CMD_WS_SWITCH = "ws.switch"
CMD_BIND = "identity.bind"
CMD_HELP = "help"
CMD_MENTION = "mention"
CMD_UNKNOWN = "unknown"

# trigger word (lowercased, without the leading slash) → code.
_SLASH_TRIGGERS: dict[str, str] = {
    "agents": CMD_AGENTS_LIST,
    "list": CMD_AGENTS_LIST,
    "助手": CMD_AGENTS_LIST,
    "agent": CMD_AGENTS_USE,
    "use": CMD_AGENTS_USE,
    "换": CMD_AGENTS_USE,
    "whoami": CMD_WHOAMI,
    "当前": CMD_WHOAMI,
    "reset": CMD_RESET,
    "重置": CMD_RESET,
    "新会话": CMD_RESET,
    "ws": CMD_WS_SWITCH,
    "工作区": CMD_WS_SWITCH,
    "bind": CMD_BIND,
    "绑定": CMD_BIND,
    "help": CMD_HELP,
    "帮助": CMD_HELP,
}

# Bare (no-slash) greeting words that open the welcome flow.
_GREETING_TRIGGERS: frozenset[str] = frozenset({"?", "？", "hi", "help", "帮助", "你好"})


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """Result of :func:`parse_command`.

    ``arg`` is the single positional argument where relevant (the alias /
    number for ``agents.use``, the code for ``identity.bind``, the optional
    workspace name for ``ws.switch``). ``text`` carries trailing free text —
    only meaningful for a mention (the message body to forward to the
    selected agent).
    """

    code: str
    arg: str | None = None
    text: str | None = None


def parse_command(raw_text: str) -> ParsedCommand | None:
    """Parse ``raw_text`` into a :class:`ParsedCommand`, or ``None``.

    ``None`` means "this is an ordinary message, route it to an agent".
    """
    if raw_text is None:
        return None
    text = raw_text.strip()
    if not text:
        return None

    # Leading mention: ``@alias rest of message``.
    if text.startswith("@"):
        body = text[1:].lstrip()
        if not body:
            return None
        alias, _, trailing = body.partition(" ")
        alias = alias.strip()
        if not alias:
            return None
        return ParsedCommand(code=CMD_MENTION, arg=alias, text=trailing.strip() or None)

    lowered = text.lower()

    # Bare greeting / help triggers (no slash).
    if lowered in _GREETING_TRIGGERS or text in _GREETING_TRIGGERS:
        return ParsedCommand(code=CMD_HELP)

    if not text.startswith("/"):
        return None

    # ``/word arg…``
    body = text[1:].strip()
    if not body:
        return ParsedCommand(code=CMD_UNKNOWN)
    word, _, rest = body.partition(" ")
    code = _SLASH_TRIGGERS.get(word.lower())
    rest = rest.strip()
    if code is None:
        return ParsedCommand(code=CMD_UNKNOWN, arg=word)
    return ParsedCommand(code=code, arg=rest or None)


__all__ = [
    "CMD_AGENTS_LIST",
    "CMD_AGENTS_USE",
    "CMD_BIND",
    "CMD_HELP",
    "CMD_MENTION",
    "CMD_RESET",
    "CMD_UNKNOWN",
    "CMD_WHOAMI",
    "CMD_WS_SWITCH",
    "ParsedCommand",
    "parse_command",
]
