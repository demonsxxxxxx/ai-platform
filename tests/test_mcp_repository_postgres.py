from pathlib import Path


def test_fresh_schema_and_repository_have_no_gateway_catalog_persistence_path():
    root = Path(__file__).resolve().parents[1]
    schema = (root / "app" / "schema.sql").read_text(encoding="utf-8").lower()
    repository = (root / "app" / "mcp" / "repository.py").read_text(encoding="utf-8").lower()

    assert "mcp_tool_catalog_entries" not in schema
    assert "mcp_tool_catalog_entries" not in repository
    assert "publish_mcp_tool_catalog" not in repository
    assert "begin_mcp_catalog_sync" not in repository
