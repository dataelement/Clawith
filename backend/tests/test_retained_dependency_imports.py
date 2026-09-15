"""Smoke checks for retained dependencies loaded at optional execution boundaries."""

import aioboto3
import boto3
from lxml.html.clean import Cleaner


def test_retained_dynamic_dependencies_import() -> None:
    assert callable(Cleaner)
    assert callable(aioboto3.Session)
    assert callable(boto3.client)
