"""The lookup eval of `bench/eval.py` is the record of what `find` finds. A
change to `find` shows here as a diff of the golden."""
import os
import subprocess
import sys
import unittest

from tests.harness import ROOT, golden


class Eval(unittest.TestCase):
    def test_baseline(self):
        done = subprocess.run([sys.executable, os.path.join(ROOT, "bench", "eval.py")],
                              capture_output=True, text=True, env={}, timeout=60)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout, golden("find-eval.txt", done.stdout))


if __name__ == "__main__":
    unittest.main()
