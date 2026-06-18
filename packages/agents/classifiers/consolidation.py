"""
Backward-compat re-export shim.

The consolidation agent (check(), ConsolidationResult, RelatedLink,
write_incident_links) moved to consolidation/check.py (INGESTION_DESIGN.md
§10b step 2) so it can be shared between backfill_agent.py and the future
ingestion/orchestrator.py. pipeline.py continues to import from here.
"""

from consolidation.check import (
    ConsolidationResult,
    RelatedLink,
    check,
    write_incident_links,
)

__all__ = ["ConsolidationResult", "RelatedLink", "check", "write_incident_links"]
