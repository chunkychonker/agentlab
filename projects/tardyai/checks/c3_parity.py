"""Checks that every C3 method declared with a js/py implementation exists in its source file.

C3 binds a `.c3typ` method marked `js` or `py` to a same-named function in the
sibling `<Type>.js` / `<Type>.py`. A declared method with no implementation
deploys cleanly and fails only when called from the Studio preview, so this is
checked offline instead.

Usage: python3 -I c3_parity.py <package-src-dir>
Exit 0 when every declared method is implemented; exit 1 listing each gap;
exit 2 when the directory is missing.
"""

import re
import sys
from pathlib import Path

# `name: function(...): ReturnType js` (optionally `js server`, `py`, etc.)
_METHOD = re.compile(r"^\s*(\w+)\s*:\s*function\s*\(.*\)\s*:.*?\b(js|py)\b", re.MULTILINE)
_JS_FUNC = re.compile(r"^\s*function\s+(\w+)\s*\(", re.MULTILINE)
_PY_FUNC = re.compile(r"^def\s+(\w+)\s*\(", re.MULTILINE)

_IMPL = {"js": (".js", _JS_FUNC), "py": (".py", _PY_FUNC)}


def declared_methods(c3typ_text: str) -> dict[str, str]:
    """Map each declared method name to its implementation language ('js' or 'py')."""
    return {m.group(1): m.group(2) for m in _METHOD.finditer(c3typ_text)}


def implemented(source_text: str, lang: str) -> set[str]:
    """Top-level function names defined in a js or py source file."""
    return set(_IMPL[lang][1].findall(source_text))


def gaps(src_dir: Path) -> list[str]:
    """Every '<Type>.<method>' declared but not implemented, plus missing source files."""
    problems = []
    for typ in sorted(src_dir.glob("*.c3typ")):
        methods = declared_methods(typ.read_text())
        for lang in sorted(set(methods.values())):
            source = typ.with_suffix(_IMPL[lang][0])
            wanted = {name for name, l in methods.items() if l == lang}
            if not source.exists():
                problems.append(f"{typ.stem}: declares {lang} methods {sorted(wanted)} but {source.name} is missing")
                continue
            for name in sorted(wanted - implemented(source.read_text(), lang)):
                problems.append(f"{typ.stem}.{name}: declared `{lang}` but not defined in {source.name}")
    return problems


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print("usage: c3_parity.py <package-src-dir>", file=sys.stderr)
        return 2
    problems = gaps(Path(sys.argv[1]))
    for p in problems:
        print(f"c3_parity: {p}")
    if not problems:
        print("c3_parity: OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
