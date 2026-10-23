"""Export a self-contained runtime, including this installed package's source."""
from __future__ import annotations
from pathlib import Path
import shutil
import tempfile


def export_runtime(directory):
    destination = Path(directory).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    source = Path(__file__).resolve().parent
    if source == destination or source in destination.parents:
        raise ValueError("export outside the installed package")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".android-console-export-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "runtime"
        shutil.copytree(source / "runtime_assets", stage)
        package = stage / "python-package"
        shutil.copytree(source, package / "src" / "android_console",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (package / "pyproject.toml").write_text('''[project]
name = "android-console"
version = "0.1.0"
requires-python = ">=3.11"
license = "MIT"
dependencies = []
[project.scripts]
android-console = "android_console.cli:main"
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/android_console"]
''')
        (stage / ".dockerignore").write_text(".git\n.env*\n*.key\n*.pem\n__pycache__\n*.pyc\n")
        for script in stage.glob("*.sh"):
            script.chmod(0o755)
        # mkdir reserves the exact destination; an existing folder is never replaced.
        destination.mkdir()
        try:
            for child in stage.iterdir():
                shutil.move(str(child), destination / child.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return destination
