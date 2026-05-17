> Sanitized report published from the VPS public security suite public-security-suite-20260517T060932Z.
> Source file: `/tmp/public-security-suite-20260517T060932Z.onwchc/publish/semgrep-baseline.md`

# Semgrep Baseline Report — 2026-05-17

## Scope
Semgrep baseline rules against the clean VPS clone of /opt/subumbra on branch main.

## Command / Profile
```bash
STAGE_DIR=~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo bash scripts/security/run-semgrep-vps.sh baseline
```

## Result
- Exit code: `0`
- Status: `PASS`

## Sanitized Report Body

# Semgrep Report

- Target: `~/security-scan-workspaces/public-security-suite-20260517T060932Z/repo`
- Findings: `16`
- Engine errors: `1`

## Severity Summary

- error: 1
- warning: 15

## Engine Errors

- `3`: Syntax error at line /src/scripts/subumbra-expire-adapter.sh:46:
 `')
PY2

mv "$tmp_file" "$env_path"
trap - EXIT

echo "adapter expired: ${adapter_id}"
echo "next: docker compose up -d --force-recreate subumbra-keys"
` was unexpected

## Findings

### dockerfile.security.missing-user-entrypoint.missing-user-entrypoint

- Severity: error
- Location: `/src/bootstrap/Dockerfile:45`
- Message: By not specifying a USER, a program in the container may run as 'root'. This is a security hazard. If an attacker can control a process running as root, they may have control over the container. Ensure that the last USER in a Dockerfile is a USER other than 'root'.
- CWE: ['CWE-269: Improper Privilege Management']
- OWASP: ['A04:2021 - Insecure Design', 'A06:2025 - Insecure Design']

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/bootstrap/subumbra-bootstrap.py:1390`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/bootstrap/subumbra-bootstrap.py:1440`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/bootstrap/subumbra-bootstrap.py:2417`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/bootstrap/subumbra-bootstrap.py:2489`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/bootstrap/subumbra-bootstrap.py:2623`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/bootstrap/subumbra-bootstrap.py:2675`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/bootstrap/subumbra-bootstrap.py:2724`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/bootstrap/subumbra-bootstrap.py:2776`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/scripts/subumbra-verify-deploy:96`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/scripts/subumbra-verify-deploy:110`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected

- Severity: warning
- Location: `/src/scripts/vps-user-provider-smoke.py:110`
- Message: Detected a dynamic value being used with urllib. urllib supports 'file://' schemes, so a dynamic value controlled by a malicious actor may allow them to read arbitrary files. Audit uses of urllib calls to ensure user data cannot control the URLs, or consider using the 'requests' library instead.
- CWE: ['CWE-939: Improper Authorization in Handler for Custom URL Scheme']
- OWASP: A01:2017 - Injection

### python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure

- Severity: warning
- Location: `/src/subumbra-keys/app.py:363`
- Message: Detected a python logger call with a potential hardcoded secret "token_expired adapter=%s expires_at=%s remote=%s" being logged. This may lead to secret credentials being exposed. Make sure that the logger is not logging  sensitive information.
- CWE: ['CWE-532: Insertion of Sensitive Information into Log File']
- OWASP: ['A09:2021 - Security Logging and Monitoring Failures', 'A09:2025 - Security Logging & Alerting Failures']

### python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure

- Severity: warning
- Location: `/src/subumbra-keys/app.py:474`
- Message: Detected a python logger call with a potential hardcoded secret "list_keys: rejected — expired token adapter=%s remote=%s" being logged. This may lead to secret credentials being exposed. Make sure that the logger is not logging  sensitive information.
- CWE: ['CWE-532: Insertion of Sensitive Information into Log File']
- OWASP: ['A09:2021 - Security Logging and Monitoring Failures', 'A09:2025 - Security Logging & Alerting Failures']

### python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure

- Severity: warning
- Location: `/src/subumbra-keys/app.py:476`
- Message: Detected a python logger call with a potential hardcoded secret "list_keys: rejected — bad token remote=%s" being logged. This may lead to secret credentials being exposed. Make sure that the logger is not logging  sensitive information.
- CWE: ['CWE-532: Insertion of Sensitive Information into Log File']
- OWASP: ['A09:2021 - Security Logging and Monitoring Failures', 'A09:2025 - Security Logging & Alerting Failures']

### python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host

- Severity: warning
- Location: `/src/subumbra-keys/app.py:815`
- Message: Running flask app with host 0.0.0.0 could expose the server publicly.
- CWE: ['CWE-668: Exposure of Resource to Wrong Sphere']
- OWASP: ['A01:2021 - Broken Access Control', 'A01:2025 - Broken Access Control']


