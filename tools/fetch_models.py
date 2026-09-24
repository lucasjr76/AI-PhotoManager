"""Dev only: download the ONNX models and (re)write models/LICENSES.md with their SHA-256.

Usage: uv run python tools/fetch_models.py
"""

import hashlib
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"

# (file, license, source URL)
MODELS = [
    (
        "face_detection_yunet_2023mar.onnx",
        "MIT",
        f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ),
    (
        "face_recognition_sface_2021dec.onnx",
        "Apache-2.0",
        f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    ),
]

HEADER = """# Modelos — licenças e origem

Gerado por `tools/fetch_models.py`. `aipdm doctor` confere os SHA-256 abaixo.
Os arquivos `.onnx` não são versionados no git.

| Arquivo | Licença | Origem | SHA-256 |
|---|---|---|---|
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    MODELS_DIR.mkdir(exist_ok=True)
    rows = []
    for name, license_id, url in MODELS:
        target = MODELS_DIR / name
        if not target.exists():
            print(f"baixando {name}...")
            tmp = target.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.rename(target)
        digest = sha256(target)
        print(f"{name}  {digest}")
        rows.append(f"| {name} | {license_id} | {url} | {digest} |")
    (MODELS_DIR / "LICENSES.md").write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
