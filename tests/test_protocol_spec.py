from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SPEC_PATH = Path(__file__).parents[1] / "spec" / "target-v0.1.openapi.json"
EXPECTED_PATHS = {
    "/.well-known/augmentworks-target.json",
    "/v1/attempts",
    "/v1/attempts/{attempt_id}/turns",
    "/v1/attempts/{attempt_id}/observe",
    "/v1/attempts/{attempt_id}",
}


def load_spec() -> dict[str, Any]:
    value = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_openapi_document_is_valid_json_with_complete_path_surface() -> None:
    spec = load_spec()

    assert spec["openapi"].startswith("3.1.")
    assert set(spec["paths"]) == EXPECTED_PATHS


def test_discovery_is_public_and_mutations_require_all_hmac_headers() -> None:
    spec = load_spec()
    paths = spec["paths"]

    assert paths["/.well-known/augmentworks-target.json"]["get"]["security"] == []
    for path, method in (
        ("/v1/attempts", "post"),
        ("/v1/attempts/{attempt_id}/turns", "post"),
        ("/v1/attempts/{attempt_id}/observe", "post"),
        ("/v1/attempts/{attempt_id}", "delete"),
    ):
        security = paths[path][method]["security"]
        assert security == [
            {
                "AWKeyId": [],
                "AWTimestamp": [],
                "AWNonce": [],
                "AWSignature": [],
            }
        ]


def test_wire_contract_does_not_define_hidden_reasoning() -> None:
    schemas = load_spec()["components"]["schemas"]
    property_names = {
        property_name
        for schema in schemas.values()
        for property_name in schema.get("properties", {})
    }

    assert "chain_of_thought" not in property_names
    assert "reasoning" not in property_names
