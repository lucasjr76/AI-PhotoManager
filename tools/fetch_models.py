"""Dev only: download the ONNX models and (re)write models/LICENSES.md with their SHA-256.

CLIP is not downloaded: it is produced by tools/export_clip.py (needs torch).
Usage: uv run python tools/fetch_models.py
"""

import gzip
import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
RAPIDOCR = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5"
GEONAMES = "https://download.geonames.org/export/dump"
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
    (
        # RapidOCR builds its orientation classifier even when disabled: give it a local
        # file, or it downloads one at runtime (RNF-2).
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "Apache-2.0",
        RAPIDOCR.replace("PP-OCRv5", "PP-OCRv4") + "/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
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
    ("places.tsv.gz", "CC-BY-4.0 (GeoNames)", f"{GEONAMES}/cities500.zip", None, False),
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


def build_places(target: Path) -> None:
    """GeoNames cities500 -> gzip TSV: city, state, country, lat, lon (CC-BY 4.0)."""

    def text(name: str) -> list[str]:
        raw = urllib.request.urlopen(f"{GEONAMES}/{name}", timeout=120).read()
        return raw.decode("utf-8").splitlines()

    countries = {
        cols[0]: cols[4]
        for line in text("countryInfo.txt")
        if line and not line.startswith("#")
        for cols in [line.split("\t")]
    }
    states = {c[0]: c[1] for line in text("admin1CodesASCII.txt") for c in [line.split("\t")]}
    archive = urllib.request.urlopen(f"{GEONAMES}/cities500.zip", timeout=300).read()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf, zf.open("cities500.txt") as fh:
        rows = []
        for line in io.TextIOWrapper(fh, encoding="utf-8"):
            cols = line.rstrip("\n").split("\t")
            name, lat, lon, country, admin1 = cols[1], cols[4], cols[5], cols[8], cols[10]
            state = states.get(f"{country}.{admin1}", "")
            rows.append(f"{name}\t{state}\t{countries.get(country, country)}\t{lat}\t{lon}\n")
    rows.sort()  # deterministic output
    with gzip.GzipFile(target, "wb", mtime=0) as out:
        out.write("".join(rows).encode("utf-8"))
    print(f"places.tsv.gz: {len(rows)} lugares")


def main() -> None:
    MODELS_DIR.mkdir(exist_ok=True)
    if not (MODELS_DIR / "places.tsv.gz").exists():
        build_places(MODELS_DIR / "places.tsv.gz")
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
