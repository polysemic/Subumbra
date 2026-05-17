> Sanitized report published from the VPS public security suite public-security-suite-20260517T060932Z.
> Source file: `/tmp/public-security-suite-20260517T060932Z.onwchc/publish/bandit.md`

# Bandit Report — 2026-05-17

## Scope
Python components in the clean VPS clone of /opt/subumbra on branch main.

## Command / Profile
```bash
bash scripts/security/bandit/scan.sh
```

## Result
- Exit code: `0`
- Status: `PASS`

## Raw Artifacts
- `scripts/security/bandit/reports/bandit-report.json and bandit-report.html`

## Console Summary
```text
Running bandit on Python source files...
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[json]	INFO	JSON output written to file: ~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo/scripts/security/bandit/reports/bandit-report.json
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.12.3
[html]	INFO	HTML output written to file: ~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo/scripts/security/bandit/reports/bandit-report.html
Bandit results — HIGH: 0  MEDIUM: 10  LOW: 14
PASS — no HIGH severity findings

Reports written to ~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo/scripts/security/bandit/reports

```
