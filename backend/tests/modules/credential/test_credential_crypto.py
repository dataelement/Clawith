from uuid import uuid4

import pytest

from app.infrastructure.errors import InvalidInput
from app.modules.credential.crypto import CredentialKeyring, Secret


def test_secret_repr_is_redacted_and_ciphertexts_use_fresh_nonces() -> None:
    keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"a" * 32})
    credential_id, tenant_id = uuid4(), uuid4()

    first = keyring.encrypt(credential_id=credential_id, tenant_id=tenant_id, secret=Secret("value"))
    second = keyring.encrypt(credential_id=credential_id, tenant_id=tenant_id, secret=Secret("value"))

    assert first[0] != second[0]
    assert repr(Secret("do-not-print")) == "Secret(<redacted>)"
    assert keyring.decrypt(
        credential_id=credential_id,
        tenant_id=tenant_id,
        encrypted_payload=first[0],
        payload_version=first[1],
        key_version=first[2],
    ) == Secret("value")


def test_wrong_key_tenant_corruption_and_unknown_version_fail_closed() -> None:
    credential_id, tenant_id = uuid4(), uuid4()
    keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"a" * 32})
    encrypted, version, key_version = keyring.encrypt(
        credential_id=credential_id, tenant_id=tenant_id, secret=Secret("value")
    )

    attempts = (
        (CredentialKeyring(active_key_version="k1", keys={"k1": b"b" * 32}), tenant_id, encrypted, version),
        (keyring, uuid4(), encrypted, version),
        (keyring, tenant_id, encrypted[:-1] + bytes([encrypted[-1] ^ 1]), version),
        (keyring, tenant_id, encrypted, version + 1),
    )
    for candidate, bound_tenant, payload, payload_version in attempts:
        with pytest.raises(InvalidInput):
            candidate.decrypt(
                credential_id=credential_id,
                tenant_id=bound_tenant,
                encrypted_payload=payload,
                payload_version=payload_version,
                key_version=key_version,
            )
