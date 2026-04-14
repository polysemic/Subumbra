
import hmac
from functools import wraps
from unittest.mock import MagicMock

# Mocking Flask-like environment
class Response:
    def __init__(self, body, status, headers):
        self.body = body
        self.status = status
        self.headers = headers

def _require_auth(view, username, password, auth_header=None):
    def wrapped(*args, **kwargs):
        if not username:
            return view(*args, **kwargs)

        if not auth_header:
            return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="KeyVault"'})

        # Basic Auth parsing simulation
        import base64
        try:
            kind, data = auth_header.split(None, 1)
            if kind.lower() != 'basic': raise ValueError()
            decoded = base64.b64decode(data).decode()
            u, p = decoded.split(':', 1)
        except:
            return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="KeyVault"'})

        user_ok = hmac.compare_digest(u or "", username)
        pass_ok = hmac.compare_digest(p or "", password)
        if not (user_ok and pass_ok):
            return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="KeyVault"'})

        return view(*args, **kwargs)

    return wrapped

def test_view():
    return "OK"

# Scenario 1: No username configured
print(f"Scenario 1 (No Config): {_require_auth(test_view, '', '', None)()}")

# Scenario 2: Configured, no auth header
print(f"Scenario 2 (Configured, No Header): {_require_auth(test_view, 'admin', 'pass', None)().status}")

# Scenario 3: Configured, wrong password
import base64
wrong_auth = "Basic " + base64.b64encode(b"admin:wrong").decode()
print(f"Scenario 3 (Wrong Pass): {_require_auth(test_view, 'admin', 'pass', wrong_auth)().status}")

# Scenario 4: Configured, correct auth
correct_auth = "Basic " + base64.b64encode(b"admin:pass").decode()
print(f"Scenario 4 (Correct): {_require_auth(test_view, 'admin', 'pass', correct_auth)}")
