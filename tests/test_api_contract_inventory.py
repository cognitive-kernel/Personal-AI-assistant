from scripts.export_api_contract_inventory import build_api_contract_inventory


def test_api_contract_inventory_covers_every_documented_api_operation():
    inventory = build_api_contract_inventory()
    summary = inventory["summary"]

    assert summary["api_operations"] > 0
    assert summary["documented_api_operations"] > 0
    assert summary["duplicate_route_keys"] == 0
    assert summary["missing_from_inventory"] == 0, {\n        "missing": inventory["findings"]["missing_from_inventory"][:20],\n        "registered": [(route["method"], route["path"]) for route in inventory["routes"][:20]],\n        "summary": summary,\n    }
    assert summary["included_routes_not_documented"] == 0
    assert summary["missing_operation_ids"] == 0
    assert summary["duplicate_operation_ids"] == 0
    assert summary["missing_success_responses"] == 0

    route_keys = [(route["method"], route["path"]) for route in inventory["routes"]]
    assert len(route_keys) == len(set(route_keys))
    assert all(route["endpoint"].startswith("app.") for route in inventory["routes"])


def test_api_contract_inventory_is_deterministic_and_reports_review_queues():
    first = build_api_contract_inventory()
    second = build_api_contract_inventory()

    assert first == second
    assert "integer_path_parameters_without_positive_minimum" in first["findings"]
    assert "limit_parameters_without_explicit_bounds" in first["findings"]
    assert "mutating_operations_without_request_body" in first["findings"]
