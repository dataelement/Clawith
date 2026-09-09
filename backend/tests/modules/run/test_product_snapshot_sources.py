from dataclasses import replace
from uuid import uuid4

from modules.run.test_snapshot import snapshot

from app.modules.run.snapshot import SourceSection, decode_snapshot, derive_child, encode_snapshot, model_visible_prefix


def test_private_memory_marker_survives_snapshot_and_child_without_changing_default_wire():
    original = snapshot()
    ordinary = encode_snapshot(original)
    assert "allow_shared_memory_writes" not in ordinary.payload["workspace"]
    assert decode_snapshot(ordinary.version, ordinary.payload, ordinary.content_hash) == original
    private = replace(original, workspace=replace(original.workspace, allow_shared_memory_writes=False))
    encoded = encode_snapshot(private)
    restored = decode_snapshot(encoded.version, encoded.payload, encoded.content_hash)
    assert not restored.workspace.allow_shared_memory_writes
    assert not derive_child(restored, run_id=uuid4()).workspace.allow_shared_memory_writes


def test_product_context_is_a_distinct_persisted_reference_source():
    original = snapshot()
    source = SourceSection("product_context", original.workspace.output, "session:example:through:3", "Past input and reply")
    changed = replace(original, sources=original.sources + (source,))
    encoded = encode_snapshot(changed)
    restored = decode_snapshot(encoded.version, encoded.payload, encoded.content_hash)
    assert restored.sources[-1] == source
    assert model_visible_prefix(restored)[-1].category == "product_context"
