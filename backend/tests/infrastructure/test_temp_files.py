import asyncio

import pytest
from infrastructure.test_input_files import storage  # noqa: F401

from app.infrastructure.errors import Conflict
from app.infrastructure.object_storage.base import StorageError
from app.infrastructure.object_storage.temp_files import TempFileStorage


async def test_temp_cas_replacement_retains_revision_and_conditional_cleanup(storage):  # noqa: F811
    _, backend = storage
    files = TempFileStorage(backend)
    key = "a2a-temporary/request/file"
    async with asyncio.timeout(4), files.guard(key):
        first = await files.write(key, b"first", expected_revision=None)
        assert await files.write(key, b"first", expected_revision=None) == first
        with pytest.raises(Conflict):
            await files.write(key, b"other", expected_revision="wrong")
        second = await files.write(key, b"second", expected_revision=first.revision)
        assert second.revision != first.revision and (await files.read(key))[0] == b"second"
        assert not await files.delete(key, revision=first.revision)
        assert await files.delete(key, revision=second.revision)
        assert await files.inspect(key) is None
    with pytest.raises(StorageError):
        await files.write(key, b"x" * (4 * 1024 * 1024 + 1), expected_revision=None)
