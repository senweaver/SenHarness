"""`calculator` tool — safely evaluate arithmetic expressions."""

from __future__ import annotations

import ast
import operator as op
from typing import Any

from pydantic import BaseModel, Field

_OPS: dict[type[ast.AST], Any] = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}

_MAX_NODES = 200


class CalculatorArgs(BaseModel):
    expression: str = Field(..., description="Arithmetic expression e.g. '(1+2)*3**4/5 - 6'")


def _eval(node: ast.AST, count: list[int]) -> float:
    count[0] += 1
    if count[0] > _MAX_NODES:
        raise ValueError("expression too complex")
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left, count), _eval(node.right, count))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand, count))
    raise ValueError(f"unsupported node: {type(node).__name__}")


def run_calculator(args: CalculatorArgs) -> dict:
    try:
        tree = ast.parse(args.expression, mode="eval")
        value = _eval(tree.body, [0])
        return {"expression": args.expression, "value": value}
    except (SyntaxError, ValueError, ZeroDivisionError) as e:
        return {"error": str(e), "expression": args.expression}
