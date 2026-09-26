# PyInstaller spec: uv run pyinstaller packaging/aipdm.spec  (onedir, no network needed)
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

ROOT = Path(SPECPATH).parent  # noqa: F821  (defined by PyInstaller)
MODELS = [
    "face_detection_yunet_2026may.onnx",
    "face_recognition_sface_2021dec.onnx",
    "ch_PP-OCRv5_det_mobile.onnx",
    "latin_PP-OCRv5_rec_mobile.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "clip_image.onnx",
    "clip_text.onnx",
    "clip_tokenizer.json",
    "places.tsv.gz",
    "LICENSES.md",
]
missing = [m for m in MODELS if not (ROOT / "models" / m).exists()]
if missing:
    raise SystemExit(f"modelos ausentes em models/: {missing}")

datas = [(str(ROOT / "models" / m), "models") for m in MODELS]
datas += [(str(ROOT / "src" / "aipdm" / "ui"), "aipdm/ui")]
datas += [(str(ROOT / "THIRD_PARTY_NOTICES.md"), ".")]
# rapidocr: keep its config files, drop the Chinese models its wheel ships (we pass our own)
datas += [d for d in collect_data_files("rapidocr") if "models" not in Path(d[1]).parts]
datas += collect_data_files("docx")  # default.docx template
datas += collect_data_files("pypdfium2_raw")
for dist in (
    "pillow",
    "pillow-heif",
    "pypdfium2",
    "python-docx",
    "xxhash",
    "numpy",
    "opencv-python-headless",
    "onnxruntime",
    "rapidocr",
    "tokenizers",
    "aipdm",
):
    datas += copy_metadata(dist)  # `aipdm doctor` reports these versions

binaries = collect_dynamic_libs("pypdfium2_raw")
hiddenimports = collect_submodules("uvicorn") + ["aipdm.server.app", "aipdm.server.run"]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=[
        "torch",
        "torchvision",
        "open_clip",
        "transformers",
        "sklearn",
        "scipy",
        "pytest",
        "mypy",
        "ruff",
        "IPython",
        "tkinter",
        "matplotlib",
        "onnx",
    ],
    noarchive=False,
)
if sys.platform.startswith("linux"):
    # The window uses the SYSTEM's GTK + WebKitGTK (pywebview's GTK backend). Bundling the
    # build machine's GTK stack next to the user's WebKit loads two GTKs into one process
    # (crashes) and adds ~450 MB. Drop that stack and its data; the typelibs and libraries
    # then come from the user's system (Ubuntu/Zorin: gir1.2-webkit2-4.1).
    GTK_STACK = (
        "libgtk", "libgdk", "libwebkit", "libjavascriptcore", "libsoup", "libglib", "libgobject",
        "libgio", "libgmodule", "libgirepository", "libcairo", "libpango", "libharfbuzz",
        "libicu", "libatk", "libatspi", "libepoxy", "libfontconfig", "libfreetype", "libfribidi",
        "libthai", "libdatrie", "libgraphite2", "libpixman", "libX", "libxcb", "libxkbcommon",
        "libwayland", "libdbus", "libsystemd", "libcloudproviders", "libtinysparql", "libjson-glib",
        "libgvfscommon", "libproxy", "libpxbackend", "libglycin", "libseccomp", "libmount",
        "libblkid", "libpcre2", "libcurl", "libgnutls", "libnghttp", "libngtcp2", "libssh2",
        "libpsl", "libidn2", "libunistring", "libtasn1", "libp11-kit", "libnettle", "libhogweed",
        "libgmp", "libkrb5", "libk5crypto", "libgssapi", "libcom_err", "libkeyutils",
        "libleancrypto", "libduktape", "libwmf", "liblcms2", "libtiff", "libjpeg", "libpng16",
        "libbrotli", "libexpat", "libdrm",
    )

    def system_gtk(entry):
        dest, src = entry[0], str(entry[1])
        return src.startswith(("/usr/", "/lib")) and Path(dest).name.startswith(GTK_STACK)

    a.binaries = [b for b in a.binaries if not system_gtk(b)]
    a.datas = [d for d in a.datas if not d[0].startswith(("share/", "gi_typelibs/", "lib/"))]

pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI-PhotoDocsManager",
    console=sys.platform != "win32",  # Windows: a window app, no black console behind it
    icon=str(ROOT / "packaging" / "icon.ico") if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AI-PhotoDocsManager")  # noqa: F821
