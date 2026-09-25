"""Dev only: download the ONNX models and (re)write models/LICENSES.md with their SHA-256.

CLIP is not downloaded: it is produced by tools/export_clip.py (needs torch).
Usage: uv run python tools/fetch_models.py
"""

import hashlib
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
RAPIDOCR = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5"
CLIP_SOURCE = (
    "https://huggingface.co/laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B-s13B-b90k"
    " (tools/export_clip.py)"
)

# (file, license, source URL, expected SHA-256 or None, downloadable)
MODELS = [
    (
        "face_detection_yunet_2026may.onnx",
        "MIT",
        f"{ZOO}/face_detection_yunet/face_detection_yunet_2026may.onnx",
        None,
        True,
    ),
    (
        "face_recognition_sface_2021dec.onnx",
        "Apache-2.0",
        f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        None,
        True,
    ),
    (
        "ch_PP-OCRv5_det_mobile.onnx",
        "Apache-2.0",
        f"{RAPIDOCR}/det/ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
        True,
    ),
    (
        "latin_PP-OCRv5_rec_mobile.onnx",
        "Apache-2.0",
        f"{RAPIDOCR}/rec/latin_PP-OCRv5_rec_mobile.onnx",
        "b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a",
        True,
    ),
    ("clip_image.onnx", "MIT", CLIP_SOURCE, None, False),
    ("clip_text.onnx", "MIT", CLIP_SOURCE + ", int8", None, False),
    (
        "clip_tokenizer.json",
        "MIT",
        "https://huggingface.co/FacebookAI/xlm-roberta-base",
        None,
        False,
    ),
]

HEADER = """# Modelos — licenças e origem

Gerado por `tools/fetch_models.py`. `aipdm doctor` confere os SHA-256 abaixo.
Os arquivos de modelo não são versionados no git.

| Arquivo | Licença | Origem | SHA-256 |
|---|---|---|---|
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    MODELS_DIR.mkdir(exist_ok=True)
    rows = []
    for name, license_id, url, expected, downloadable in MODELS:
        target = MODELS_DIR / name
        if not target.exists():
            if not downloadable:
                raise SystemExit(f"{name} ausente — rode: uv run python tools/export_clip.py")
            print(f"baixando {name}...")
            tmp = target.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.rename(target)
        digest = sha256(target)
        if expected and digest != expected:
            raise SystemExit(f"{name}: SHA-256 {digest} difere do esperado {expected}")
        print(f"{name}  {digest}")
        rows.append(f"| {name} | {license_id} | {url} | {digest} |")
    (MODELS_DIR / "LICENSES.md").write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
