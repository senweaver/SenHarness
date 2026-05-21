"""Probe pydantic-ai-shields API."""
from __future__ import annotations

import inspect

from pydantic_ai_shields import (
    BlockedKeywords,
    BudgetExceededError,
    CostInfo,
    CostTracking,
    InputGuard,
    NoRefusals,
    OutputGuard,
    PiiDetector,
    PromptInjection,
    SecretRedaction,
    ToolGuard,
)

PROBES = [
    InputGuard,
    OutputGuard,
    ToolGuard,
    PiiDetector,
    PromptInjection,
    SecretRedaction,
    BlockedKeywords,
    NoRefusals,
    CostTracking,
    CostInfo,
    BudgetExceededError,
]

for cls in PROBES:
    print(f"=== {cls.__name__} ===")
    try:
        print("  sig:", inspect.signature(cls))
    except (ValueError, TypeError) as e:
        print(f"  sig: <{e}>")
    doc = (cls.__doc__ or "").strip().splitlines()
    if doc:
        print("  doc:", doc[0][:180])
    pub = [x for x in dir(cls) if not x.startswith("_")]
    print("  members:", pub[:12])
    print()
