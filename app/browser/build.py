"""Assemble the in-browser build of docparse Studio into a folder ready to publish.

    python app/browser/build.py --out /path/to/dist

Downloads (from the npm registry and PyPI, which are cached in --cache):
  pyodide 0.29.5 core, pdfjs-dist 3.11.174, and pure-Python wheels for pdfminer.six,
  pdfplumber, pypdf and charset-normalizer.
Bundles: docparse/, app/{exporters,validate}.py, studio.py and the cryptography stub as pylib.zip.

Nothing here is executed in the browser except Pyodide and pdf.js; both are loaded as the
artifact's own files because the artifact sandbox only allows *scripts* from public CDNs.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PYODIDE = "0.29.5"
PDFJS = "3.11.174"
WHEELS = ["pdfminer.six==20260107", "pdfplumber==0.11.10", "pypdf==7.0.0", "charset-normalizer==3.5.2"]
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "pyodide-lock.json"]
# The artifact service refuses binary archives (.zip/.whl: 415), so those ship base64-encoded as .txt
# and the worker decodes them in memory.


def npm_extract(pkg: str, cache: str) -> str:
    """npm pack <pkg> into cache and extract it; returns the package directory."""
    dest = os.path.join(cache, pkg.replace("@", "-").replace("/", "_"))
    if not os.path.isdir(os.path.join(dest, "package")):
        os.makedirs(dest, exist_ok=True)
        tgz = subprocess.run(["npm", "pack", pkg, "--silent"], cwd=dest, check=True, capture_output=True, text=True).stdout.strip().splitlines()[-1]
        with tarfile.open(os.path.join(dest, tgz)) as t:
            t.extractall(dest, filter="data")
    return os.path.join(dest, "package")


def wheels(cache: str) -> list[str]:
    d = os.path.join(cache, "wheels")
    os.makedirs(d, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:", "--platform", "any",
                    "--python-version", "3.13", "--implementation", "py", "-d", d, *WHEELS], check=True)
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".whl"))


def b64(src: str, dst: str) -> None:
    with open(src, "rb") as f, open(dst, "w") as g:
        g.write(base64.b64encode(f.read()).decode())


def pylib() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root in ("docparse",):
            for dp, _, fs in os.walk(os.path.join(REPO, root)):
                if "__pycache__" in dp:
                    continue
                for f in fs:
                    if f.endswith(".py"):
                        p = os.path.join(dp, f)
                        z.write(p, os.path.relpath(p, REPO))
        z.writestr("app/__init__.py", "")
        for f in ("exporters.py", "validate.py"):
            z.write(os.path.join(REPO, "app", f), f"app/{f}")
        z.write(os.path.join(HERE, "studio.py"), "studio.py")
        stubs = os.path.join(HERE, "stubs")
        for dp, _, fs in os.walk(stubs):
            for f in fs:
                p = os.path.join(dp, f)
                z.write(p, os.path.relpath(p, stubs))
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=os.path.join(os.path.expanduser("~"), ".cache", "docparse-studio-build"))
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "pyodide"))
    os.makedirs(os.path.join(out, "wheels"))
    os.makedirs(os.path.join(out, "pdfjs"))
    pyo = npm_extract(f"pyodide@{PYODIDE}", a.cache)
    for f in PYODIDE_FILES:
        shutil.copy(os.path.join(pyo, f), os.path.join(out, "pyodide", f))
    b64(os.path.join(pyo, "python_stdlib.zip"), os.path.join(out, "pyodide", "python_stdlib.b64.txt"))
    pdfjs = npm_extract(f"pdfjs-dist@{PDFJS}", a.cache)
    for f in ("pdf.min.js", "pdf.worker.min.js"):
        shutil.copy(os.path.join(pdfjs, "build", f), os.path.join(out, "pdfjs", f))
    for w in wheels(a.cache):
        b64(w, os.path.join(out, "wheels", os.path.basename(w) + ".b64.txt"))
    with open(os.path.join(out, "pylib.b64.txt"), "w") as f:
        f.write(base64.b64encode(pylib()).decode())
    shutil.copy(os.path.join(HERE, "index.html"), out)
    shutil.copy(os.path.join(HERE, "worker.js"), out)
    # worker.js lists the wheel file names; check they match what was downloaded
    names = {n[: -len(".b64.txt")] for n in os.listdir(os.path.join(out, "wheels"))}
    with open(os.path.join(HERE, "worker.js")) as f:
        js = f.read()
    missing = [n for n in names if n not in js]
    if missing:
        sys.exit(f"worker.js does not list these wheels: {missing}")
    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(out) for f in fs)
    print(f"built {out} ({total / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
