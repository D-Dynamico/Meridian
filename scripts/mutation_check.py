"""Mutation checks (docs/TEST_PLAN.md §17): break one guard at a time, confirm a test fails.

A test that stays green when the code it guards is deleted protects nothing. Each entry in
scripts/mutations.json is one deliberate break: a text replacement in one source file.
For each, this script applies the edit, runs the tests, and restores the file in a
finally block, so even an interrupted run leaves the source as it was.

An entry may name the tests that must catch it ("expect"). Then those tests, and only
those, are run, and every one of them must fail. Without "expect", any failing test in
the suite counts, and the run stops at the first failure to save time.

Usage, from the repo root:
    python scripts/mutation_check.py            run every mutation
    python scripts/mutation_check.py --check    only verify every pattern still matches
    python scripts/mutation_check.py zip-slip   run the mutations whose name contains this

Patterns are exact source text. When a refactor changes that text, --check names the
entry, so a mutation can never be skipped silently.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUTATIONS = Path(__file__).with_name("mutations.json")


def load_mutations(source: Path, name_filter: str | None) -> list[dict]:
    entries = json.loads(source.read_text(encoding="utf-8"))
    if name_filter:
        entries = [m for m in entries if name_filter.lower() in m["name"].lower()]
    return entries


def edits_of(mutation: dict) -> list[tuple[str, str]]:
    return [tuple(e) for e in mutation["edits"]]


def apply(mutation: dict) -> tuple[Path, bytes]:
    """Apply a mutation and return what is needed to undo it. Reads and writes UTF-8
    explicitly: the Windows default, cp1252, would corrupt every non-ASCII character."""
    path = ROOT / mutation["path"]
    original = path.read_bytes()
    text = original.decode("utf-8")
    for old, new in edits_of(mutation):
        if old not in text:
            raise LookupError(f"{mutation['name']}: pattern not found in {mutation['path']}: {old[:60]!r}")
        text = text.replace(old, new)
    path.write_bytes(text.encode("utf-8"))
    return path, original


def run_tests(mutation: dict) -> tuple[bool, str]:
    """Return (caught, summary line)."""
    expected = mutation.get("expect", [])
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    command += ["-rf", *expected] if expected else ["-x"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    lines = result.stdout.strip().splitlines()
    summary = lines[-1] if lines else result.stderr.strip()[-200:]
    if not expected:
        return result.returncode != 0, summary
    failed = {line.split()[1].split("[")[0] for line in lines if line.startswith("FAILED ")}
    missing = [test for test in expected if test not in failed]
    if missing:
        return False, f"{summary} | expected to fail but passed: {', '.join(missing)}"
    return True, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name_filter", nargs="?", help="run only mutations whose name contains this")
    parser.add_argument("--check", action="store_true", help="only verify the patterns match")
    parser.add_argument(
        "--mutations", type=Path, default=MUTATIONS, help="mutation list (default: scripts/mutations.json)"
    )
    args = parser.parse_args()

    mutations = load_mutations(args.mutations, args.name_filter)
    missed, broken = [], []
    for number, mutation in enumerate(mutations, 1):
        label = f"[{number}/{len(mutations)}] {mutation['name']}"
        try:
            path, original = apply(mutation)
        except LookupError as exc:
            broken.append(str(exc))
            print(f"BROKEN  {label}: pattern not found", flush=True)
            continue
        try:
            if args.check:
                print(f"OK      {label}", flush=True)
                continue
            caught, summary = run_tests(mutation)
            print(f"{'CAUGHT' if caught else 'MISSED'}  {label}: {summary}", flush=True)
            if not caught:
                missed.append(mutation["name"])
        finally:
            path.write_bytes(original)

    print()
    for problem in broken:
        print(f"Pattern no longer matches: {problem}")
    if missed:
        print(f"{len(missed)} mutation(s) not caught: {', '.join(missed)}")
    if not (missed or broken):
        verb = "match" if args.check else "were caught"
        print(f"All {len(mutations)} mutations {verb}.")
    return 1 if missed or broken else 0


if __name__ == "__main__":
    sys.exit(main())
