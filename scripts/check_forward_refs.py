"""운영(Python 3.11)과 같은 '즉시 평가' 규칙에서 터지는 이름 순서 오류를 정적으로 찾는다.

Python 3.14 는 함수·필드 타입 표기를 나중에 평가해서 아래 같은 코드도 돌아가지만, 3.11~3.13 은 정의 시점에 평가해 NameError 가 난다.
    @router.post("/x")
    def f(req: Req): ...      # Req 가 아래쪽에서 정의되면 3.11 에서 NameError → 앱 전체가 격리된다
    class Req(BaseModel): ...
사용:  py scripts/check_forward_refs.py [폴더...]   (기본: apps core)   문제가 있으면 종료 코드 1
"""
import ast
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def check(path: str):
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    if any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and any(a.name == "annotations" for a in n.names) for n in tree.body):
        return []                                    # 문자열처럼 늦게 평가되므로 안전
    defined_at = {}                                  # 모듈 최상위 이름 → 정의된 줄
    for n in tree.body:
        names = []
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names = [n.name]
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [(a.asname or a.name).split(".")[0] for a in n.names]
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            tg = n.targets if isinstance(n, ast.Assign) else [n.target]
            names = [t.id for t in tg if isinstance(t, ast.Name)]
        for nm in names:
            defined_at.setdefault(nm, n.lineno)

    problems = []

    def names_in(ann):
        return [x for x in ast.walk(ann) if isinstance(x, ast.Name)]

    def visit_annotations(node, top_line):
        anns = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
                if arg.annotation is not None:
                    anns.append(arg.annotation)
            if node.returns is not None:
                anns.append(node.returns)
        for sub in anns:
            for nm in names_in(sub):
                line = defined_at.get(nm.id)
                if line is not None and line > top_line:
                    problems.append((path, nm.lineno, nm.id, line))

    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit_annotations(n, n.lineno)
        elif isinstance(n, ast.ClassDef):
            for sub in n.body:                       # 클래스 안 메서드·필드 표기도 클래스 정의 시점에 평가된다
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit_annotations(sub, n.lineno)
                elif isinstance(sub, ast.AnnAssign):
                    for nm in names_in(sub.annotation):
                        line = defined_at.get(nm.id)
                        if line is not None and line > n.lineno:
                            problems.append((path, nm.lineno, nm.id, line))
    return problems


def main() -> int:
    dirs = sys.argv[1:] or ["apps", "core"]
    bad = []
    for d in dirs:
        for cur, _ds, fs in os.walk(os.path.join(ROOT, d)):
            if any(x in cur for x in ("node_modules", "__pycache__", ".git")):
                continue
            for f in fs:
                if f.endswith(".py"):
                    bad += check(os.path.join(cur, f))
    for p, line, nm, dl in bad:
        print(f"{os.path.relpath(p, ROOT)}:{line}  '{nm}' 이(가) 아래({dl}줄)에서 정의됨 — Python 3.11 에서 NameError")
    print(f"점검 완료: 문제 {len(bad)}건")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
