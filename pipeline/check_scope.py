"""PR check loads this script from the trusted base revision."""
import os
from pathlib import Path
import sys
from patch_boundary import canonical_patch, git


def main():
    base, head = os.environ["BASE_SHA"], os.environ["HEAD_SHA"]
    patch = git(Path.cwd(), "diff", "--binary", "--no-renames", base, head)
    canonical_patch(Path.cwd(), base, patch)
    print("Repair scope verified: regular application files under src/ only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
