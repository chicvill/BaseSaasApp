"""apps/calc_expr/engine.py — 순수 Standalone 수식 괄호 연산 객체."""
import ast
import operator

# 안전한 연산자 매핑
SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


class ExprEngine:
    """ast 기반의 안전하고 격리된 수식 파서 (eval 미사용 보안 표준)."""

    def evaluate(self, expression: str) -> dict:
        expr_str = expression.strip()
        try:
            node = ast.parse(expr_str, mode='eval').body
            val = self._eval_node(node)
            fmt_val = int(val) if isinstance(val, (int, float)) and float(val).is_integer() else val
            return {
                "expr": expr_str,
                "result": fmt_val,
                "formula": f"{expr_str} = {fmt_val}"
            }
        except Exception as e:
            raise ValueError(f"수식 해석 실패: {e}")

    def _eval_node(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("숫자만 허용됩니다.")
        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in SAFE_OPERATORS:
                raise ValueError(f"지원되지 않는 연산자: {op_type}")
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            return SAFE_OPERATORS[op_type](left, right)
        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in SAFE_OPERATORS:
                raise ValueError(f"지원되지 않는 단항 연산자: {op_type}")
            return SAFE_OPERATORS[op_type](self._eval_node(node.operand))
        else:
            raise ValueError(f"허용되지 않는 구문: {type(node).__name__}")
