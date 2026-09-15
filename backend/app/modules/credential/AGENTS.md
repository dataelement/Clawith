# Credential module

Credential owns encrypted product Secret bytes and non-Secret ownership metadata. Callers use
`public.py`; `models.py`, `repository.py`, and ciphertext are private. Encryption requires an
explicitly injected keyring and fails closed for unknown keys, unsupported payload versions, or
authentication failure. Capability owners validate a Credential reference through metadata, then
reveal it only at their external execution boundary using the exact resolved owner tuple.

Secret rotation changes bytes under the same Credential identity. It does not change grants or
cancel Runs. Do not add plaintext fallback, generic grants, provider orchestration, or Secret values
to public metadata, logs, representations, Run state, or API projections.
