# Security Reports

This folder is for sanitized, publishable security summaries that are safe to
keep in the repository and share on GitHub.

Guidelines:

- Keep raw scanner workspaces, agent transcripts, and exploit logs out of the repo.
- Prefer short markdown summaries over bulk JSON or HTML dumps.
- Redact tokens, secrets, internal-only hostnames, and copy-pasteable exploit payloads.
- Treat this folder as public-facing documentation, not as a raw evidence archive.

Recommended flow for Shannon:

1. Run Shannon outside the repo, for example under `~/shannon-subumbra/reports/...`
2. Review the generated report manually
3. Publish a sanitized markdown copy into `security/reports/`

The helper script below automates step 3:

```bash
scripts/security/publish-shannon-report.sh ~/shannon-subumbra/reports/<workspace>
```

Additional off-repo report publishers:

```bash
scripts/security/publish-report-file.sh ~/zap-subumbra/reports/<run>/zap-report.md
scripts/security/publish-report-file.sh ~/nuclei-subumbra/reports/<run>/nuclei-report.md
scripts/security/publish-report-file.sh ~/semgrep-subumbra/reports/<run>/semgrep-report.md
```

Suggested tool layout on the VPS:

- Shannon workspaces: `~/shannon-subumbra/reports/...`
- ZAP workspaces: `~/zap-subumbra/reports/...`
- Nuclei workspaces: `~/nuclei-subumbra/reports/...`
- Semgrep workspaces: `~/semgrep-subumbra/reports/...`

Budget-friendly Shannon profiles now available:

- `auth-proxy-lite`
- `auth-worker-lite`
- `authz-worker-lite`
