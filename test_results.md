# Subumbra Test Suite Results

## Environment Setup
- **OS**: Linux (Sandbox)
- **Node.js**: v20.x (with npm)
- **Python**: v3.12.x
- **Dependencies**:
    - Python: `cryptography==42.0.8`, `Flask==3.0.3`, `gunicorn==22.0.0`, `httpx==0.27.0`
    - Node.js: `wrangler@4.77.0` (as per `package.json`, though `npx` used `4.81.0`)

## Test Execution
The test suite in `worker/test-local.sh` was executed against a local `wrangler dev` server.

### Command:
```bash
cd worker && bash test-local.sh
```

### Output:
```
── Health check ─────────────────────────────────────────────────────────
  OK — server is up at http://localhost:8787

── Test 1: Missing X-Forge-Token header ─────────────────────────────────
   Expect: HTTP 401 (no token supplied — should be rejected immediately)
  PASS  Missing X-Forge-Token → 401 (HTTP 401)

── Test 2: Wrong X-Forge-Token ──────────────────────────────────────────
   Expect: HTTP 401 (token mismatch — timing-safe comparison must reject)
  PASS  Wrong X-Forge-Token → 401 (HTTP 401)

── Test 3: Valid token, missing ciphertext ───────────────────────────────
   Expect: HTTP 400 (ciphertext field absent — input validation should fire)
  PASS  Valid token, missing ciphertext → 400 (HTTP 400)

── Test 4: Valid token, plausible ciphertext ────────────────────────────
   Expect: HTTP 500 (passes all validation; decryption fails on bad ciphertext)
   OR:     HTTP 503 if WORKER_PRIVATE_KEY is not set in .dev.vars
  PASS  Valid token + ciphertext format → HTTP 500 (reached crypto layer)
        body: {"error":"decryption failed"}

─────────────────────────────────────────────────────────────────────────
  Results: 4 passed, 0 failed
─────────────────────────────────────────────────────────────────────────
```

## Summary of Results
All 4 existing smoke tests for the Cloudflare Worker passed.
- **Authentication**: Verified that missing or incorrect tokens are rejected with 401.
- **Validation**: Verified that missing required fields (ciphertext) result in a 400.
- **Crypto Layer**: Verified that validly formatted requests reach the decryption step (resulting in 500 when provided with dummy ciphertext).

## Identified Bugs and Issues

### 1. Missing Documentation/Directory (`AGENTS.md`)
The `AGENTS.md` file references a `council/` directory and several files within it (`council/COUNCIL.md`, `council/COUNCIL_PROMPT.md`), but this directory does not exist in the repository.
- **Severity**: Low (Documentation/Process inconsistency)
- **Description**: The repository seems to expect a multi-agent review process that is not present in the current file structure.

### 2. Typo in `adapter-probe/probe.py`
In `adapter-probe/probe.py`, the model name for Anthropic is listed as `claude-haiku-4-5-20251001`.
- **Severity**: Low
- **Description**: While this is a probe script, the model version `4-5` does not exist yet (as of early 2025), and `20251001` is a future date. This might cause failures if used for actual production probing against real endpoints.

### 3. `worker/test-local.sh` Port Sensitivity
The test script assumes `wrangler dev` binds to `localhost:8787`. If the port is already in use, `wrangler` might bind to a different port, causing the test script to fail unless the `WORKER_URL` environment variable is manually set.
- **Severity**: Very Low (Environmental)

### 4. No Automated Tests for Python Services
While there are tests for the Cloudflare Worker, there are no existing automated test scripts or suites found for the Python-based services (`forge-keys`, `ui`, `bootstrap`).
- **Severity**: Medium (Missing test coverage)
- **Description**: The `bootstrap` logic is critical for security (key generation and encryption), but lacks an automated test suite to verify the V2 envelope encryption logic independently of the Worker.

## Recommendations
- Create the missing `council/` directory or update `AGENTS.md` to reflect the actual project structure.
- Implement unit tests for `bootstrap/keyvault-bootstrap.py` to verify RSA/AES-GCM encryption round-trips.
- Add a health check or smoke test for the `ui` and `forge-keys` services in the automated test suite.
