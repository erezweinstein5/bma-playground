"""Validate untrusted patches in an isolated clone before applying to the runner."""
from pathlib import Path, PurePosixPath
import subprocess
import tempfile

LIMIT = 5 * 1024 * 1024


def git(repo, *args, **kwargs):
    return subprocess.check_output(["git", "-C", str(repo), *args], **kwargs)


def canonical_patch(source, base, patch):
    if not patch or len(patch) > LIMIT or b"GIT binary patch" in patch:
        raise ValueError("Patch must be nonempty, textual, and at most 5 MiB")
    with tempfile.TemporaryDirectory(prefix="bma-patch-") as temporary:
        repo = Path(temporary) / "repo"
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(source), str(repo)],
                       check=True, stdout=subprocess.DEVNULL)
        git(repo, "checkout", "--quiet", "--detach", base)
        git(repo, "config", "core.hooksPath", "/dev/null")
        git(repo, "apply", "--check", "--index", "-", input=patch)
        git(repo, "apply", "--index", "-", input=patch)
        raw = git(repo, "diff", "--cached", "--raw", "--no-renames", "-z").split(b"\0")
        if raw == [b""]:
            raise ValueError("Patch changes no files")
        for i in range(0, len(raw) - 1, 2):
            metadata, name = raw[i].decode(), raw[i + 1].decode()
            path = PurePosixPath(name)
            if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] != "src"
                    or any(part in {".", "..", ".git"} for part in path.parts)
                    or any(ord(char) < 32 for char in name)):
                raise ValueError(f"Repair may only change files under src/: {name}")
            old_mode, new_mode = metadata[1:].split()[:2]
            if old_mode not in {"000000", "100644"} or new_mode not in {"000000", "100644"}:
                raise ValueError("Symlinks, executables, and submodules are not accepted")
        return git(repo, "diff", "--cached", "--no-ext-diff", "--no-renames", "--src-prefix=a/",
                   "--dst-prefix=b/")
