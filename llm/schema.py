"""
Minimal JSON-shape validator.

Deliberately not jsonschema: the schemas here are shallow (a handful of typed
keys, sometimes a list of objects), and a 12kB dependency on a 4GB VPS is a
cost with no return.  If schemas ever get deep, swap this module out -- the
gateway only calls ``validate``.
"""
from __future__ import annotations

_TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class SchemaError(ValueError):
    """Response did not match the route's declared shape."""


def validate(payload, schema: dict, path: str = "$"):
    """Raise SchemaError on the first mismatch, naming the offending path."""
    if not schema:
        return payload

    expected = schema.get("type")
    if expected:
        python_type = _TYPE_MAP.get(expected)
        if python_type is None:
            raise SchemaError(f"{path}: unknown schema type {expected!r}")
        # bool is a subclass of int in Python; a boolean where a number is
        # expected is almost always a model mistake worth surfacing.
        if expected in ("number", "integer") and isinstance(payload, bool):
            raise SchemaError(f"{path}: expected {expected}, got boolean")
        if not isinstance(payload, python_type):
            raise SchemaError(
                f"{path}: expected {expected}, got {type(payload).__name__}"
            )

    if expected == "object" or (expected is None and isinstance(payload, dict)):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in payload:
                raise SchemaError(f"{path}: missing required key {key!r}")
        for key, value in payload.items():
            if key in properties:
                validate(value, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise SchemaError(f"{path}: unexpected key {key!r}")

    if expected == "array" or (expected is None and isinstance(payload, list)):
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(payload):
                validate(item, item_schema, f"{path}[{index}]")
        min_items = schema.get("minItems")
        if min_items is not None and len(payload) < min_items:
            raise SchemaError(f"{path}: expected at least {min_items} items")

    enum = schema.get("enum")
    if enum is not None and payload not in enum:
        raise SchemaError(f"{path}: {payload!r} not in {enum}")

    return payload


#: Grounded extraction schema (spec section 7).  A value without a box cannot
#: be confirmed against a crop, so bbox is required alongside the value.
GROUNDED_EXTRACTION_SCHEMA = {
    "type": "object",
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field", "value", "bbox", "confidence"],
                "properties": {
                    "field": {"type": "string"},
                    "value": {"type": "string"},
                    "page": {"type": "integer"},
                    "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}

TITLE_PARSE_SCHEMA = {
    "type": "object",
    "required": ["works"],
    "properties": {
        "works": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["description"],
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "discipline_hint": {"type": "string"},
                },
            },
        }
    },
}

REQUIREMENT_EXTRACTION_SCHEMA = {
    "type": "object",
    "required": ["requirements"],
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["doc_type", "source_text"],
                "properties": {
                    "doc_type": {"type": "string"},
                    "source_text": {"type": "string"},
                    "is_mandatory": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "classification_text": {"type": "string"},
    },
}
