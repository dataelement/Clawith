"""PyInstaller entry point.

PyInstaller loads the target script as `__main__`, which breaks relative
imports inside the package. Use this file as the build entry so
`clawith_bridge.__main__` loads normally as a submodule.
"""
from clawith_bridge.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
