# Generic REST App Connection Path

Status: experimental / in progress. This guide is not part of the guaranteed
0.0.1 alpha-supported operator path yet.

Any HTTP REST API can be brokered via Subumbra using `protocol: "http_rest"`
and the appropriate `auth.scheme`.

See `docs/provider-catalog.md` for registered providers. For unlisted targets,
define a policy with `source: "import_path"`.
