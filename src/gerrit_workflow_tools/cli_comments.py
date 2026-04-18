from __future__ import annotations

import argparse
import json
import logging
import sys

from gerrit_workflow_tools.cli_common import (
    HELP_JSON,
    configure_logging,
    cwd_from_env,
    handle_git_error,
)
from gerrit_workflow_tools.gerrit_client import (
    GerritApiError,
    GerritClient,
    resolve_change_ref,
)
from gerrit_workflow_tools.gerrit_comments import (
    build_human_display_payload,
    build_json_payload,
    change_id_for_sha,
    flatten_change_comments,
    format_human,
    local_change_map_from_stack,
    ordered_relation_chain,
    resolve_change_for_gcomments,
    select_commit_for_comments,
)
from gerrit_workflow_tools.gerrit_url import resolve_gerrit_web_base
from gerrit_workflow_tools.git_run import GitError
from gerrit_workflow_tools.stack import get_stack_snapshot

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """CLI entry for ``ger comments``: fetch and display Gerrit review comments for the stack or a change."""
    p = argparse.ArgumentParser(prog="ger comments")
    p.add_argument(
        "rev",
        nargs="?",
        default=None,
        metavar="REV",
        help=("Optional git revision (commit, branch, etc.) or Gerrit change (numeric id, Change-Id I…, or query)."),
    )
    p.add_argument(
        "--whole-chain",
        action="store_true",
        help="Include related changes in dependency order (oldest to newest).",
    )
    p.add_argument(
        "--no-skip-fixups",
        action="store_true",
        help="Do not skip fixup!/squash! commits when resolving Change-Id.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="all_",
        help="Include resolved comments.",
    )
    p.add_argument(
        "--open",
        action="store_true",
        help="Show only strictly unresolved comments.",
    )
    p.add_argument("--json", action="store_true", dest="json_", help=HELP_JSON)
    p.add_argument(
        "--full",
        action="store_true",
        help="Show full comment and commit body text.",
    )
    p.add_argument("--oneline", action="store_true", help="Use one line per comment.")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log resolution steps to stderr.",
    )
    args = p.parse_args(argv)
    configure_logging(args.verbose)
    cwd = cwd_from_env()

    if args.all_ and args.open:
        print("error: --all and --open are mutually exclusive", file=sys.stderr)
        return 1

    try:
        web_base = resolve_gerrit_web_base(cwd)
    except (GitError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1

    logger.debug("gcomments web_base=%s", web_base)

    try:
        client = GerritClient(web_base, cwd=str(cwd))
        stack_snap = get_stack_snapshot(cwd)
        local_map = local_change_map_from_stack(cwd, snapshot=stack_snap)

        if args.rev is not None:
            pos = args.rev.strip()
            if not pos:
                print("error: empty positional REV", file=sys.stderr)
                return 1
            # Numeric / I+40 → Gerrit query; else git rev.
            if resolve_change_ref(pos) != pos:
                first = resolve_change_for_gcomments(client, change_arg=pos, local_change_id=None)
            else:
                sha = select_commit_for_comments(
                    cwd,
                    explicit_rev=pos,
                    skip_fixups=not args.no_skip_fixups,
                    snapshot=stack_snap,
                )
                raw_msg = next((r[3] for r in stack_snap.rows if r[0] == sha), None)
                cid = change_id_for_sha(cwd, sha, raw_message=raw_msg)
                first = resolve_change_for_gcomments(client, change_arg=None, local_change_id=cid)
        else:
            sha = select_commit_for_comments(
                cwd,
                explicit_rev=None,
                skip_fixups=not args.no_skip_fixups,
                snapshot=stack_snap,
            )
            raw_msg = next((r[3] for r in stack_snap.rows if r[0] == sha), None)
            cid = change_id_for_sha(cwd, sha, raw_message=raw_msg)
            first = resolve_change_for_gcomments(client, change_arg=None, local_change_id=cid)

        chain = ordered_relation_chain(client, first) if args.whole_chain else [first]

        strict_open = args.open
        include_all = args.all_

        comments_by_change = []
        for ch in chain:
            chg_id = ch.get("id")
            if not isinstance(chg_id, str) or not chg_id:
                raise GerritApiError("change has no id in API response")
            raw_map = client.get_comments(chg_id)
            flattened = flatten_change_comments(
                web_base,
                ch,
                raw_map,
                include_all=include_all,
                strict_open=strict_open,
            )
            comments_by_change.append(flattened)

        if args.json_:
            payload = build_json_payload(
                chain,
                comments_by_change,
                local_commit_by_change_id=local_map,
            )
            print(json.dumps(payload, indent=2))
            return 0

        human = build_human_display_payload(
            chain,
            comments_by_change,
            local_commit_by_change_id=local_map,
        )
        print(
            format_human(human, full=args.full, oneline=args.oneline),
            end="",
        )
        return 0
    except GitError as e:
        return handle_git_error(e)
    except GerritApiError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
