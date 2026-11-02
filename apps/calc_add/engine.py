"""apps/calc_add/engine.py — 순수 Standalone 덧셈 연산 객체."""

class AddEngine:
    """플랫폼 비의존적인 순수 독립 연산 클래스."""

    def execute(self, a: float, b: float) -> dict:
        ans = a + b
        # 깔끔한 정수 표시 지원 (예: 3.0 -> 3)
        fmt_a = int(a) if a.is_integer() else a
        fmt_b = int(b) if b.is_integer() else b
        fmt_ans = int(ans) if ans.is_integer() else ans
        return {
            "a": fmt_a,
            "b": fmt_b,
            "op": "+",
            "result": fmt_ans,
            "formula": f"{fmt_a} + {fmt_b} = {fmt_ans}"
        }
