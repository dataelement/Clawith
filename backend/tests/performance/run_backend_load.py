"""Run the immutable core load profile against disposable test infrastructure."""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--scenario", choices=("core",), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    profile = args.profile.resolve()
    output = args.out.resolve()
    spec = importlib.util.spec_from_file_location("load_profile_validator", ROOT / "scripts/validate_load_profile.py")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    try:
        content = json.loads(profile.read_text())
    except (OSError, ValueError) as error:
        parser.error(f"Cannot read profile: {error}")
    if issues := validator.validate_profile(content):
        parser.error("Profile differs from the accepted contract: " + "; ".join(str(issue) for issue in issues))
    environment = dict(os.environ)
    # The runner owns its disposable Compose project. It must not inherit an
    # arbitrary database destination from an interactive or production shell.
    environment.pop("CLAWITH_TEST_POSTGRES_URL", None)
    environment["CLAWITH_CORE_LOAD_PROFILE"] = str(profile)
    environment["CLAWITH_CORE_LOAD_OUT"] = str(output)
    print("Core load: 180 seconds warmup + 900 seconds measurement; setup/drain are additional.", flush=True)
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/performance/test_core_load.py::test_full_core_profile",
        "-q", "-s"], cwd=ROOT, env=environment, check=False)
    if result.returncode:
        return result.returncode
    report = json.loads(output.read_text())
    print(f"Report: {output}; qualification: {report['qualification']}", flush=True)
    return 0 if report["qualification"] == "qualified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
