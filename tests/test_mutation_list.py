"""The mutation check's list stays in step with the code: every mutant's original text is still in its file, once.

(A mutant whose text moved is skipped by scripts/mutation_check.py and reported as not caught; this test says so in
the ordinary suite, minutes earlier, with the names.)
"""
import os
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def mutants() -> dict:
    with open(os.path.join(ROOT, "scripts", "mutation_check.py"), encoding="utf-8") as f:
        src = f.read()
    start = src.index("MUTANTS = {")
    end = src.index("\n}\n", start) + 3
    ns: dict = {}
    exec(src[start:end], ns)  # just the dict literal: importing the script would run it
    return ns["MUTANTS"]


class MutationList(unittest.TestCase):
    def test_every_mutant_still_finds_its_text(self):
        missing, ambiguous = [], []
        for name, (rel, original, *_rest) in mutants().items():
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                n = f.read().count(original)
            (missing if n == 0 else ambiguous if n > 1 else []).append(name)
        self.assertEqual(missing, [], "code changed under these mutants: update their text in scripts/mutation_check.py")
        self.assertEqual(ambiguous, [], "these mutants' text appears more than once")


if __name__ == "__main__":
    unittest.main()
