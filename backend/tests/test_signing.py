import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.nobitex.client import sign


def test_signature_verifies_and_is_urlsafe():
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes_raw()
    secret = base64.urlsafe_b64encode(raw).decode()
    sig = sign(secret, "1700000000", "post", "/market/orders/add", '{"a":"1"}')
    key.public_key().verify(base64.urlsafe_b64decode(sig), b'1700000000POST/market/orders/add{"a":"1"}')
    assert "+" not in sig and "/" not in sig


def test_signature_changes_with_body():
    secret = base64.urlsafe_b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode()
    assert sign(secret, "1", "POST", "/x", "{}") != sign(secret, "1", "POST", "/x", '{"a":1}')
