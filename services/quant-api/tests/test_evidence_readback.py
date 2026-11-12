"""Retention succeeds only for the signed bytes read from the exact S3 version."""

import base64
import hashlib
from datetime import UTC, datetime
from io import BytesIO

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from osiris_api.control_plane.custody import S3Custody, sign_bundle


class S3Fixture:
    def __init__(self, mutate=None):
        self.mutate = mutate or (lambda response: None)
        self.read_calls = []

    def get_object_lock_configuration(self, **kwargs):
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def get_bucket_versioning(self, **kwargs):
        return {"Status": "Enabled"}

    def put_object(self, **kwargs):
        self.stored = kwargs
        return {"VersionId": "immutable-version-1"}

    def get_object_retention(self, **kwargs):
        assert kwargs["VersionId"] == "immutable-version-1"
        return {
            "Retention": {
                "Mode": "COMPLIANCE",
                "RetainUntilDate": self.stored["ObjectLockRetainUntilDate"],
            }
        }

    def get_object(self, **kwargs):
        self.read_calls.append(kwargs)
        self.body = BytesIO(self.stored["Body"])
        response = {
            "VersionId": "immutable-version-1",
            "Body": self.body,
            "ContentLength": len(self.stored["Body"]),
            "ServerSideEncryption": "aws:kms",
            "ChecksumSHA256": base64.b64encode(
                hashlib.sha256(self.stored["Body"]).digest()
            ).decode(),
        }
        self.mutate(response)
        return response


@pytest.fixture
def evidence():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return sign_bundle({"run": "synthetic-local-fixture", "live_execution": False}, key), public


def retain(s3, evidence):
    return S3Custody(
        s3, "fixture-bucket", "fixture-kms", clock=lambda: datetime(2026, 11, 12, tzinfo=UTC)
    ).retain("proof", *evidence)


def test_retention_reads_and_hashes_exact_immutable_version(evidence):
    s3 = S3Fixture()
    result = retain(s3, evidence)
    assert s3.read_calls == [
        {
            "Bucket": "fixture-bucket",
            "Key": result["key"],
            "VersionId": "immutable-version-1",
            "ChecksumMode": "ENABLED",
        }
    ]
    assert result["sha256"] == hashlib.sha256(s3.stored["Body"]).hexdigest()
    assert result["content_readback_verified"] is True
    assert result["independent_key_custody_attested"] is False
    assert s3.body.closed


@pytest.mark.parametrize(
    "field,value",
    [
        ("VersionId", "another-version"),
        ("VersionId", None),
        ("ContentLength", 0),
        ("ContentLength", True),
        ("ContentLength", "100"),
        ("ServerSideEncryption", "AES256"),
        ("ServerSideEncryption", None),
        ("ChecksumSHA256", "AAAA"),
    ],
)
def test_metadata_or_checksum_mismatch_never_becomes_verified(evidence, field, value):
    s3 = S3Fixture(lambda response: response.update({field: value}))
    with pytest.raises(ValueError, match="evidence_readback_not_verified"):
        retain(s3, evidence)
    assert s3.body.closed


@pytest.mark.parametrize("change", ["corrupt", "truncated", "extra"])
def test_retention_metadata_cannot_substitute_for_matching_bytes(evidence, change):
    s3 = S3Fixture()

    def mutate(response):
        raw = s3.stored["Body"]
        altered = {"corrupt": b"X" + raw[1:], "truncated": raw[:-1], "extra": raw + b"X"}[change]
        s3.body.close()
        s3.body = response["Body"] = BytesIO(altered)
        # Matching the original length in the metadata must still fail.

    s3.mutate = mutate
    with pytest.raises(ValueError, match="evidence_readback_not_verified"):
        retain(s3, evidence)
    assert s3.body.closed


def test_readback_is_bounded_and_stream_closes_on_failure(evidence):
    class Endless(BytesIO):
        def read(self, size=-1):
            assert size == len(s3.stored["Body"]) + 1
            return b"X" * size

    s3 = S3Fixture()

    def mutate(response):
        s3.body.close()
        s3.body = response["Body"] = Endless()

    s3.mutate = mutate
    with pytest.raises(ValueError, match="evidence_readback_not_verified"):
        retain(s3, evidence)
    assert s3.body.closed


def test_verified_bytes_suffice_when_server_checksum_is_absent(evidence):
    s3 = S3Fixture(lambda response: response.pop("ChecksumSHA256"))
    assert retain(s3, evidence)["content_readback_verified"] is True


def test_network_callbacks_cannot_change_already_verified_bundle(evidence):
    import json

    from osiris_api.control_plane.custody import verify_bundle

    class MutatingS3(S3Fixture):
        def get_bucket_versioning(self, **kwargs):
            evidence[0]["payload"]["run"] = "changed-after-verification"
            return super().get_bucket_versioning(**kwargs)

    s3 = MutatingS3()
    assert retain(s3, evidence)["content_readback_verified"] is True
    saved = verify_bundle(json.loads(s3.stored["Body"]), evidence[1])
    assert saved["run"] == "synthetic-local-fixture"


def test_unreadable_body_is_never_success_and_stream_closes(evidence):
    class Broken(BytesIO):
        def read(self, size=-1):
            raise OSError("fixture read failure")

    s3 = S3Fixture()

    def mutate(response):
        s3.body.close()
        s3.body = response["Body"] = Broken()

    s3.mutate = mutate
    with pytest.raises(OSError, match="fixture read failure"):
        retain(s3, evidence)
    assert s3.body.closed
