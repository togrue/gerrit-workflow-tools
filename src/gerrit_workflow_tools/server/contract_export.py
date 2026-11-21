"""The ``ger serve`` contract (:mod:`~gerrit_workflow_tools.server.contract`) for other languages, and checked.

:func:`typescript` and :func:`json_schema` write the generated files a client builds on;
:func:`problems` says where a JSON value strays from a contract type, so tests can hold
every answer an endpoint gives against what the contract promises.
"""

from __future__ import annotations

import ast
import enum
import inspect
import json
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Literal, Union, get_args, get_origin

from typing_extensions import NotRequired, get_type_hints, is_typeddict

from gerrit_workflow_tools.server import contract


GENERATED_BY = "scripts/generate_serve_contract.py"


@dataclass(frozen=True)
class _Field:
    name: str
    hint: Any
    required: bool
    doc: str | None


@dataclass
class _Model:
    """The contract read once: its named types in definition order, with their documentation."""

    aliases: dict[str, Any] = field(default_factory=dict)
    """Named ``Literal`` and union aliases, and enums of core, by name."""
    alias_docs: dict[str, str] = field(default_factory=dict)
    objects: dict[str, type] = field(default_factory=dict)
    """The public TypedDicts, by name."""
    class_docs: dict[str, str] = field(default_factory=dict)
    field_docs: dict[str, dict[str, str]] = field(default_factory=dict)
    bases: dict[str, list[str]] = field(default_factory=dict)

    def fields(self, cls: type) -> list[_Field]:
        hints = get_type_hints(cls, include_extras=True)
        out = []
        for name, hint in hints.items():
            required = get_origin(hint) is not NotRequired
            out.append(
                _Field(name, get_args(hint)[0] if not required else hint, required, self._field_doc(cls.__name__, name))
            )
        return out

    def _field_doc(self, cls_name: str, name: str) -> str | None:
        found = self.field_docs.get(cls_name, {}).get(name)
        if found is not None:
            return found
        for base in self.bases.get(cls_name, []):
            if (doc := self._field_doc(base, name)) is not None:
                return doc
        return None

    def alias_of(self, hint: Any) -> str | None:
        if isinstance(hint, type) and issubclass(hint, enum.Enum):
            return hint.__name__
        for name, alias in self.aliases.items():
            if not isinstance(alias, type) and hint == alias:
                return name
        return None


def _doc(text: str | None) -> str | None:
    """A docstring as one Markdown paragraph block: RST literals become backticks."""
    if not text:
        return None
    return inspect.cleandoc(text).replace("``", "`")


def _is_alias(value: Any) -> bool:
    return get_origin(value) in (Literal, Union, types.UnionType)


def _read() -> _Model:
    model = _Model()
    for module in (contract, *contract.MODULES):
        _read_module(model, module)
    return model


def _trailing_doc(node: ast.stmt | None) -> str | None:
    """The string literal *node* is, as a docstring after a field or an alias; ``None`` for anything else."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return _doc(node.value.value)
    return None


def _read_module(model: _Model, module: ModuleType) -> None:
    body = ast.parse(inspect.getsource(module)).body
    for node, after in zip(body, [*body[1:], None], strict=True):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if _is_alias(getattr(module, name, None)) and (doc := _trailing_doc(after)):
                model.alias_docs[name] = doc
        if not isinstance(node, ast.ClassDef):
            continue
        model.bases[node.name] = [base.id for base in node.bases if isinstance(base, ast.Name)]
        model.class_docs[node.name] = _doc(ast.get_docstring(node)) or ""
        model.field_docs[node.name] = {
            item.target.id: doc
            for item, following in zip(node.body, [*node.body[1:], None], strict=True)
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and (doc := _trailing_doc(following))
        }
    for name, value in vars(module).items():
        if name.startswith("_") or name in model.aliases:
            continue
        if _is_alias(value) or (isinstance(value, type) and issubclass(value, enum.Enum)):
            model.aliases[name] = value
        elif is_typeddict(value) and value.__module__ == module.__name__:
            model.objects[name] = value


def _union_args(hint: Any) -> tuple[Any, ...] | None:
    return get_args(hint) if get_origin(hint) in (Union, types.UnionType) else None


# --- TypeScript ------------------------------------------------------------------------------


def _ts(model: _Model, hint: Any, *, named: bool = True) -> str:
    alias = model.alias_of(hint) if named else None
    if alias is not None:
        return alias
    if hint is type(None):
        return "null"
    if hint is str:
        return "string"
    if hint in (int, float):
        return "number"
    if hint is bool:
        return "boolean"
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return " | ".join(json.dumps(member.value) for member in hint)
    if is_typeddict(hint):
        return hint.__name__
    if (args := _union_args(hint)) is not None:
        return " | ".join(_ts(model, arg) for arg in args)
    origin = get_origin(hint)
    if origin is Literal:
        return " | ".join(json.dumps(value) for value in get_args(hint))
    if origin is list:
        item = _ts(model, get_args(hint)[0])
        return f"({item})[]" if " | " in item else f"{item}[]"
    if origin is dict:
        return f"Record<string, {_ts(model, get_args(hint)[1])}>"
    raise TypeError(f"the contract cannot say {hint!r} in TypeScript")


def _ts_doc(doc: str | None, indent: str = "") -> Iterator[str]:
    if not doc:
        return
    lines = doc.splitlines()
    if len(lines) == 1:
        yield f"{indent}/** {lines[0]} */"
        return
    yield f"{indent}/**"
    for line in lines:
        yield f"{indent} *{' ' + line if line else ''}"
    yield f"{indent} */"


def typescript() -> str:
    """``vscode/src/contract.ts``: every contract type, and ``Endpoints`` from call to query, body and answer."""
    model = _read()
    out = [
        f"// Generated by {GENERATED_BY} from src/gerrit_workflow_tools/server/contract.py. Do not edit.",
        "// The `ger serve` contract: docu/spec/commands/serve.md.",
        "",
    ]
    for name, alias in model.aliases.items():
        doc = model.alias_docs.get(name) or (_doc(alias.__doc__) if isinstance(alias, type) else None)
        out += [*_ts_doc(doc), f"export type {name} = {_ts(model, alias, named=False)};", ""]
    for name, cls in model.objects.items():
        out.extend(_ts_doc(model.class_docs.get(name)))
        fields = model.fields(cls)
        if not fields:
            out += [f"export type {name} = Record<string, never>;", ""]
            continue
        out.append(f"export interface {name} {{")
        for item in fields:
            out.extend(_ts_doc(item.doc, "  "))
            out.append(f"  {item.name}{'' if item.required else '?'}: {_ts(model, item.hint)};")
        out += ["}", ""]
    out.append(
        "/** Every call of the API, by `METHOD /path` under the prefix `Hello.api`; `{name}` is a path segment. */"
    )
    out.append("export interface Endpoints {")
    for endpoint in contract.ENDPOINTS:
        query, body, answer = (_ts(model, t) for t in (endpoint.query, endpoint.body, endpoint.answer))
        out.append(f'  "{endpoint.key}": {{ query: {query}; body: {body}; answer: {answer} }};')
    out += ["}", "", "export type EndpointKey = keyof Endpoints;", ""]
    return "\n".join(out)


# --- JSON Schema -----------------------------------------------------------------------------


def _schema(model: _Model, hint: Any, *, named: bool = True) -> dict[str, Any]:
    alias = model.alias_of(hint) if named else None
    if alias is not None:
        return {"$ref": f"#/$defs/{alias}"}
    simple = {type(None): "null", str: "string", int: "integer", float: "number", bool: "boolean"}
    if hint in simple:
        return {"type": simple[hint]}
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return {"enum": [member.value for member in hint]}
    if is_typeddict(hint):
        return {"$ref": f"#/$defs/{hint.__name__}"}
    if (args := _union_args(hint)) is not None:
        return {"anyOf": [_schema(model, arg) for arg in args]}
    origin = get_origin(hint)
    if origin is Literal:
        return {"enum": list(get_args(hint))}
    if origin is list:
        return {"type": "array", "items": _schema(model, get_args(hint)[0])}
    if origin is dict:
        return {"type": "object", "additionalProperties": _schema(model, get_args(hint)[1])}
    raise TypeError(f"the contract cannot say {hint!r} in JSON Schema")


def _described(schema: dict[str, Any], doc: str | None) -> dict[str, Any]:
    return {"description": doc, **schema} if doc else schema


def json_schema() -> dict[str, Any]:
    """``docu/spec/serve-contract.schema.json``: the contract as JSON Schema, for a client in any language.

    ``$defs`` holds every type; ``endpoints`` names each call's query, body and answer.
    """
    model = _read()
    defs: dict[str, Any] = {}
    for name, alias in model.aliases.items():
        doc = model.alias_docs.get(name) or (_doc(alias.__doc__) if isinstance(alias, type) else None)
        defs[name] = _described(_schema(model, alias, named=False), doc)
    for name, cls in model.objects.items():
        fields = model.fields(cls)
        defs[name] = _described(
            {
                "type": "object",
                "properties": {item.name: _described(_schema(model, item.hint), item.doc) for item in fields},
                "required": [item.name for item in fields if item.required],
                "additionalProperties": False,
            },
            model.class_docs.get(name),
        )
    endpoints = {
        endpoint.key: {
            "method": endpoint.method,
            "path": endpoint.path,
            "query": _schema(model, endpoint.query),
            "body": _schema(model, endpoint.body),
            "answer": _schema(model, endpoint.answer),
        }
        for endpoint in contract.ENDPOINTS
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ger serve API",
        "description": f"Generated by {GENERATED_BY} from src/gerrit_workflow_tools/server/contract.py. Do not edit.",
        "endpoints": endpoints,
        "$defs": defs,
    }


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"


# --- Holding a value against the contract ------------------------------------------------------


def problems(value: Any, hint: Any, where: str = "$") -> list[str]:
    """Where *value* strays from the contract type *hint*: missing, unknown or mistyped keys. Empty when it fits."""
    if hint is type(None):
        return [] if value is None else [f"{where}: {value!r} is not null"]
    if hint is bool:
        return [] if isinstance(value, bool) else [f"{where}: {value!r} is not a boolean"]
    if hint is int:
        return (
            [] if isinstance(value, int) and not isinstance(value, bool) else [f"{where}: {value!r} is not an integer"]
        )
    if hint is float:
        fits = isinstance(value, (int, float)) and not isinstance(value, bool)
        return [] if fits else [f"{where}: {value!r} is not a number"]
    if hint is str:
        return [] if isinstance(value, str) else [f"{where}: {value!r} is not a string"]
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        known = [member.value for member in hint]
        return [] if value in known else [f"{where}: {value!r} is not one of {known}"]
    if is_typeddict(hint):
        return _object_problems(value, hint, where)
    if (args := _union_args(hint)) is not None:
        each = [problems(value, arg, where) for arg in args]
        return (
            []
            if any(not found for found in each)
            else [f"{where}: fits none of {hint}: " + "; ".join(found for some in each for found in some)]
        )
    origin = get_origin(hint)
    if origin is Literal:
        known = list(get_args(hint))
        fits = any(value == item and type(value) is type(item) for item in known)
        return [] if fits else [f"{where}: {value!r} is not one of {known}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{where}: {value!r} is not a list"]
        return [found for i, item in enumerate(value) for found in problems(item, get_args(hint)[0], f"{where}[{i}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{where}: {value!r} is not an object"]
        inner = get_args(hint)[1]
        return [found for key, item in value.items() for found in problems(item, inner, f"{where}.{key}")]
    raise TypeError(f"the contract cannot check {hint!r}")


def _object_problems(value: Any, cls: type, where: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{where}: {value!r} is not a {cls.__name__} object"]
    hints = get_type_hints(cls, include_extras=True)
    found = [f"{where}.{key}: not in {cls.__name__}" for key in value if key not in hints]
    for name, hint in hints.items():
        optional = get_origin(hint) is NotRequired
        if name not in value:
            if not optional:
                found.append(f"{where}.{name}: missing from {cls.__name__}")
            continue
        found += problems(value[name], get_args(hint)[0] if optional else hint, f"{where}.{name}")
    return found
