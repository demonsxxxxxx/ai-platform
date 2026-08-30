import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_kadr_01_establishes_one_knowledge_authority_and_provider_boundary():
    adr = _read("docs/adr/0013-external-knowledge-authority.md")
    flat = " ".join(adr.split())

    assert "status: accepted" in adr
    assert "`knowledge` is a bounded context" in flat
    assert "RAGFlow is the first provider adapter" in flat
    assert "`mcp` continues to own the generic MCP server/tool catalog" in flat
    assert "`agent_apps` owns the immutable selection" in flat
    assert "`runs.AdmitRun` creates one Unit of Work" in flat
    assert "`conversations` owns assistant-message finalization" in flat
    assert "opaque `secret_ref`" in flat
    assert "Shared secret infrastructure owns encrypted credential bytes" in flat
    assert "Model/Engine credentials and external-Knowledge credentials" in flat
    assert "neither owner may resolve the other's reference" in flat
    assert "Engine adapters receive only normalized evidence" in flat
    assert "never resolve a Knowledge credential reference" in flat
    assert "not a browser field, route parameter, or configurable Knowledge concept" in flat


def test_runtime_and_source_authorities_name_the_knowledge_context():
    runtime = _read("docs/architecture/runtime-authorities.md")
    source = _read("docs/architecture/source-code-architecture.md")
    runtime_flat = " ".join(runtime.split())
    source_flat = " ".join(source.split())

    assert "| External Knowledge | Governed connection revisions" in runtime_flat
    assert "| MCP | Governed MCP server and tool catalog" in runtime_flat
    assert "  knowledge/" in source
    assert "    credentials/" in source
    assert "| `knowledge` | provider connection revisions" in source
    assert "`knowledge.infrastructure.providers.<provider>`" in source.replace("/", ".")
    assert "Shared `platform.credentials` infrastructure owns encrypted credential bytes" in source_flat
    assert "distinct purpose namespaces" in source_flat
    assert "Neither bounded context may resolve the other's credential reference" in source_flat


def test_external_knowledge_readme_points_to_the_accepted_authority_decision():
    readme = _read("docs/product/external-knowledge/README.md")
    flat = " ".join(readme.split())

    assert "[ADR 0013](../../adr/0013-external-knowledge-authority.md)" in readme
    assert "`knowledge` as the single product authority" in flat
    assert "`mcp` continues to own the generic MCP server/tool catalog" in flat
    assert "requires an accepted ADR" not in flat


def test_architecture_policy_activates_knowledge_as_a_bounded_context():
    policy = json.loads(_read("architecture-policy.json"))

    assert "knowledge" in policy["target_packages"]
    assert "knowledge" in policy["bounded_contexts"]
    assert set(policy["target_packages"]) == set(policy["bounded_contexts"]) | {
        "bootstrap",
        "compat",
        "kernel",
        "platform",
    }


def test_context_glossary_defines_stable_knowledge_language():
    context = _read("CONTEXT.md")

    for term in (
        "**Knowledge Connection**",
        "**Knowledge Source**",
        "**Retrieval Profile**",
        "**Run Knowledge Snapshot**",
        "**Knowledge Evidence**",
        "**Knowledge Citation**",
    ):
        assert term in context
