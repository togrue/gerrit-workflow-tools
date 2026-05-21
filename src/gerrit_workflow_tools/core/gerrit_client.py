"""Compatibility re-export for the Gerrit REST client.

New code should import from :mod:`gerrit_workflow_tools.core.gerrit.rest`.
"""

from gerrit_workflow_tools.core.gerrit.rest import (  # noqa: F401
    GerritApiError,
    GerritClient,
    change_id_for_gerrit_rest_path,
    parallel_map,
    pick_change_from_query_result,
    resolve_change_ref,
    resolve_gerrit_change,
    resolve_gerrit_web_base,
    set_log_gerrit_response_bodies,
)
