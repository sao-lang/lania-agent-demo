"""计算器能力实现。

提供安全的数学表达式求值，使用受限的 eval 环境防止任意代码执行。
"""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass


@dataclass
class CalculationResult:
    """计算结果。"""
    expression: str
    result: float
    explanation: str = ''


# 安全运算白名单
_SAFE_OPERATORS: dict[type, object] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# 安全常量
_SAFE_CONSTANTS = {
    'pi': math.pi,
    'e': math.e,
    'inf': math.inf,
    'nan': math.nan,
}

# 安全函数
_SAFE_FUNCTIONS = {
    'abs': abs,
    'round': round,
    'min': min,
    'max': max,
    'sum': sum,
    'sqrt': math.sqrt,
    'sin': math.sin,
    'cos': math.cos,
    'tan': math.tan,
    'asin': math.asin,
    'acos': math.acos,
    'atan': math.atan,
    'log': math.log,
    'log10': math.log10,
    'log2': math.log2,
    'exp': math.exp,
    'ceil': math.ceil,
    'floor': math.floor,
    'trunc': math.trunc,
    'degrees': math.degrees,
    'radians': math.radians,
    'factorial': math.factorial,
}


class SafeEvaluator(ast.NodeVisitor):
    """安全 AST 求值器，仅允许白名单操作。"""

    def __init__(self) -> None:
        self._names: dict[str, object] = {**_SAFE_CONSTANTS, **_SAFE_FUNCTIONS}

    def visit_Expression(self, node: ast.Expression) -> object:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> object:
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f'unsupported constant: {type(node.value)}')

    def visit_Name(self, node: ast.Name) -> object:
        if node.id in self._names:
            return self._names[node.id]
        raise NameError(f'name not allowed: {node.id}')

    def visit_UnaryOp(self, node: ast.UnaryOp) -> object:
        op_func = _SAFE_OPERATORS.get(type(node.op))
        if op_func is None:
            raise ValueError(f'unsupported operator: {type(node.op).__name__}')
        return op_func(self.visit(node.operand))  # type: ignore[operator]

    def visit_BinOp(self, node: ast.BinOp) -> object:
        op_func = _SAFE_OPERATORS.get(type(node.op))
        if op_func is None:
            raise ValueError(f'unsupported operator: {type(node.op).__name__}')
        return op_func(self.visit(node.left), self.visit(node.right))  # type: ignore[operator]

    def visit_Call(self, node: ast.Call) -> object:
        if not isinstance(node.func, ast.Name):
            raise ValueError('only simple function calls allowed')
        func = self._names.get(node.func.id)
        if func is None:
            raise NameError(f'function not allowed: {node.func.id}')
        args = [self.visit(arg) for arg in node.args]
        return func(*args)

    def visit(self, node: ast.AST) -> object:  # type: ignore[override]
        node_type = type(node).__name__
        if node_type not in ('Expression', 'Constant', 'Name', 'UnaryOp', 'BinOp', 'Call', 'Load'):
            raise ValueError(f'node type not allowed: {node_type}')
        return super().visit(node)


class CalculatorCapability:
    """计算器能力，支持安全的数学表达式求值。"""

    name = 'calculator'

    def calculate(self, expression: str) -> CalculationResult:
        """计算数学表达式。

        支持：四则运算 +-*/、幂运算 **、数学函数（sqrt, sin, cos 等）、
        常量（pi, e）、括号分组。

        Args:
            expression: 数学表达式字符串。

        Returns:
            CalculationResult 结果。
        """
        try:
            tree = ast.parse(expression.strip(), mode='eval')
            evaluator = SafeEvaluator()
            result = evaluator.visit(tree)
            if not isinstance(result, (int, float)):
                raise ValueError('expression did not evaluate to a number')
            return CalculationResult(
                expression=expression,
                result=round(float(result), 10),
                explanation=f'计算结果: {result}',
            )
        except (SyntaxError, ValueError, NameError, ZeroDivisionError) as exc:
            raise ValueError(f'invalid expression: {exc}') from exc
