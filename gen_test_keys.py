import hashlib
from base64 import b64encode
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

def generate_test_keys():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
    )

    # Private key: PKCS#8 DER base64
    private_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_b64 = b64encode(private_der).decode("ascii")

    # Fingerprint: sha256 of SPKI DER
    public_key = private_key.public_key()
    public_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fingerprint = "sha256:" + hashlib.sha256(public_der).hexdigest()

    print(f"WORKER_PRIVATE_KEY={private_b64}")
    print(f"WORKER_KEY_FINGERPRINT={fingerprint}")

if __name__ == "__main__":
    generate_test_keys()
