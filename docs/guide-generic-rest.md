# Generic REST App Connection Path

Status: experimental / in progress. This guide is not part of the guaranteed
0.0.1 alpha-supported operator path yet.

Any HTTP REST API can be brokered via Subumbra using `protocol: "http_rest"`
and the appropriate `auth.scheme`.

Operators declare provider labels, `policy.target.host`, and `policy.auth` in
`subumbra.json`; see `subumbra.example.json` for the reference manifest shape.
For example curl requests against common providers see `docs/provider-catalog.md`.
