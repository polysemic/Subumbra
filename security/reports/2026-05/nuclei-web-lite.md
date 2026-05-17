> Sanitized report published from the VPS public security suite public-security-suite-20260517T060932Z.
> Source file: `/tmp/public-security-suite-20260517T060932Z.onwchc/publish/nuclei-web-lite.md`

# Nuclei Web Lite Report — 2026-05-17

## Scope
Low-rate public web scan against the live Worker URL from ~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo/.env.

## Command / Profile
```bash
STAGE_DIR=~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo TARGET_URL=https://subumbra-proxy.polysemic.workers.dev bash scripts/security/run-nuclei-vps.sh web-lite
```

## Result
- Exit code: `0`
- Status: `PASS`

## Sanitized Report Body

# Nuclei Report

- Target: `https://subumbra-proxy.polysemic.workers.dev`
- Findings: `2`

## Severity Summary

- low: 2

## Findings

### weak-cipher-suites

- Name: Weak Cipher Suites Detection
- Severity: low
- Matched At: `subumbra-proxy.polysemic.workers.dev:443`
- Description: A weak cipher is defined as an encryption/decryption algorithm that uses a key of insufficient length. Using an insufficient length for a key in an encryption/decryption algorithm opens up the possibility (or probability) that the encryption scheme could be broken.

### weak-cipher-suites

- Name: Weak Cipher Suites Detection
- Severity: low
- Matched At: `subumbra-proxy.polysemic.workers.dev:443`
- Description: A weak cipher is defined as an encryption/decryption algorithm that uses a key of insufficient length. Using an insufficient length for a key in an encryption/decryption algorithm opens up the possibility (or probability) that the encryption scheme could be broken.


