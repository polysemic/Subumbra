> Sanitized report published from the VPS public security suite public-security-suite-20260517T060932Z.
> Source file: `/tmp/public-security-suite-20260517T060932Z.onwchc/publish/trivy.md`

# Trivy Report — 2026-05-17

## Scope
Filesystem, dependency, secret, and misconfiguration scan of the clean VPS clone of /opt/subumbra on branch main.

## Command / Profile
```bash
bash scripts/security/trivy/scan.sh
```

## Result
- Exit code: `1`
- Status: `REVIEW REQUIRED`

## Raw Artifacts
- `scripts/security/trivy/reports/trivy-fs-report.json`

## Console Summary
```text
Running trivy filesystem scan...
2026-05-17T06:10:56Z	INFO	[vuln] Vulnerability scanning is enabled
2026-05-17T06:10:56Z	INFO	[misconfig] Misconfiguration scanning is enabled
2026-05-17T06:10:56Z	INFO	[checks-client] Using existing checks from cache	path="/root/.cache/trivy/policy/content"
2026-05-17T06:10:58Z	INFO	[secret] Secret scanning is enabled
2026-05-17T06:10:58Z	INFO	[secret] If your scanning is slow, please try '--scanners vuln,misconfig' to disable secret scanning
2026-05-17T06:10:58Z	INFO	[secret] Please see https://trivy.dev/docs/v0.70/guide/scanner/secret#recommendation for faster secret detection
2026-05-17T06:10:58Z	WARN	[pip] Unable to find python `site-packages` directory. License detection is skipped.	err="unable to find path to Python executable"
2026-05-17T06:10:58Z	INFO	[npm] To collect the license information of packages, "npm install" needs to be performed beforehand	dir="bootstrap/node_modules"
2026-05-17T06:10:58Z	INFO	[npm] To collect the license information of packages, "npm install" needs to be performed beforehand	dir="worker/node_modules"
2026-05-17T06:10:58Z	INFO	Suppressing dependencies for development and testing. To display them, try the '--include-dev-deps' flag.
2026-05-17T06:10:58Z	INFO	Number of language-specific files	num=7
2026-05-17T06:10:58Z	INFO	[npm] Detecting vulnerabilities...
2026-05-17T06:10:58Z	INFO	[pip] Detecting vulnerabilities...
2026-05-17T06:10:58Z	INFO	Detected config files	num=5
2026-05-17T06:10:59Z	INFO	[vuln] Vulnerability scanning is enabled
2026-05-17T06:10:59Z	INFO	[misconfig] Misconfiguration scanning is enabled
2026-05-17T06:10:59Z	INFO	[checks-client] Using existing checks from cache	path="/root/.cache/trivy/policy/content"
2026-05-17T06:11:02Z	INFO	[secret] Secret scanning is enabled
2026-05-17T06:11:02Z	INFO	[secret] If your scanning is slow, please try '--scanners vuln,misconfig' to disable secret scanning
2026-05-17T06:11:02Z	INFO	[secret] Please see https://trivy.dev/docs/v0.70/guide/scanner/secret#recommendation for faster secret detection
2026-05-17T06:11:02Z	INFO	[npm] To collect the license information of packages, "npm install" needs to be performed beforehand	dir="bootstrap/node_modules"
2026-05-17T06:11:02Z	INFO	[npm] To collect the license information of packages, "npm install" needs to be performed beforehand	dir="worker/node_modules"
2026-05-17T06:11:02Z	WARN	[pip] Unable to find python `site-packages` directory. License detection is skipped.	err="unable to find path to Python executable"
2026-05-17T06:11:02Z	INFO	Suppressing dependencies for development and testing. To display them, try the '--include-dev-deps' flag.
2026-05-17T06:11:02Z	INFO	Number of language-specific files	num=7
2026-05-17T06:11:02Z	INFO	[npm] Detecting vulnerabilities...
2026-05-17T06:11:02Z	INFO	[pip] Detecting vulnerabilities...
2026-05-17T06:11:02Z	INFO	Detected config files	num=5

Report Summary

┌─────────────────────────────────┬────────────┬─────────────────┬─────────┬───────────────────┐
│             Target              │    Type    │ Vulnerabilities │ Secrets │ Misconfigurations │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ bootstrap/package-lock.json     │    npm     │        0        │    -    │         -         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ bootstrap/requirements.txt      │    pip     │        0        │    -    │         -         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ subumbra-keys/requirements.txt  │    pip     │        0        │    -    │         -         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ subumbra-probe/requirements.txt │    pip     │        0        │    -    │         -         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ subumbra-proxy/requirements.txt │    pip     │        0        │    -    │         -         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ ui/requirements.txt             │    pip     │        0        │    -    │         -         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ bootstrap/Dockerfile            │ dockerfile │        -        │    -    │         1         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ subumbra-keys/Dockerfile        │ dockerfile │        -        │    -    │         1         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ subumbra-probe/Dockerfile       │ dockerfile │        -        │    -    │         1         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ subumbra-proxy/Dockerfile       │ dockerfile │        -        │    -    │         1         │
├─────────────────────────────────┼────────────┼─────────────────┼─────────┼───────────────────┤
│ ui/Dockerfile                   │ dockerfile │        -        │    -    │         1         │
└─────────────────────────────────┴────────────┴─────────────────┴─────────┴───────────────────┘
Legend:
- '-': Not scanned
- '0': Clean (no security findings detected)


bootstrap/Dockerfile (dockerfile)
=================================
Tests: 26 (SUCCESSES: 25, FAILURES: 1)
Failures: 1 (UNKNOWN: 0, LOW: 1, MEDIUM: 0, HIGH: 0, CRITICAL: 0)

DS-0026 (LOW): Add HEALTHCHECK instruction in your Dockerfile
════════════════════════════════════════
You should add HEALTHCHECK instruction in your docker container images to perform the health check on running containers.

See https://avd.aquasec.com/misconfig/ds-0026
────────────────────────────────────────



subumbra-keys/Dockerfile (dockerfile)
=====================================
Tests: 27 (SUCCESSES: 26, FAILURES: 1)
Failures: 1 (UNKNOWN: 0, LOW: 1, MEDIUM: 0, HIGH: 0, CRITICAL: 0)

DS-0026 (LOW): Add HEALTHCHECK instruction in your Dockerfile
════════════════════════════════════════
You should add HEALTHCHECK instruction in your docker container images to perform the health check on running containers.

See https://avd.aquasec.com/misconfig/ds-0026
────────────────────────────────────────



subumbra-probe/Dockerfile (dockerfile)
======================================
Tests: 27 (SUCCESSES: 26, FAILURES: 1)
Failures: 1 (UNKNOWN: 0, LOW: 1, MEDIUM: 0, HIGH: 0, CRITICAL: 0)

DS-0026 (LOW): Add HEALTHCHECK instruction in your Dockerfile
════════════════════════════════════════
You should add HEALTHCHECK instruction in your docker container images to perform the health check on running containers.

See https://avd.aquasec.com/misconfig/ds-0026
────────────────────────────────────────



subumbra-proxy/Dockerfile (dockerfile)
======================================
Tests: 27 (SUCCESSES: 26, FAILURES: 1)
Failures: 1 (UNKNOWN: 0, LOW: 1, MEDIUM: 0, HIGH: 0, CRITICAL: 0)

DS-0026 (LOW): Add HEALTHCHECK instruction in your Dockerfile
════════════════════════════════════════
You should add HEALTHCHECK instruction in your docker container images to perform the health check on running containers.

See https://avd.aquasec.com/misconfig/ds-0026
────────────────────────────────────────



ui/Dockerfile (dockerfile)
==========================
Tests: 27 (SUCCESSES: 26, FAILURES: 1)
Failures: 1 (UNKNOWN: 0, LOW: 1, MEDIUM: 0, HIGH: 0, CRITICAL: 0)

DS-0026 (LOW): Add HEALTHCHECK instruction in your Dockerfile
════════════════════════════════════════
You should add HEALTHCHECK instruction in your docker container images to perform the health check on running containers.

See https://avd.aquasec.com/misconfig/ds-0026
────────────────────────────────────────



Reports written to ~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo/scripts/security/trivy/reports

```
