"""One parameter contract for the UI, the planner and the executor.

The declaration names the parameters a capability exposes; the original
Pydantic request model supplies their *semantics* (type, requiredness, enum,
length/range constraints and default).  Explicit adapter contracts are kept
only for executor-only parameters that have no request-body counterpart.

Nothing here re-implements a business rule: the original executor still owns
every additional gate and range check.  This module makes the *shape* of the
parameter identical everywhere, so a new optional schema field reaches the
executor without a second hand-kept if/elif branch.
"""
from __future__ import annotations

import importlib
import inspect
import types
from collections.abc import Callable
from dataclasses import replace
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from shine.copilot.contracts import CapabilityDeclaration, ParamSpec, digest


class ParamError(ValueError):
    """The value does not satisfy the declared/original parameter contract."""


_BODY_BINDINGS = frozenset({"body"})
_JSON_TYPES = {
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
    dict: "object",
    list: "list",
    None: "null",
}


def _import_schema(spec: str | None) -> type[BaseModel] | None:
    if not spec:
        return None
    module_name, _, attr = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
        model = getattr(module, attr)
    except (ImportError, AttributeError):
        return None
    return model if isinstance(model, type) and issubclass(model, BaseModel) else None


def _field_for(row: CapabilityDeclaration, name: str):
    model = _import_schema(row.request_schema)
    if model is None:
        return None
    return (getattr(model, "model_fields", {}) or {}).get(name)


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _type_name(annotation: Any) -> str:
    annotation, _ = _unwrap_optional(annotation)
    if annotation is str:
        return "string"
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is dict:
        return "object"
    origin = get_origin(annotation)
    if origin is Literal:
        return "enum"
    if origin is list:
        item = get_args(annotation)[0] if get_args(annotation) else str
        return "string_list" if item is str or get_origin(item) is Literal else "list"
    if origin is dict:
        return "object"
    return _JSON_TYPES.get(annotation, "string")


def _enum_values(annotation: Any) -> list[Any]:
    annotation, _ = _unwrap_optional(annotation)
    origin = get_origin(annotation)
    if origin is Literal:
        return list(get_args(annotation))
    if origin is list:
        item = get_args(annotation)[0] if get_args(annotation) else None
        return _enum_values(item) if item is not None else []
    if origin in (Union, types.UnionType):
        values: list[Any] = []
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            values.extend(_enum_values(arg))
        return values
    return []


def _metadata_contract(field) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for meta in getattr(field, "metadata", ()) or ():
        for attr, key in (
            ("min_length", "min_length"),
            ("max_length", "max_length"),
            ("ge", "minimum"),
            ("le", "maximum"),
            ("gt", "exclusive_minimum"),
            ("lt", "exclusive_maximum"),
            ("pattern", "pattern"),
            ("multiple_of", "multiple_of"),
        ):
            value = getattr(meta, attr, None)
            if value is not None and key not in out:
                out[key] = value if isinstance(value, int | float | str) else str(value)
    return out


def _json_default(field) -> Any:
    try:
        from pydantic_core import PydanticUndefined

        if field.default is PydanticUndefined:
            if getattr(field, "default_factory", None) is None:
                return None
            return field.get_default(call_default_factory=True)
        return field.get_default(call_default_factory=True)
    except Exception:  # noqa: BLE001 - an unreadable default is simply not exposed
        return None


def _adapter_for(field) -> TypeAdapter:
    annotation = getattr(field, "annotation", Any)
    metadata = tuple(getattr(field, "metadata", ()) or ())
    return TypeAdapter(
        Annotated[annotation, *metadata] if metadata else annotation,
        config=ConfigDict(strict=True),
    )


def _validate_overrides(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 12 or not all(
        isinstance(item_key, str)
        and isinstance(item_value, str)
        and len(item_key) <= 64
        and len(item_value) <= 200
        for item_key, item_value in value.items()
    ):
        raise ParamError("parameter_overrides 必须是最多 12 项的字符串键值表。")
    return dict(value)


def _validate_strategy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ParamError("strategy 必须是策略合同对象。")
    from shine.strategy import GenerationStrategy

    try:
        return GenerationStrategy.model_validate(value).model_dump(mode="json")
    except ValueError as exc:
        raise ParamError(f"strategy 不符合既有策略合同：{str(exc)[:200]}") from exc


#: Explicit adapters are only for parameters that the original request body
#: does not carry.  They never duplicate a schema rule.
_EXPLICIT_VALIDATORS: dict[tuple[str | None, str], Callable[[Any], Any]] = {
    ("workflow_user_copy", "parameter_overrides"): _validate_overrides,
    (None, "strategy"): _validate_strategy,
}


def _generic_value(spec: ParamSpec, value: Any) -> Any:
    if value is None:
        return None
    if spec.type == "boolean":
        if not isinstance(value, bool):
            raise ParamError(f"{spec.name} 必须是布尔值。")
        return value
    if spec.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ParamError(f"{spec.name} 必须是整数。")
        if value < 1:
            raise ParamError(f"{spec.name} 必须是正整数。")
        if spec.max_length is not None and value > spec.max_length:
            raise ParamError(f"{spec.name} 不能超过 {spec.max_length}。")
        return value
    if spec.type == "string_list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ParamError(f"{spec.name} 必须是字符串列表。")
        if spec.max_length is not None and len(value) > spec.max_length:
            raise ParamError(f"{spec.name} 最多 {spec.max_length} 项。")
        if spec.required and not value:
            raise ParamError(f"{spec.name} 不能为空。")
        if spec.enum:
            unknown = [item for item in value if item not in spec.enum]
            if unknown:
                raise ParamError(f"{spec.name} 含未知项：{', '.join(unknown)}")
        return list(dict.fromkeys(value))
    if spec.type == "object" or spec.type == "list":
        if not isinstance(value, dict | list) or not value:
            raise ParamError(f"{spec.name} 必须是对象。")
        return value
    if not isinstance(value, str):
        raise ParamError(f"{spec.name} 必须是字符串。")
    if "\x00" in value:
        raise ParamError(f"{spec.name} 含非法字符。")
    if spec.max_length is not None and len(value) > spec.max_length:
        raise ParamError(f"{spec.name} 不能超过 {spec.max_length} 个字符。")
    if spec.enum and value not in spec.enum:
        raise ParamError(f"{spec.name} 必须是 {', '.join(str(item) for item in spec.enum)} 之一。")
    return value.strip() if spec.name in {"title", "language", "filename", "name"} else value


def _coerce_enum(field, value: Any) -> Any:
    """Let a JSON string reach a ``StrEnum`` request field.

    The request models declare ``GenerationMode`` and friends, and the HTTP
    layer receives them as strings.  Strict mode is right for the *shape*
    (a list is not a string) but an enum member is legitimately identified by
    its value, so the scalar is converted here instead of loosening the whole
    adapter.  An unknown value stays a named parameter error.
    """
    annotation, _optional = _unwrap_optional(getattr(field, "annotation", Any))
    if isinstance(value, str) and isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except ValueError as exc:
            allowed = ", ".join(str(member.value) for member in annotation)
            raise ParamError(f"必须是 {allowed} 之一。") from exc
    return value


def _value_for(row: CapabilityDeclaration, spec: ParamSpec, value: Any) -> Any:
    adapter = _EXPLICIT_VALIDATORS.get((row.action_id, spec.name)) or _EXPLICIT_VALIDATORS.get(
        (None, spec.name)
    )
    if adapter is not None:
        return adapter(value)
    if spec.binding not in _BODY_BINDINGS:
        return _generic_value(spec, value)
    field = _field_for(row, spec.name)
    if field is None:
        return _generic_value(spec, value)
    value = _coerce_enum(field, value)
    try:
        validated = _adapter_for(field).validate_python(value)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ParamError(f"{spec.name}: {first.get('msg') or '不符合原请求模型'}") from exc
    dumped = _adapter_for(field).dump_python(validated, mode="json")
    if spec.enum and dumped not in spec.enum:
        raise ParamError(f"{spec.name} 必须是 {', '.join(str(item) for item in spec.enum)} 之一。")
    return dumped


def _default_for(row: CapabilityDeclaration, spec: ParamSpec) -> tuple[Any, bool]:
    """Schema default when the parameter is a request-body field."""
    if spec.binding in _BODY_BINDINGS:
        field = _field_for(row, spec.name)
        if field is not None and not field.is_required():
            adapter = _adapter_for(field)
            default = _json_default(field)
            try:
                return adapter.dump_python(adapter.validate_python(default), mode="json"), True
            except ValidationError:
                return default, True
            except Exception:  # noqa: BLE001
                return default, True
    return None, False


def _is_required(row: CapabilityDeclaration, spec: ParamSpec) -> bool:
    if spec.derive:
        return spec.required
    if spec.required:
        return True
    if spec.binding in _BODY_BINDINGS:
        field = _field_for(row, spec.name)
        if field is not None:
            return bool(field.is_required())
    return False


def validate_params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate one action's parameters against the one resolved contract.

    Unknown fields are rejected, body fields use their original Pydantic model
    (strict types and the model's own constraints), and executor-only fields use
    the declaration's explicit contract.  Defaults come from the schema.
    """
    from shine.copilot import declarations as decl

    row = decl.write_action(kind)
    if row is None:
        raise ParamError(f"未知动作：{kind}")
    if not isinstance(params, dict):
        raise ParamError("动作参数必须是对象。")
    declared = {spec.name: spec for spec in row.params}
    extra = set(params) - set(declared)
    if extra:
        raise ParamError(f"动作不接受的参数：{', '.join(sorted(extra))}")

    clean: dict[str, Any] = {}
    for spec in row.params:
        required = _is_required(row, spec)
        if spec.name in params:
            value = params[spec.name]
            if value is None:
                if required:
                    raise ParamError(f"{spec.name} 不能为空。")
                clean[spec.name] = None
                continue
            clean[spec.name] = _value_for(row, spec, value)
            continue
        default, has_default = _default_for(row, spec)
        if has_default and not required:
            clean[spec.name] = default
            continue
        if required and not (spec.name == "project_id" and spec.binding == "path"):
            raise ParamError(f"缺少必填参数：{spec.name}")
    return clean


def field_contract(row: CapabilityDeclaration, spec: ParamSpec) -> dict[str, Any]:
    """The resolved contract row used by the artefact, the UI and CI."""
    contract = dict(spec.contract())
    contract["source"] = "declaration"
    if spec.binding in _BODY_BINDINGS:
        field = _field_for(row, spec.name)
        if field is not None:
            annotation = getattr(field, "annotation", Any)
            enum = _enum_values(annotation)
            metadata = _metadata_contract(field)
            contract.update(
                {
                    "source": row.request_schema,
                    "type": _type_name(annotation),
                    "required": bool(
                        spec.required or (field.is_required() and not spec.derive)
                    ),
                    "max_length": metadata.get("max_length", spec.max_length),
                    "enum": enum or list(spec.enum),
                }
            )
            contract.update({key: value for key, value in metadata.items() if key != "max_length"})
            default, has_default = _default_for(row, spec)
            contract["default"] = default if has_default else None
            contract["has_default"] = has_default
            contract["nullable"] = _unwrap_optional(annotation)[1]
    if not contract.get("enum"):
        contract["enum"] = []
    return contract


def resolved_params(row: CapabilityDeclaration) -> list[dict[str, Any]]:
    return [field_contract(row, spec) for spec in row.params]


def resolved_contract(row: CapabilityDeclaration) -> dict[str, Any]:
    """Declaration contract with parameters resolved from the real schema."""
    contract = row.contract()
    contract["params"] = resolved_params(row)
    return contract


def resolved_contract_digest(row: CapabilityDeclaration) -> str:
    return digest(resolved_contract(row))


def fillable_contract() -> dict[str, dict[str, Any]]:
    """Body-bound text fields the NLU may fill, derived from declarations."""
    from shine.copilot import declarations as decl

    out: dict[str, dict[str, Any]] = {}
    for row in decl.WRITE_ACTIONS:
        fields = {
            spec.name: field_contract(row, spec)
            for spec in row.params
            if spec.binding in _BODY_BINDINGS
            and spec.type == "string"
            and not spec.name.endswith("_id")
        }
        if fields:
            out[row.action_id] = fields
    return out


def routed_kwargs(
    row: CapabilityDeclaration,
    params: dict[str, Any],
    executor: Callable[..., Any],
    *,
    handled: set[str] | None = None,
) -> dict[str, Any]:
    """Forward declared body parameters to the original executor by name.

    A parameter that the executor cannot receive is refused rather than
    silently dropped; that keeps a newly declared legal parameter honest end to
    end.  ``handled`` names parameters an explicit adapter consumes under a
    different signature, so they are not silently lost either.
    """
    signature = inspect.signature(executor)
    accepts_extra = any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )
    body_names = {spec.name for spec in row.params if spec.binding in _BODY_BINDINGS}
    consumed = set(handled or ())
    kwargs: dict[str, Any] = {}
    for name in sorted(body_names & set(params)):
        if name in consumed:
            continue
        if name in signature.parameters or accepts_extra:
            kwargs[name] = params[name]
            continue
        raise ParamError(f"{name} 无法传给原执行器 {executor.__module__}.{executor.__name__}")
    return kwargs


def model_payload(row: CapabilityDeclaration, params: dict[str, Any]) -> dict[str, Any]:
    """The request-body projection of the accepted parameters."""
    body_names = {spec.name for spec in row.params if spec.binding in _BODY_BINDINGS}
    return {name: params[name] for name in sorted(body_names & set(params))}


def diff_param_contract(
    previous: list[dict[str, Any]] | None,
    current: list[dict[str, Any]] | None,
) -> list[tuple[str, str]]:
    """Classify parameter-contract drift for the runtime compatibility gate."""
    old = {row.get("name"): row for row in previous or []}
    new = {row.get("name"): row for row in current or []}
    changes: list[tuple[str, str]] = []
    for name in sorted(set(old) | set(new)):
        before, after = old.get(name), new.get(name)
        if before is None:
            changes.append(
                (f"parameter added: {name}", "BREAKING" if after and after.get("required") else "COMPATIBLE")
            )
            continue
        if after is None:
            changes.append((f"parameter removed: {name}", "BREAKING"))
            continue
        if before.get("type") != after.get("type"):
            changes.append((f"parameter type changed: {name}", "BREAKING"))
        if not before.get("required") and after.get("required"):
            changes.append((f"optional became required: {name}", "BREAKING"))
        old_enum = before.get("enum") or []
        new_enum = after.get("enum") or []
        if old_enum != new_enum:
            narrowed = (
                (not old_enum and bool(new_enum))
                or (bool(old_enum) and not new_enum)
                or (bool(old_enum) and any(value not in new_enum for value in old_enum))
            )
            changes.append(
                (f"parameter enum changed: {name}", "BREAKING" if narrowed else "COMPATIBLE")
            )
        if before.get("default", "<none>") != after.get("default", "<none>"):
            changes.append((f"parameter default changed: {name}", "BREAKING"))
        for key, mode in (("max_length", "shrink"), ("maximum", "shrink"), ("min_length", "grow"), ("minimum", "grow")):
            old_value, new_value = before.get(key), after.get(key)
            if old_value is None or new_value is None or old_value == new_value:
                continue
            stricter = new_value < old_value if mode == "shrink" else new_value > old_value
            changes.append((f"parameter {key} changed: {name}", "BREAKING" if stricter else "COMPATIBLE"))
        if before.get("nullable") and not after.get("nullable"):
            changes.append((f"parameter nullable removed: {name}", "BREAKING"))
    return changes


def validate_param_value(kind: str, name: str, value: Any) -> Any:
    """Validate one value with the same contract the preview/executor uses."""
    row = declaration_by_action(kind)
    if row is None:
        raise ParamError(f"未知动作：{kind}")
    spec = next((item for item in row.params if item.name == name), None)
    if spec is None:
        raise ParamError(f"{kind} 不接受参数 {name}")
    return _value_for(row, spec, value)


def declaration_by_action(kind: str) -> CapabilityDeclaration | None:
    from shine.copilot import declarations as decl

    return decl.write_action(kind)


def rebind(row: CapabilityDeclaration, **changes: Any) -> CapabilityDeclaration:
    """A test/developer helper that keeps the declaration immutable."""
    return replace(row, **changes)


__all__ = [
    "ParamError",
    "declaration_by_action",
    "diff_param_contract",
    "field_contract",
    "fillable_contract",
    "model_payload",
    "rebind",
    "resolved_contract",
    "resolved_contract_digest",
    "resolved_params",
    "routed_kwargs",
    "validate_param_value",
    "validate_params",
]
