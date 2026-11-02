"""apps/calc_div/engine.py — 순수 Standalone 나눗셈 연산 객체."""

class DivEngine:
    """플랫폼 비의존적인 순수 독립 나눗셈 연산 클래스."""

    def execute(self, a: float, b: float) -> dict:
        if b == 0:
            raise ValueError("0으로 나눌 수 없습니다.")
        ans = a / b
        fmt_a = int(a) if a.is_integer() else a
        fmt_b = int(b) if b.is_integer() else b
        fmt_ans = int(ans) if ans.is_integer() else round(ans, 4)
        return {
            "a": fmt_a,
            "b": fmt_b,
            "op": "/",
            "result": fmt_ans,
            "formula": f"{fmt_a} / {fmt_b} = {fmt_ans}"
        }
