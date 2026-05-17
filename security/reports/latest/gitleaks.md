> Sanitized report published from the VPS public security suite public-security-suite-20260517T060932Z.
> Source file: `/tmp/public-security-suite-20260517T060932Z.onwchc/publish/gitleaks.md`

# Gitleaks Report — 2026-05-17

## Scope
Git history and tracked source in a clean VPS clone of /opt/subumbra on branch main.

## Command / Profile
```bash
bash scripts/security/gitleaks/scan.sh
```

## Result
- Exit code: `0`
- Status: `PASS`

## Raw Artifacts
- `scripts/security/gitleaks/reports/gitleaks-report.json`

## Console Summary
```text
Running gitleaks on ~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo ...

    ○
    │╲
    │ ○
    ○ ░
    ░    gitleaks

[90m6:09AM[0m [32mINF[0m [1mUnknown SCM platform. Use --platform to include links in findings.[0m [36mhost=[0m
[90m6:09AM[0m [32mINF[0m [1m576 commits scanned.[0m
[90m6:09AM[0m [32mINF[0m [1mscanned ~7051807 bytes (7.05 MB) in 3.78s[0m
[90m6:09AM[0m [32mINF[0m [1mno leaks found[0m
PASS — no secrets detected

```
