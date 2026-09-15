"""ASGI entry point for the target application."""

from app.application import create_app

app = create_app()
