# Security Reports

This directory is the public-facing security evidence area for Subumbra.

It is meant to show serious, repeatable testing without publishing raw
attack-material, secrets, or internal-only operational detail.

## Purpose

Subumbra publishes security testing artifacts so operators, reviewers, and
security engineers can see:

- what classes of tools are being run
- what the tools found
- what was fixed
- what was reviewed and accepted as non-blocking or platform-controlled

This folder is intentionally curated. It is **not** a raw evidence dump.

## Layout

```text
security/
├── README.md
├── advisories/
│   └── accepted-risks.md
└── reports/
    ├── YYYY-MM/
    │   └── *.md
    └── latest/
        └── *.md
```

- `reports/YYYY-MM/` keeps dated historical snapshots.
- `reports/latest/` is the easiest place for reviewers to start.
- `advisories/accepted-risks.md` explains reviewed findings that are accepted,
  platform-controlled, or intentionally out of scope for the current release.

## Publishing Rules

Good candidates for public raw-or-sanitized markdown reports:

- Gitleaks
- pip-audit
- Bandit
- Semgrep
- Trivy
- Nuclei
- ZAP baseline

Do **not** publish raw:

- Shannon workspaces
- exploit chains or attack playbooks
- token-bearing logs
- operator-only host details unless they are already intentionally public
- copy-pasteable payload collections that materially help an attacker

For Shannon, publish a **sanitized summary** of findings rather than the raw
workspace.

## Helper Scripts

Publish a sanitized single-file report:

```bash
scripts/security/publish-report-file.sh ~/zap-subumbra/reports/<run>/zap-report.md
scripts/security/publish-report-file.sh ~/nuclei-subumbra/reports/<run>/nuclei-report.md
scripts/security/publish-report-file.sh ~/semgrep-subumbra/reports/<run>/semgrep-report.md
```

Publish a sanitized Shannon summary from a workspace:

```bash
scripts/security/publish-shannon-report.sh ~/shannon-subumbra/reports/<workspace>
```

Install or verify the VPS public scan toolchain:

```bash
/opt/subumbra/scripts/security/install-public-scan-tools-vps.sh
/opt/subumbra/scripts/security/install-public-scan-tools-vps.sh --check
```

Both helpers now write:

- a dated historical copy under `security/reports/YYYY-MM/`
- a mirrored current copy under `security/reports/latest/`

Run the full public scan suite sequentially from your local machine:

```bash
scripts/security/run-public-report-suite-vps.sh
```

This orchestrator SSHes to the VPS, updates `/opt/subumbra`, creates a clean
temporary clone under `~/security-scan-workspaces/`, runs the supported public
scans one by one, copies the publish-ready markdown back locally, and then
publishes each report into both `security/reports/YYYY-MM/` and
`security/reports/latest/`.

If you are already on the VPS, run the repo copy directly instead:

```bash
/opt/subumbra/scripts/security/run-public-report-suite-vps.sh
```

In that case the script detects that `ssh subumbra` is unavailable and falls
back to running locally on the VPS while still using a clean temporary clone
under `~/security-scan-workspaces/`.

## Suggested VPS Report Layout

- Shannon workspaces: `~/shannon-subumbra/reports/...`
- ZAP workspaces: `~/zap-subumbra/reports/...`
- Nuclei workspaces: `~/nuclei-subumbra/reports/...`
- Semgrep workspaces: `~/semgrep-subumbra/reports/...`

## Shannon Publishing Policy

Recommended flow:

1. Run Shannon outside the repo.
2. Review the results manually.
3. Extract the real findings into a sanitized markdown summary.
4. Publish only that sanitized summary into this directory.

Budget-friendly Shannon profiles currently kept in-repo:

- `auth-proxy-lite`
- `auth-worker-lite`
- `authz-worker-lite`

## Reading These Reports

These reports should be read together with:

- [README.md](../README.md)
- [SECURITY.md](../SECURITY.md)
- [accepted-risks.md](advisories/accepted-risks.md)

Scanner findings are not all equal. Some are direct product issues, some are
environmental, some are accepted operator tradeoffs, and some are platform
behaviors outside Subumbra's direct control. We try to annotate that clearly
instead of pretending every scanner line is equally actionable.
