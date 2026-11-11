"""Ed25519 evidence bundles and S3 Object Lock COMPLIANCE retention verification."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .database import canonical, digest, identifier


def sign_bundle(payload: object, key: Ed25519PrivateKey) -> dict:
    return {
        "schema": "osiris.signed-evidence.v1",
        "payload": payload,
        "sha256": digest(payload),
        "signature": base64.b64encode(key.sign(canonical(payload))).decode(),
    }


def verify_bundle(bundle: dict, public_key: bytes):
    if (
        not isinstance(bundle, dict)
        or set(bundle) != {"schema", "payload", "sha256", "signature"}
        or bundle["schema"] != "osiris.signed-evidence.v1"
        or digest(bundle["payload"]) != bundle["sha256"]
    ):
        raise ValueError("invalid_signed_bundle")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            base64.b64decode(bundle["signature"], validate=True), canonical(bundle["payload"])
        )
    except Exception:
        raise ValueError("evidence_signature_invalid") from None
    return bundle["payload"]


class S3Custody:
    """Pass an explicitly configured boto3-compatible S3 client; no default credential discovery here."""

    def __init__(self, client, bucket: str, kms_key_id: str, *, clock=None):
        if not bucket or not kms_key_id:
            raise ValueError("bucket_and_kms_key_required")
        self.client, self.bucket, self.kms_key_id = client, bucket, kms_key_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def retain(self, evidence_id: str, bundle: dict, public_key: bytes, *, days: int = 30) -> dict:
        evidence_id = identifier(evidence_id)
        if type(days) is not int or not 1 <= days <= 3650:
            raise ValueError("invalid_retention_days")
        verify_bundle(bundle, public_key)
        configuration = self.client.get_object_lock_configuration(Bucket=self.bucket)
        versioning = self.client.get_bucket_versioning(Bucket=self.bucket)
        if (
            configuration.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") != "Enabled"
            or versioning.get("Status") != "Enabled"
        ):
            raise ValueError("versioned_object_lock_bucket_required")
        until, raw = self.clock() + timedelta(days=days), canonical(bundle)
        key = evidence_id + "/" + hashlib.sha256(raw).hexdigest() + ".json"
        response = self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=raw,
            IfNoneMatch="*",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=until,
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self.kms_key_id,
            ChecksumSHA256=base64.b64encode(hashlib.sha256(raw).digest()).decode(),
        )
        version = response.get("VersionId")
        if not version or version == "null":
            raise ValueError("immutable_object_version_missing")
        retention = self.client.get_object_retention(
            Bucket=self.bucket, Key=key, VersionId=version
        ).get("Retention", {})
        retained_until = retention.get("RetainUntilDate")
        if (
            retention.get("Mode") != "COMPLIANCE"
            or not isinstance(retained_until, datetime)
            or retained_until < until
        ):
            raise ValueError("object_retention_not_verified")
        return {
            "key": key,
            "version_id": version,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "retained_until": retained_until.isoformat(),
            "independent_key_custody_attested": False,
        }
