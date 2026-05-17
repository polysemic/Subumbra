> Sanitized report published from the VPS public security suite public-security-suite-20260517T060932Z.
> Source file: `/tmp/public-security-suite-20260517T060932Z.onwchc/publish/pip-audit.md`

# pip-audit Report — 2026-05-17

## Scope
Pinned Python dependency sets in the clean VPS clone of /opt/subumbra on branch main.

## Command / Profile
```bash
bash scripts/security/pip-audit/scan.sh
```

## Result
- Exit code: `0`
- Status: `PASS`

## Raw Artifacts
- `scripts/security/pip-audit/reports/*.json`

## Console Summary
```text
Scanning bootstrap/requirements.txt ...
  PASS — bootstrap: no known vulnerabilities
Scanning subumbra-keys/requirements.txt ...
  PASS — subumbra-keys: no known vulnerabilities
Scanning subumbra-proxy/requirements.txt ...
  PASS — subumbra-proxy: no known vulnerabilities
Scanning subumbra-probe/requirements.txt ...
  PASS — subumbra-probe: no known vulnerabilities
Scanning ui/requirements.txt ...
  PASS — ui: no known vulnerabilities

PASS — all components clean
Reports written to ~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo/scripts/security/pip-audit/reports

```
