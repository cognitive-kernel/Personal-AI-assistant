from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

from app.main import app


_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _schema_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    return str(value)


def _success_responses(operation: dict[str, Any]) -> dict[str, Any]:
    return {
        code: response
        for code, response in operation.get("responses", {}).items()
        if str(code).startswith("2")
    }


def build_api_contract_inventory(application=app) -> dict[str, Any]:
    """Return a deterministic, read-only inventory of every registered /api route."""
    openapi = application.openapi()
    openapi_paths = openapi.get("paths", {})
    route_entries: list[dict[str, Any]] = []
    route_keys: Counter[tuple[str, str]] = Counter()
    operation_ids: defaultdict[str, list[str]] = defaultdict(list)

    for route in application.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        endpoint = route.endpoint
        methods = sorted((route.methods or set()) & _HTTP_METHODS)
        for method in methods:
            key = (method, route.path)
            route_keys[key] += 1
            operation = openapi_paths.get(route.path, {}).get(method.lower())
            if operation:
                operation_id = operation.get("operationId")
                if operation_id:
                    operation_ids[operation_id].append(f"{method} {route.path}")
                parameters = [
                    {
                        "name": item.get("name"),
                        "in": item.get("in"),
                        "required": bool(item.get("required", False)),
                        "schema": item.get("schema", {}),
                    }
                    for item in operation.get("parameters", [])
                ]
                request_body = operation.get("requestBody") or {}
                body_content = request_body.get("content") or {}
                request_schemas = {
                    media_type: value.get("schema", {})
                    for media_type, value in sorted(body_content.items())
                }
                success_responses = _success_responses(operation)
                response_schemas = {}
                for code, response in sorted(success_responses.items()):
                    response_schemas[code] = {
                        media_type: value.get("schema", {})
                        for media_type, value in sorted((response.get("content") or {}).items())
                    }
                summary = operation.get("summary")
                tags = operation.get("tags", [])
            else:
                operation_id = None
                parameters = []
                request_body = {}
                request_schemas = {}
                success_responses = {}
                response_schemas = {}
                summary = None
                tags = []

            route_entries.append(
                {
                    "method": method,
                    "path": route.path,
                    "endpoint": f"{endpoint.__module__}.{endpoint.__qualname__}",
                    "route_name": route.name,
                    "tags": tags,
                    "summary": summary,
                    "include_in_schema": bool(route.include_in_schema),
                    "operation_id": operation_id,
                    "status_code": route.status_code,
                    "response_model": _schema_name(route.response_model),
                    "request_body_required": bool(request_body.get("required", False)),
                    "request_body_schemas": request_schemas,
                    "parameters": parameters,
                    "success_response_schemas": response_schemas,
                    "mutating": method not in {"GET", "HEAD", "OPTIONS"},
                }
            )

    documented_operations = []
    for path, path_item in sorted(openapi_paths.items()):
        if not path.startswith("/api/"):
            continue
        for method, operation in sorted(path_item.items()):
            if method.upper() not in _HTTP_METHODS:
                continue
            documented_operations.append((method.upper(), path, operation.get("operationId")))

    inventory_keys = {(item["method"], item["path"]) for item in route_entries}
    documented_keys = {(method, path) for method, path, _operation_id in documented_operations}
    missing_from_inventory = sorted(
        f"{method} {path}" for method, path in documented_keys - inventory_keys
    )
    not_documented = sorted(
        f"{method} {path}"
        for method, path in inventory_keys - documented_keys
        if any(
            item["method"] == method and item["path"] == path and item["include_in_schema"]
            for item in route_entries
        )
    )
    duplicate_operation_ids = {
        operation_id: sorted(locations)
        for operation_id, locations in sorted(operation_ids.items())
        if len(locations) > 1
    }
    missing_operation_ids = sorted(
        f"{method} {path}"
        for method, path, operation_id in documented_operations
        if not operation_id
    )
    missing_success_responses = sorted(
        f"{method} {path}"
        for method, path, operation_id in documented_operations
        if not _success_responses(openapi_paths[path][method.lower()])
    )

    path_id_gaps = []
    limit_gaps = []
    mutations_without_body = []
    for item in route_entries:
        for parameter in item["parameters"]:
            schema = parameter["schema"]
            if parameter["in"] == "path" and schema.get("type") == "integer":
                if schema.get("minimum") is None or schema.get("minimum", 0) < 1:
                    path_id_gaps.append(f"{item['method']} {item['path']}:{parameter['name']}")
            if parameter["in"] == "query" and parameter["name"] == "limit":
                if "minimum" not in schema or "maximum" not in schema:
                    limit_gaps.append(f"{item['method']} {item['path']}:limit")
        if item["mutating"] and not item["request_body_schemas"]:
            mutations_without_body.append(f"{item['method']} {item['path']}")

    route_entries.sort(key=lambda item: (item["path"], item["method"], item["endpoint"]))
    return {
        "inventory_version": 1,
        "application": {
            "title": openapi.get("info", {}).get("title"),
            "version": openapi.get("info", {}).get("version"),
            "openapi_version": openapi.get("openapi"),
        },
        "summary": {
            "api_operations": len(route_entries),
            "documented_api_operations": len(documented_operations),
            "api_paths": len({item["path"] for item in route_entries}),
            "mutating_operations": sum(item["mutating"] for item in route_entries),
            "routes_not_in_openapi": sum(not item["include_in_schema"] for item in route_entries),
            "duplicate_route_keys": sum(count - 1 for count in route_keys.values() if count > 1),
            "missing_from_inventory": len(missing_from_inventory),
            "included_routes_not_documented": len(not_documented),
            "missing_operation_ids": len(missing_operation_ids),
            "duplicate_operation_ids": len(duplicate_operation_ids),
            "missing_success_responses": len(missing_success_responses),
            "integer_path_parameters_without_positive_minimum": len(path_id_gaps),
            "limit_parameters_without_explicit_bounds": len(limit_gaps),
            "mutating_operations_without_request_body": len(mutations_without_body),
        },
        "findings": {
            "missing_from_inventory": missing_from_inventory,
            "included_routes_not_documented": not_documented,
            "missing_operation_ids": missing_operation_ids,
            "duplicate_operation_ids": duplicate_operation_ids,
            "missing_success_responses": missing_success_responses,
            "integer_path_parameters_without_positive_minimum": sorted(path_id_gaps),
            "limit_parameters_without_explicit_bounds": sorted(limit_gaps),
            "mutating_operations_without_request_body": sorted(mutations_without_body),
        },
        "routes": route_entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a read-only FastAPI API contract inventory.")
    parser.add_argument("--output", type=Path, default=Path("api-contract-inventory.json"))
    args = parser.parse_args()
    inventory = build_api_contract_inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(inventory["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
