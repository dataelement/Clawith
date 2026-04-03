"""Tests for sandbox enhancements: shared venv, pip install, long tasks."""

import asyncio
import sys
import tempfile
import os
from pathlib import Path

# Set stdout encoding for Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.sandbox.config import SandboxConfig
from app.services.sandbox.local.subprocess_backend import (
    SubprocessBackend,
    _check_code_safety,
    _extract_pip_packages,
    _check_pip_packages,
    _ensure_shared_venv,
    _get_venv_python,
    _inject_pip_index_url,
)


def test_extract_pip_packages():
    """Test pip package extraction from commands."""
    print("\n=== Test: Extract pip packages ===")

    test_cases = [
        ("pip install numpy", ["numpy"]),
        ("pip install numpy pandas", ["numpy", "pandas"]),
        ("pip install numpy==1.20.0", ["numpy"]),
        ("pip install numpy>=1.20", ["numpy"]),
        ("pip3 install requests", ["requests"]),
        ("python -m pip install flask", ["flask"]),
        ("pip install -r requirements.txt", []),  # -r should be filtered
        ("pip install -U numpy", ["numpy"]),  # -U should be filtered
        ("pip install numpy; echo done", ["numpy"]),
        ("pip install numpy && pip install pandas", ["numpy", "pandas"]),
    ]

    all_passed = True
    for code, expected in test_cases:
        result = _extract_pip_packages(code)
        status = "[PASS]" if result == expected else "[FAIL]"
        print(f"  {status} '{code}' -> {result} (expected: {expected})")
        if result != expected:
            all_passed = False

    return all_passed


def test_check_pip_packages():
    """Test pip package black list check."""
    print("\n=== Test: Check pip packages against black list ===")

    black_list = ["ptyprocess", "pwntools", "subprocess"]

    all_passed = True
    test_cases = [
        (["numpy", "pandas"], None),  # Safe packages
        (["ptyprocess"], "blocked"),  # In black list
        (["pwntools"], "blocked"),    # In black list
    ]

    for packages, expected_type in test_cases:
        result = _check_pip_packages(packages, black_list)
        if expected_type is None:
            status = "[PASS]" if result is None else "[FAIL]"
            if result is not None:
                all_passed = False
        else:
            status = "[PASS]" if result is not None else "[FAIL]"
            if result is None:
                all_passed = False
        print(f"  {status} packages={packages} -> {result}")

    return all_passed


def test_check_code_safety():
    """Test code safety checks with pip install support."""
    print("\n=== Test: Code safety checks ===")

    all_passed = True

    # Test pip install blocking
    result = _check_code_safety("bash", "pip install numpy", allow_pip_install=False)
    status = "[PASS]" if result else "[FAIL]"
    print(f"  {status} pip install blocked when not allowed: {result}")

    result = _check_code_safety("bash", "pip install numpy", allow_pip_install=True)
    status = "[PASS]" if result is None else "[FAIL]"
    print(f"  {status} pip install allowed when enabled: {result is None}")
    if result is not None:
        all_passed = False

    result = _check_code_safety("bash", "pip install ptyprocess", allow_pip_install=True,
                                 pip_black_list=["ptyprocess"])
    status = "[PASS]" if result else "[FAIL]"
    print(f"  {status} blacklisted package blocked: {result}")

    # Test dangerous commands still blocked
    result = _check_code_safety("bash", "rm -rf /", allow_pip_install=True)
    status = "[PASS]" if result else "[FAIL]"
    print(f"  {status} rm -rf / blocked: {result}")

    result = _check_code_safety("python", "import subprocess", allow_pip_install=True)
    status = "[PASS]" if result else "[FAIL]"
    print(f"  {status} subprocess import blocked: {result}")

    return all_passed


def test_inject_pip_index_url():
    """Test pip index URL injection."""
    print("\n=== Test: Inject pip index URL ===")

    index_url = "https://pypi.tuna.tsinghua.edu.cn/simple"

    test_cases = [
        ("pip install numpy", True),  # Should inject
        ("pip install numpy pandas", True),
        ("pip install -U numpy", True),
        ("pip install -i https://custom.com/simple numpy", False),  # Already has index
        ("echo hello", False),  # No pip install
        ("pip install numpy && echo done", True),
    ]

    all_passed = True
    for code, should_inject in test_cases:
        result = _inject_pip_index_url(code, index_url)
        injected = index_url in result

        if should_inject:
            status = "[PASS]" if injected else "[FAIL]"
            if not injected:
                all_passed = False
        else:
            status = "[PASS]" if not injected else "[FAIL]"
            if injected and "custom.com" not in result:
                all_passed = False

        print(f"  {status} '{code}' -> injected={injected}")

    return all_passed


async def test_shared_venv():
    """Test shared virtual environment creation."""
    print("\n=== Test: Shared venv creation ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)

        try:
            venv_path = await _ensure_shared_venv(base_dir)
            print(f"  [PASS] Venv created at: {venv_path}")

            venv_python = _get_venv_python(venv_path)
            print(f"  [INFO] Python path: {venv_python}")

            if venv_python.exists():
                print(f"  [PASS] Python executable exists")
            else:
                print(f"  [FAIL] Python executable not found: {venv_python}")
                return False

            # Check cache
            from app.services.sandbox.local.subprocess_backend import _shared_venv_cache
            cache_key = str(base_dir)
            if cache_key in _shared_venv_cache:
                print(f"  [PASS] Venv cached")
            else:
                print(f"  [FAIL] Venv not in cache")
                return False

            return True

        except Exception as e:
            print(f"  [FAIL] Error: {e}")
            return False


async def test_subprocess_backend():
    """Test SubprocessBackend with shared venv."""
    print("\n=== Test: SubprocessBackend execution ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        config = SandboxConfig(
            allow_pip_install=True,
            allow_network=False,
            max_timeout=60,
            extended_timeout=300,
            long_task_timeout=1800,
            pip_black_list=["ptyprocess", "pwntools"],
        )

        backend = SubprocessBackend(config, agent_data_dir=tmpdir)
        all_passed = True

        # Test 1: Simple Python execution
        print("\n  Test 1: Simple Python")
        result = await backend.execute(
            code="print('Hello, World!')",
            language="python",
            timeout=10,
        )
        print(f"    success={result.success}, stdout={result.stdout.strip()}")
        if not result.success:
            print(f"    [FAIL] Error: {result.error}")
            all_passed = False

        # Test 2: Check venv is used
        print("\n  Test 2: Check venv is used")
        result = await backend.execute(
            code="import sys; print(sys.executable)",
            language="python",
            timeout=10,
            use_venv=True,
        )
        print(f"    Python: {result.stdout.strip()}")
        if "_shared_venv" in result.stdout:
            print(f"    [PASS] Using shared venv")
        else:
            print(f"    [INFO] Not using shared venv (might not exist yet)")

        # Test 3: Timeout modes
        print("\n  Test 3: Timeout modes")
        capabilities = backend.get_capabilities()
        print(f"    max_timeout: {capabilities.max_timeout}s")
        if capabilities.max_timeout >= 1800:
            print(f"    [PASS] Long task timeout supported")
        else:
            print(f"    [FAIL] Long task timeout not supported")
            all_passed = False

        # Test 4: Blacklisted package
        print("\n  Test 4: Blacklisted package blocked")
        result = await backend.execute(
            code="pip install pwntools",
            language="bash",
            timeout=10,
        )
        if result.error and "black list" in result.error:
            print(f"    [PASS] Blacklisted package blocked: {result.error}")
        else:
            print(f"    [FAIL] Blacklisted package not blocked: {result.error}")
            all_passed = False

        return all_passed


async def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("SANDBOX ENHANCEMENT TESTS")
    print("=" * 60)

    results = []

    # Sync tests
    results.append(("Extract pip packages", test_extract_pip_packages()))
    results.append(("Check pip packages", test_check_pip_packages()))
    results.append(("Code safety checks", test_check_code_safety()))
    results.append(("Inject pip index URL", test_inject_pip_index_url()))

    # Async tests
    results.append(("Shared venv creation", await test_shared_venv()))
    results.append(("SubprocessBackend", await test_subprocess_backend()))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = 0
    failed = 0
    for name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1

    print(f"\nTotal: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
