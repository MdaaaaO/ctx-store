import json
import os
import shutil
import stat
import tempfile
import unittest

from ctxstore import fs
from tests.harness import FIXTURE, ctx, doctor_stable, golden


class Doctor(unittest.TestCase):
    def test_golden(self):
        code, out, err = ctx("doctor", "--json", env={"CTX_STORE": FIXTURE})
        self.assertEqual((code, err), (0, ""))
        stable = doctor_stable(out)
        self.assertEqual(stable, golden("doctor.json", stable))

    def test_text(self):
        code, out, _ = ctx("doctor", "--store", FIXTURE)
        self.assertEqual(code, 0)
        self.assertIn(f"store: {os.path.realpath(FIXTURE)} (markdown, from flag)\n  schema version: 1\n", out)
        self.assertRegex(out, r"  lock mode: (flock|mkdir|none)\n$")

    def test_deterministic(self):
        env = {"CTX_STORE": FIXTURE}
        self.assertEqual(ctx("doctor", "--json", env=env), ctx("doctor", "--json", env=env))

    def test_walks_up(self):
        inside = os.path.join(FIXTURE, "reference")
        code, out, _ = ctx("doctor", "--json", cwd=inside, walk=True)
        self.assertEqual(code, 0)
        store = json.loads(out)["data"]["stores"][0]
        self.assertEqual(store["path"], os.path.realpath(FIXTURE))

    def test_finds_nested_context_dir(self):
        with tempfile.TemporaryDirectory() as work:
            shutil.copytree(FIXTURE, os.path.join(work, ".context"))
            deep = os.path.join(work, "repo", "src")
            os.makedirs(deep)
            code, out, _ = ctx("doctor", "--json", cwd=deep, walk=True)
            self.assertEqual(code, 0)
            path = json.loads(out)["data"]["stores"][0]["path"]
            self.assertEqual(path, os.path.realpath(os.path.join(work, ".context")))

    def test_no_walk(self):
        inside = os.path.join(FIXTURE, "reference")
        code, out, err = ctx("doctor", cwd=inside)
        self.assertEqual((code, out, err), (2, "", "NO_STORE: no store found\n"))
        code, out, _ = ctx("doctor", "--json", cwd=inside, env={"CTX_STORE": FIXTURE})
        self.assertEqual(json.loads(out)["data"]["store_source"], "env")
        code, out, _ = ctx("doctor", "--json", "--store", FIXTURE, cwd=inside)
        self.assertEqual(json.loads(out)["data"]["store_source"], "flag")
        code, out, _ = ctx("doctor", "--json", cwd=inside, walk=True)
        self.assertEqual(json.loads(out)["data"]["store_source"], "walk")

    def test_marker_less_directory_never_matches(self):
        with tempfile.TemporaryDirectory() as work:
            os.makedirs(os.path.join(work, ".context", "reference"))
            self.assertEqual(ctx("doctor", cwd=work, walk=True)[0], 2)

    def test_store_list(self):
        with tempfile.TemporaryDirectory() as work:
            second = os.path.join(work, "second")
            shutil.copytree(FIXTURE, second)
            env = {"CTX_STORE": os.pathsep.join([FIXTURE, second])}
            code, out, _ = ctx("doctor", "--json", env=env)
            self.assertEqual(code, 0)
            paths = [s["path"] for s in json.loads(out)["data"]["stores"]]
            self.assertEqual(paths, [os.path.realpath(FIXTURE), os.path.realpath(second)])

    def test_no_store(self):
        with tempfile.TemporaryDirectory() as work:
            code, out, err = ctx("doctor", cwd=work, walk=True)
            self.assertEqual((code, out, err), (2, "", "NO_STORE: no store found\n"))
            code, _, err = ctx("doctor", "--store", work)
            self.assertEqual((code, err), (2, f"NO_STORE {work}: no store found\n"))

    def test_bad_marker(self):
        for marker in ("{", "{}", '{"schema_version": "1"}', '{"schema_version": 0}', "[]"):
            with tempfile.TemporaryDirectory() as work:
                with open(os.path.join(work, fs.MARKER), "w") as handle:
                    handle.write(marker)
                code, _, err = ctx("doctor", "--store", work)
                self.assertEqual(code, 3, marker)
                self.assertEqual(err, "SCHEMA_VIOLATION ctx-store.json: schema violation\n")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores modes")
    def test_unreadable_marker(self):
        with tempfile.TemporaryDirectory() as work:
            marker = os.path.join(work, fs.MARKER)
            with open(marker, "w") as handle:
                handle.write('{"schema_version": 1}')
            os.chmod(marker, 0)
            code, out, err = ctx("doctor", "--store", work)
            self.assertEqual((code, out), (3, ""))
            self.assertEqual(err, "SCHEMA_VIOLATION ctx-store.json: schema violation\n")

    def test_environment(self):
        env = {
            "CTX_STORE": FIXTURE,
            "CTX_LOCK_TIMEOUT": "2.5",
            "CTX_CACHE_DIR": "/nonexistent/cache",
            "CTX_SCRATCH": "/nonexistent/scratch",
            "CTX_GIT": "1",
        }
        data = json.loads(ctx("doctor", "--json", env=env)[1])["data"]
        self.assertEqual(data["lock_timeout"], 2.5)
        self.assertEqual(data["cache_dir"], {"path": "/nonexistent/cache", "exists": False})
        self.assertEqual(data["scratch"], "/nonexistent/scratch")
        self.assertTrue(data["git"])

    def test_cache_dir_from_xdg_then_home(self):
        env = {"CTX_STORE": FIXTURE, "XDG_CACHE_HOME": "/x", "HOME": "/h"}
        data = json.loads(ctx("doctor", "--json", env=env)[1])["data"]
        self.assertEqual(data["cache_dir"]["path"], os.path.join("/x", "ctx"))
        env.pop("XDG_CACHE_HOME")
        data = json.loads(ctx("doctor", "--json", env=env)[1])["data"]
        self.assertEqual(data["cache_dir"]["path"], os.path.join("/h", ".cache", "ctx"))

    def test_bad_lock_timeout(self):
        for value in ("soon", "-1", "nan", "inf"):
            code, _, err = ctx("doctor", env={"CTX_STORE": FIXTURE, "CTX_LOCK_TIMEOUT": value})
            self.assertEqual((code, err), (1, "USAGE CTX_LOCK_TIMEOUT: bad command line\n"), value)

    def test_writes_nothing(self):
        with tempfile.TemporaryDirectory() as work:
            store = os.path.join(work, "store")
            shutil.copytree(FIXTURE, store)
            before = _snapshot(store)
            env = {"CTX_STORE": store, "XDG_CACHE_HOME": os.path.join(work, "cache")}
            code, out, _ = ctx("doctor", "--json", env=env)
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(out)["data"]["stores"][0]["read_only"])
            self.assertEqual(_snapshot(store), before)
            self.assertEqual(sorted(os.listdir(work)), ["store"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores modes")
    def test_unwritable_store(self):
        with tempfile.TemporaryDirectory() as work:
            store = os.path.join(work, "store")
            shutil.copytree(FIXTURE, store)
            os.chmod(store, stat.S_IRUSR | stat.S_IXUSR)
            try:
                data = json.loads(ctx("doctor", "--json", "--store", store)[1])["data"]
            finally:
                os.chmod(store, stat.S_IRWXU)
            self.assertTrue(data["stores"][0]["read_only"])
            self.assertIn(data["stores"][0]["lock_mode"], ("flock", "none"))

    @unittest.skipUnless(os.environ.get("CTX_TEST_RO_STORE"), "needs a read-only mount")
    def test_read_only_mount(self):
        """CI mounts the fixture store read-only and names it here."""
        store = os.environ["CTX_TEST_RO_STORE"]
        code, out, err = ctx("doctor", "--json", "--store", store)
        self.assertEqual((code, err), (0, ""))
        data = json.loads(out)["data"]["stores"][0]
        self.assertTrue(data["read_only"])
        self.assertEqual(data["schema_version"], 1)
        self.assertIn(data["lock_mode"], ("flock", "none"))


class Mounts(unittest.TestCase):
    def test_longest_mount_wins(self):
        with tempfile.NamedTemporaryFile("w", suffix=".mounts") as table:
            table.write(
                "rootfs / ext4 rw 0 0\n"
                "share /mnt/my\\040share nfs4 rw 0 0\n"
                "tmp /mnt/my\\040share/deep tmpfs rw 0 0\n"
                "broken\n"
            )
            table.flush()
            self.assertEqual(fs.filesystem("/home/u/store", table.name), "ext4")
            self.assertEqual(fs.filesystem("/mnt/my share/store", table.name), "nfs4")
            self.assertEqual(fs.filesystem("/mnt/my share/deep", table.name), "tmpfs")
            self.assertEqual(fs.filesystem("/mnt/my sharedeep", table.name), "ext4")

    def test_no_mount_table(self):
        self.assertEqual(fs.filesystem("/x", "/nonexistent/mounts"), "unknown")


def _snapshot(root):
    seen = []
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            with open(path, "rb") as handle:
                seen.append((os.path.relpath(path, root), handle.read()))
    return sorted(seen)


if __name__ == "__main__":
    unittest.main()
