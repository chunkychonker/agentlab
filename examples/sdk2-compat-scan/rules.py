"""Pure detection rules for Anthropic API ``2026-10-21`` / Python SDK 2.x exposure.

This module maps source text to findings. It never touches the filesystem,
the network, the clock, or the environment.

What it can and cannot tell you: every rule is a *static* pattern over literal
syntax. A call made through an alias (``create = client.messages.create``), a
keyword arriving through ``**kwargs``, or a message dict assembled at runtime is
invisible here. An empty result therefore means "no known pattern found", never
"safe to upgrade".
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    """How sure a rule is that the pattern breaks under 2026-10-21 / SDK 2.x."""

    ERROR = "error"  # documented 400 / TypeError / ValueError on the new version
    WARN = "warn"  # likely breakage, or a heuristic over literals only


class Rule(Enum):
    """Every rule the scanner knows, with its stable id and severity."""

    SAMPLING_PARAM = ("SAMPLING-PARAM", Severity.ERROR)
    MESSAGEPARAM_CALL = ("MESSAGEPARAM-CALL", Severity.ERROR)
    MODELS_BETAS = ("MODELS-BETAS", Severity.ERROR)
    DUAL_CREDENTIAL = ("DUAL-CREDENTIAL", Severity.ERROR)
    XAPIKEY_HEADER = ("XAPIKEY-HEADER", Severity.WARN)
    DOC_IN_ASSISTANT = ("DOC-IN-ASSISTANT", Severity.WARN)
    CITATIONS_IN_USER = ("CITATIONS-IN-USER", Severity.WARN)
    UNBOUNDED_PIN = ("UNBOUNDED-PIN", Severity.WARN)
    PARSE_ERROR = ("PARSE-ERROR", Severity.ERROR)

    def __init__(self, rule_id: str, severity: Severity) -> None:
        self.rule_id = rule_id
        self.severity = severity


_RULES_BY_ID = {rule.rule_id: rule for rule in Rule}


@dataclass(frozen=True)
class Finding:
    """One rule match at one 1-based line of one file.

    Raises ``ValueError`` on construction if ``line`` < 1 or ``message`` is
    empty, so a finding that points nowhere cannot exist.
    """

    rule: Rule
    path: str
    line: int
    message: str

    def __post_init__(self) -> None:
        if self.line < 1:
            raise ValueError(f"line must be >= 1, got {self.line}")
        if not self.message:
            raise ValueError("a finding must say what it found")


@dataclass(frozen=True)
class SourceReport:
    """Findings for one Python file, split by whether a pragma suppressed them."""

    findings: tuple[Finding, ...]
    suppressed: tuple[Finding, ...]


# --- names the rules key on (defined once) ----------------------------------

_SAMPLING_PARAMS = frozenset({"temperature", "top_p", "top_k"})
_MESSAGES_METHODS = frozenset({"create", "stream", "parse", "count_tokens", "tool_runner"})
_REQUEST_KEYS = frozenset({"max_tokens", "messages"})
_MESSAGEPARAM_NAMES = frozenset({"MessageParam", "BetaMessageParam"})
_CLIENT_CLASSES = frozenset({"Anthropic", "AsyncAnthropic"})
_CLIENT_REBUILDERS = frozenset({"copy", "with_options"})
_CREDENTIAL_KWARGS = frozenset({"api_key", "auth_token", "credentials", "config", "profile"})
_MODELS_METHODS = frozenset({"list", "retrieve"})
_XAPIKEY = "x-api-key"  # sdk2-compat: ignore[XAPIKEY-HEADER]
_PACKAGE = "anthropic"

_PRAGMA = re.compile(r"#\s*sdk2-compat:\s*ignore\[([^\]]*)\]")


# --- small AST helpers -------------------------------------------------------


def _callee_name(func: ast.expr) -> str | None:
    """``foo`` for ``foo(...)`` and ``a.b.foo(...)``; ``None`` for anything else."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _str_keys(node: ast.Dict) -> dict[str, ast.expr]:
    """The string-literal keys of a dict literal, mapped to their value nodes."""
    return {
        key.value: value
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _key_line(node: ast.Dict, name: str) -> int:
    for key in node.keys:
        if isinstance(key, ast.Constant) and key.value == name:
            return key.lineno
    return node.lineno


def _is_str(node: ast.expr | None, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _dict_items(node: ast.expr | None) -> list[ast.Dict]:
    """The dict-literal elements of a list/tuple literal; ``[]`` for anything else."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return [elt for elt in node.elts if isinstance(elt, ast.Dict)]
    return []


# --- per-node rules ----------------------------------------------------------


def _check_call(path: str, node: ast.Call) -> list[Finding]:
    found: list[Finding] = []
    name = _callee_name(node.func)
    kwargs = {kw.arg: kw for kw in node.keywords if kw.arg is not None}

    # SAMPLING-PARAM: <x>.messages.<create|stream|parse|count_tokens|tool_runner>(temperature=...)
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr in _MESSAGES_METHODS
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "messages"
    ):
        for param in sorted(_SAMPLING_PARAMS & kwargs.keys()):
            found.append(
                Finding(
                    Rule.SAMPLING_PARAM,
                    path,
                    kwargs[param].lineno,
                    f"`{param}=` on messages.{func.attr}: API 2026-10-21 returns 400 even at "
                    f"the default value, and SDK 2.x removed the kwarg",
                )
            )

    # MESSAGEPARAM-CALL: MessageParam(...) is a union in SDK 2.x, not a callable
    if name in _MESSAGEPARAM_NAMES:
        found.append(
            Finding(
                Rule.MESSAGEPARAM_CALL,
                path,
                node.lineno,
                f"`{name}(...)` raises TypeError in SDK 2.x (it is now a union of "
                f"per-role types); use a dict literal or UserMessageParam/AssistantMessageParam",
            )
        )

    # MODELS-BETAS: client.models.list/retrieve(betas=...) outside client.beta
    if (
        isinstance(func, ast.Attribute)
        and func.attr in _MODELS_METHODS
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "models"
        and not (isinstance(func.value.value, ast.Attribute) and func.value.value.attr == "beta")
        and "betas" in kwargs
    ):
        found.append(
            Finding(
                Rule.MODELS_BETAS,
                path,
                kwargs["betas"].lineno,
                f"`models.{func.attr}(betas=...)` raises TypeError in SDK 2.x; "
                f"call client.beta.models.{func.attr} instead",
            )
        )

    # DUAL-CREDENTIAL: two or more non-None credentials on one constructor/copy
    if name in _CLIENT_CLASSES or (
        isinstance(func, ast.Attribute) and func.attr in _CLIENT_REBUILDERS
    ):
        given = sorted(
            arg
            for arg in _CREDENTIAL_KWARGS & kwargs.keys()
            if not _is_none(kwargs[arg].value)
        )
        if len(given) >= 2:
            found.append(
                Finding(
                    Rule.DUAL_CREDENTIAL,
                    path,
                    node.lineno,
                    f"`{name}(...)` passes {', '.join(given)}: SDK 2.x allows one "
                    f"credential per client and raises ValueError",
                )
            )
    return found


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _check_class(path: str, node: ast.ClassDef) -> list[Finding]:
    return [
        Finding(
            Rule.MESSAGEPARAM_CALL,
            path,
            base.lineno,
            f"subclassing `{_callee_name(base)}` raises TypeError in SDK 2.x "
            f"(it is now a union, not a class)",
        )
        for base in node.bases
        if _callee_name(base) in _MESSAGEPARAM_NAMES
    ]


def _check_dict(path: str, node: ast.Dict) -> list[Finding]:
    found: list[Finding] = []
    keys = _str_keys(node)

    # SAMPLING-PARAM (batch / raw request params): sampling key beside max_tokens/messages
    if keys.keys() & _REQUEST_KEYS:
        for param in sorted(_SAMPLING_PARAMS & keys.keys()):
            found.append(
                Finding(
                    Rule.SAMPLING_PARAM,
                    path,
                    _key_line(node, param),
                    f'"{param}" in a request-params dict: API 2026-10-21 returns 400 '
                    f"even at the default value",
                )
            )

    role = keys.get("role")
    content = _dict_items(keys.get("content"))

    # DOC-IN-ASSISTANT: a document block directly in an assistant message
    if _is_str(role, "assistant"):
        for block in content:
            if _is_str(_str_keys(block).get("type"), "document"):
                found.append(
                    Finding(
                        Rule.DOC_IN_ASSISTANT,
                        path,
                        block.lineno,
                        "document block directly in an assistant message: API 2026-10-21 "
                        "returns 400 (nesting it in a tool_result is still accepted)",
                    )
                )

    # CITATIONS-IN-USER: non-empty citations on a text block in a user message,
    # directly or inside a search_result block
    if _is_str(role, "user"):
        for block in content:
            block_keys = _str_keys(block)
            candidates = [block]
            if _is_str(block_keys.get("type"), "search_result"):
                candidates.extend(_dict_items(block_keys.get("content")))
            for text_block in candidates:
                text_keys = _str_keys(text_block)
                citations = text_keys.get("citations")
                if (
                    _is_str(text_keys.get("type"), "text")
                    and isinstance(citations, (ast.List, ast.Tuple))
                    and citations.elts
                ):
                    found.append(
                        Finding(
                            Rule.CITATIONS_IN_USER,
                            path,
                            _key_line(text_block, "citations"),
                            "non-empty citations on a text block in a user message: "
                            "API 2026-10-21 returns 400",
                        )
                    )
    return found


def _check_constant(path: str, node: ast.Constant) -> list[Finding]:
    if isinstance(node.value, str) and node.value.strip().lower() == _XAPIKEY:
        return [
            Finding(
                Rule.XAPIKEY_HEADER,
                path,
                node.lineno,
                f"{node.value!r} literal: SDK 2.x sends the API key as "
                f"`Authorization: Bearer`, so code or proxies reading this header see nothing",
            )
        ]
    return []


# --- pragmas ------------------------------------------------------------------


def _pragmas(path: str, source: str) -> tuple[dict[int, frozenset[Rule]], list[Finding]]:
    """Per-line ``# sdk2-compat: ignore[RULE-ID, ...]`` pragmas.

    Only real comment tokens count, so the pragma text inside a string or a
    docstring (like this one) is not a pragma. Call only on source that
    ``ast.parse`` accepted, so tokenizing cannot fail.
    An unknown rule id or an empty bracket is reported as a PARSE-ERROR finding
    rather than ignored, so a typo cannot silently suppress nothing.
    """
    by_line: dict[int, frozenset[Rule]] = {}
    errors: list[Finding] = []
    comments = (
        (tok.start[0], tok.string)
        for tok in tokenize.generate_tokens(io.StringIO(source).readline)
        if tok.type == tokenize.COMMENT
    )
    for number, text in comments:
        match = _PRAGMA.search(text)
        if match is None:
            continue
        ids = [part.strip() for part in match.group(1).split(",") if part.strip()]
        unknown = [rule_id for rule_id in ids if rule_id not in _RULES_BY_ID]
        if not ids or unknown:
            errors.append(
                Finding(
                    Rule.PARSE_ERROR,
                    path,
                    number,
                    f"sdk2-compat pragma names no known rule id: {match.group(0)!r}",
                )
            )
            continue
        by_line[number] = frozenset(_RULES_BY_ID[rule_id] for rule_id in ids)
    return by_line, errors


# --- public entry points -------------------------------------------------------


def scan_source(path: str, source: str) -> SourceReport:
    """Scan one Python file's text for 2026-10-21 / SDK 2.x breaking patterns.

    ``path`` is only echoed into findings. Findings are ordered by line, then
    rule id. A finding whose line carries ``# sdk2-compat: ignore[ITS-ID]`` is
    moved to ``suppressed``; it is never dropped.

    Failure modes: a file that does not parse (``SyntaxError``, or
    ``ValueError`` for NUL bytes) yields a single PARSE-ERROR finding at the
    reported line instead of raising, so one broken file neither crashes a scan
    nor disappears from it. Misses (not errors): calls via aliases, keywords
    via ``**kwargs``, and messages built at runtime.
    """
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return SourceReport(
            (Finding(Rule.PARSE_ERROR, path, max(exc.lineno or 1, 1), f"not valid Python: {exc.msg}"),),
            (),
        )
    except ValueError as exc:
        return SourceReport((Finding(Rule.PARSE_ERROR, path, 1, f"not valid Python: {exc}"),), ())

    raw: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            raw.extend(_check_call(path, node))
        elif isinstance(node, ast.ClassDef):
            raw.extend(_check_class(path, node))
        elif isinstance(node, ast.Dict):
            raw.extend(_check_dict(path, node))
        elif isinstance(node, ast.Constant):
            raw.extend(_check_constant(path, node))

    pragmas, pragma_errors = _pragmas(path, source)
    kept = [f for f in raw if f.rule not in pragmas.get(f.line, frozenset())] + pragma_errors
    suppressed = [f for f in raw if f.rule in pragmas.get(f.line, frozenset())]
    return SourceReport(tuple(sorted(kept, key=_order)), tuple(sorted(suppressed, key=_order)))


def _order(finding: Finding) -> tuple[int, str, str]:
    return (finding.line, finding.rule.rule_id, finding.message)


# --- requirements ---------------------------------------------------------------

_REQ_NAME = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*)$")
_SPECIFIER = re.compile(r"^(===|~=|==|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+$")
_UPPER_BOUNDING_OPS = frozenset({"<", "<=", "==", "~=", "==="})


def _normalize(name: str) -> str:
    """PEP 503 project-name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def scan_requirement(path: str, line_no: int, line: str) -> Finding | None:
    """Judge one ``requirements*.txt`` line's ``anthropic`` requirement.

    Returns UNBOUNDED-PIN when the line names ``anthropic`` with no specifier
    that caps it from above (``<``, ``<=``, ``==``, ``~=``, ``===``): a fresh
    install of such a line resolves to the newest release, today a 2.x the code
    was never tested on. Returns ``None`` for capped or exact pins, for other
    packages (``anthropic-foo``), comments, blank lines, and pip options (``-r``,
    ``--hash`` ...). A capped pin that still admits 2.x (``<3``, ``==2.3.0``) is
    treated as a deliberate choice and not flagged.

    Failure modes: an ``anthropic`` line whose specifiers cannot be parsed, or a
    direct reference (``anthropic @ url``), returns a PARSE-ERROR finding rather
    than a guess. Backslash line continuations are not joined; each physical
    line is judged alone.
    """
    text = re.sub(r"(^|\s)#.*$", "", line).strip()
    if not text or text.startswith("-"):
        return None
    match = _REQ_NAME.match(text)
    if match is None or _normalize(match.group(1)) != _PACKAGE:
        return None

    spec = match.group(3).split(";", 1)[0].strip()
    if spec.startswith("@"):
        return Finding(Rule.PARSE_ERROR, path, line_no, f"direct reference not judged: {text!r}")
    specifiers = [part.strip() for part in spec.split(",")] if spec else []
    ops: list[str] = []
    for specifier in specifiers:
        parsed = _SPECIFIER.match(specifier)
        if parsed is None:
            return Finding(
                Rule.PARSE_ERROR, path, line_no, f"unparseable anthropic specifier: {text!r}"
            )
        ops.append(parsed.group(1))

    if _UPPER_BOUNDING_OPS.isdisjoint(ops):
        shown = spec if spec else "(no specifier)"
        return Finding(
            Rule.UNBOUNDED_PIN,
            path,
            line_no,
            f"anthropic {shown} has no upper bound: a fresh install resolves to SDK 2.x "
            f"(API 2026-10-21), which this code was not tested on",
        )
    return None
