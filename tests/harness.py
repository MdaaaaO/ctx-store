"""Fixture-store harness: runs the real entry point as a subprocess."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTX = os.path.join(ROOT, "ctx")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "store-v1")
GOLDEN = os.path.join(ROOT, "tests", "golden")


def ctx(*args, env=None, cwd=None, walk=False):
    """Run ctx with a clean environment; returns (exit, stdout, stderr).

    The walk up is off unless a test asks for it, so a run that forgets its
    store can never land on a real one above the checkout."""
    env = dict(env or {})
    if not walk:
        env["CTX_NO_WALK"] = "1"
    done = subprocess.run(
        [sys.executable, CTX, *args],
        env=env,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    return done.returncode, done.stdout, done.stderr


def golden(name, actual):
    """Compare with tests/golden/<name>; CTX_UPDATE_GOLDEN=1 rewrites it."""
    path = os.path.join(GOLDEN, name)
    if os.environ.get("CTX_UPDATE_GOLDEN") == "1":
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(actual)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def doctor_stable(stdout):
    """The doctor envelope with the machine-dependent values masked."""
    envelope = json.loads(stdout)
    data = envelope["data"]
    data["python"] = "<python>"
    for store in data["stores"]:
        store["path"] = "<store>"
        store["filesystem"] = "<filesystem>"
        store["lock_mode"] = "<lock>"
        store["read_only"] = "<read-only>"
    return json.dumps(envelope, indent=2, sort_keys=True) + "\n"
