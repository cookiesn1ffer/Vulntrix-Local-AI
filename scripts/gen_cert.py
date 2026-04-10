"""
scripts/gen_cert.py — Generate a self-signed TLS certificate for Vulntrix.

Creates:
  certs/server.key   — RSA-2048 private key
  certs/server.crt   — Self-signed X.509 cert (valid 1 year, localhost + 127.0.0.1)

Usage:
  python scripts/gen_cert.py

Then restart web_server.py — it auto-detects certs/ and switches to HTTPS on port 8443.

Browser trust:
  The certificate is self-signed, so the browser will show a security warning.
  Click "Advanced → Proceed anyway" once per session to dismiss it.
  For a trusted cert, replace server.key/server.crt with one from Let's Encrypt or
  use `mkcert` (https://github.com/FiloSottile/mkcert) which installs a trusted CA.

Requires: pip install cryptography
"""

from __future__ import annotations

import datetime
import ipaddress
import sys
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("ERROR: 'cryptography' package not found.")
    print("Run:  pip install cryptography")
    sys.exit(1)

CERT_DIR = Path(__file__).resolve().parent.parent / "certs"
KEY_FILE  = CERT_DIR / "server.key"
CERT_FILE = CERT_DIR / "server.crt"


def generate(days: int = 365) -> None:
    CERT_DIR.mkdir(exist_ok=True)

    print("Generating RSA-2048 private key…")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME,            "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,       "Vulntrix"),
        x509.NameAttribute(NameOID.COMMON_NAME,             "localhost"),
    ])

    now = datetime.datetime.utcnow()

    print(f"Generating self-signed certificate (valid {days} days)…")
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv6Address("::1")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    KEY_FILE.chmod(0o600)   # owner read-only

    CERT_FILE.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )

    print(f"\n✓ Certificate written to:  {CERT_FILE}")
    print(f"✓ Private key written to:  {KEY_FILE}")
    print(f"\nExpires: {now + datetime.timedelta(days=days):%Y-%m-%d}")
    print("\nRestart web_server.py — it will auto-detect certs/ and use HTTPS on port 8443.")
    print("Browser will show an untrusted-cert warning once — click 'Proceed anyway'.")


if __name__ == "__main__":
    generate()
