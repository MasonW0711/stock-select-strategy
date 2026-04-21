import glob
import py_compile


def test_all_py_files_compile():
    py_files = [
        f
        for f in glob.glob("**/*.py", recursive=True)
        if ".venv" not in f and "venv" not in f and ".git" not in f and ".github" not in f
    ]
    for f in py_files:
        py_compile.compile(f, doraise=True)
