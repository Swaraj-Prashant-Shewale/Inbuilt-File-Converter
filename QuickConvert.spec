# PyInstaller spec for the standalone (no-Python) build of Quick Convert.
# Build with:  .\build_exe.ps1   (or: pyinstaller --noconfirm QuickConvert.spec)
#
# Most conversion packages are imported lazily inside engines.py, so PyInstaller
# cannot discover them by static analysis - they are pulled in explicitly here.
from PyInstaller.utils.hooks import collect_all

# Packages with data files and/or C extensions that must be collected whole.
_pkgs = [
    "PIL", "pillow_heif",           # images
    "pymupdf", "pdf2docx", "docx", "fontTools", "lxml",  # pdf <-> docx
    "py7zr", "pyppmd", "inflate64", "brotli",            # 7z codecs
    "multivolumefile", "texttable", "Cryptodome", "backports.zstd",
    "win32com",                     # drive Microsoft Word for docx->pdf
]

datas, binaries, hiddenimports = [], [], []
for _p in _pkgs:
    try:
        d, b, h = collect_all(_p)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # a package that isn't installed is simply skipped
        print(f"[spec] collect_all skipped {_p}: {e}")

# Lazily-imported modules PyInstaller's analysis would otherwise miss.
hiddenimports += ["fitz", "cv2", "numpy", "fire", "zstandard", "backports.zstd",
                  "win32com.client", "pythoncom", "pywintypes", "win32api",
                  "win32timezone"]

# Trim dev/test-only packages AND the heavy ML/data-science stack that happens
# to be installed on the build machine - PyInstaller pulls in anything an
# optional `try: import torch`-style guard references, ballooning the bundle to
# gigabytes. Quick Convert needs none of these (keep numpy/cv2/fontTools/lxml).
# (Do NOT exclude lxml/setuptools - python-docx needs them at runtime.)
_excludes = [
    # dev/test only
    "pytest", "sphinx", "mypy", "black", "pylint", "isort", "flake8",
    "twine", "coverage", "IPython", "notebook", "jedi", "pydoc_data",
    # ML / DL frameworks and their GPU runtimes (multi-GB, unused)
    "torch", "torchvision", "torchaudio", "tensorflow", "tensorboard",
    "keras", "jax", "jaxlib", "transformers", "tokenizers", "safetensors",
    "onnx", "onnxruntime", "ctranslate2", "sentencepiece", "faiss",
    # scientific / data stack (unused by any conversion path)
    "scipy", "pandas", "sympy", "numba", "llvmlite", "pyarrow", "duckdb",
    "matplotlib", "sklearn", "scikit_learn", "statsmodels", "gensim",
    "spacy", "nltk", "av",
    # other GUI toolkits (we use tkinter)
    "PySide6", "shiboken6", "PyQt5", "PyQt6", "PySide2", "wx",
]

a = Analysis(
    ["converter.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QuickConvert",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed app (GUI); no console window
    icon="quickconvert.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="QuickConvert",    # -> <distpath>/QuickConvert/QuickConvert.exe
)
