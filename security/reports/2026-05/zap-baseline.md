> Sanitized report published from the VPS public security suite public-security-suite-20260517T060932Z.
> Source file: `/tmp/public-security-suite-20260517T060932Z.onwchc/publish/zap-baseline.md`

# ZAP Baseline Report — 2026-05-17

## Scope
Baseline passive web scan against the live Worker URL from ~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo/.env.

## Command / Profile
```bash
STAGE_DIR=~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo TARGET_URL=https://subumbra-proxy.polysemic.workers.dev bash scripts/security/run-zap-vps.sh baseline
```

## Result
- Exit code: `0`
- Status: `PASS`

## Raw Artifacts
- `~/security-scan-workspaces/public-security-suite-20260517T060932Z/zap-baseline/zap-report.md`

## Console Summary
```text
Running ZAP baseline scan
Target URL: https://subumbra-proxy.polysemic.workers.dev
Output dir: ~/security-scan-workspaces/public-security-suite-20260517T060932Z/zap-baseline

Finished. Reports are under:
  ~/security-scan-workspaces/public-security-suite-20260517T060932Z/zap-baseline
Diagnostic log:
  ~/security-scan-workspaces/public-security-suite-20260517T060932Z/zap-baseline/zap-run.log
Publish sanitized markdown with:
  scripts/security/publish-report-file.sh ~/security-scan-workspaces/public-security-suite-20260517T060932Z/zap-baseline/zap-report.md

```
