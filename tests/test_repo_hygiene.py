"""Repository settings that protect sample files.

Line-ending conversion corrupts binary shapefile parts without any error at checkout. The
sample files would only fail much later, inside pyogrio, with a confusing message. These
tests ask git directly which attributes apply, so removing a line from .gitattributes turns
them red.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(),
    reason="needs a git checkout (not available inside the Docker image)",
)


def git_attributes(path: str) -> dict[str, str]:
    """Return the attributes git applies to a path. The path does not need to exist.

    core.ignorecase is forced off so the check sees what a Linux checkout sees. Windows
    turns it on by default, and then "*.SHP" also matches ".shp", which hid a missing
    "*.shp" rule when this test was first mutation-checked.
    """
    out = subprocess.run(
        ["git", "-c", "core.ignorecase=false", "check-attr", "-a", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # Each line looks like "samples/a.shp: text: unset".
    attributes = {}
    for line in out.splitlines():
        _, name, value = line.rsplit(": ", 2)
        attributes[name] = value
    return attributes


@pytest.mark.parametrize("ext", ["shp", "shx", "dbf", "zip", "SHP"])
def test_shapefile_parts_and_zips_are_binary(ext):
    attributes = git_attributes(f"samples/parcels.{ext}")
    # "binary" expands to "-text -diff -merge". "text: unset" is what stops conversion.
    assert attributes.get("text") == "unset", attributes


@pytest.mark.parametrize("path", ["Dockerfile", "app/main.py", "samples/survey.kml"])
def test_text_files_check_out_with_lf(path):
    assert git_attributes(path).get("eol") == "lf"
