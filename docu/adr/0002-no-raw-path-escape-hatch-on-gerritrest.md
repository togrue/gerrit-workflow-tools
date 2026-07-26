# No raw-path escape hatch on `GerritRest`

`GerritRest` exposes only named, semantic operations. It deliberately has no `get_json(path, params)`, because a raw-path method makes the set of things that can cross the seam unbounded: any caller could reach any Gerrit endpoint, and every implementation would have to route arbitrary paths to stay usable.

The Checks plugin was the one caller that needed it. Rather than keep the hatch, `get_checks(change_id)` was promoted onto the seam; it returns Checks rows verbatim, and deciding which states count as a failure stays above the seam in `GerritService._fetch_ci_failures`.

## Consequences

`get_json` still exists on `HttpGerritRest`, and `ger fetch-api` uses it through that concrete type on purpose — GETting an arbitrary path from a real server is the entire point of that debug command, so it takes no injected implementation.

If `get_checks` looks like an arbitrary special case sitting next to a client that can obviously GET anything, that is this decision. Adding `get_json` back to the protocol to "simplify" it would re-open the seam.
