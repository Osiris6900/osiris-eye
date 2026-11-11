"""Disposable TLS API fixture; never uses production credentials."""
import base64
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import uvicorn

def main():
    root = Path(os.environ['OSIRIS_BROWSER_FIXTURE_DIR'])
    root.mkdir(mode=0o700, exist_ok=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(private, hashes.SHA256()))
    key_file, ca_file = root / 'key.pem', root / 'ca.pem'
    if key_file.exists() and ca_file.exists():
        private = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        certificate = x509.load_pem_x509_certificate(ca_file.read_bytes())
    key_file.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_file.chmod(0o600)
    ca_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    tokens = {role: secrets.token_urlsafe(24) for role in ('analyst', 'operator', 'security_admin')}
    os.environ.update(OSIRIS_CONTROL_DB=str(root / 'control.sqlite3'), OSIRIS_DB_PATH=str(root / 'research.sqlite3'),
        OSIRIS_CONTROL_MASTER_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), OSIRIS_ADMIN_TOKENS=json.dumps(tokens),
        OSIRIS_ENV='development', OSIRIS_API_TOKEN='fixture-api-only', OSIRIS_PUBLIC_COINBASE_ENABLED='false',
        OSIRIS_PUBLIC_BINANCE_ENABLED='false', OSIRIS_PUBLIC_STOOQ_ENABLED='false')
    fixture = root / 'fixture.json'
    fixture.write_text(json.dumps({'tokens': tokens, 'ca': str(ca_file), 'database': str(root / 'worker.sqlite3')}))
    fixture.chmod(0o600)
    uvicorn.run('osiris_api.main:app', host='127.0.0.1', port=int(os.getenv('OSIRIS_BROWSER_API_PORT', '18043')), ssl_keyfile=str(key_file), ssl_certfile=str(ca_file), access_log=False)


if __name__ == "__main__":
    main()
