# Historical outputs

This directory contains generated outputs from local development and regression runs. Treat these files as historical artifacts, not as the authoritative contract for the skill.

Authoritative contracts live in:

- `SKILL.md`
- `references/`
- `profiles/<profile-id>/profile.json`
- profile-specific contract files such as `profiles/ctd-32s73-stability/fact-packet-contract.md`

If an output file contains legacy provenance, stale state names, or failed validation reports, use it only as a negative or historical fixture unless a current eval explicitly says otherwise. Current downstream-consumable fact shards and fact packets must use:

- `agent_provenance.execution_mode: "subagent"`
- `agent_provenance.extraction_agent: "reference_fact_extraction_agent"`
- `agent_provenance.validation_agent: "reference_fact_validation_agent"`
