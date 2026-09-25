"""Dev only: export the LAION multilingual CLIP (open_clip) to ONNX.

Model: xlm-roberta-base-ViT-B-32 / laion5b_s13b_b90k (MIT).
Writes to models/: clip_image.onnx, clip_text.onnx (int8 unless --fp32-text),
clip_tokenizer.json. Checks ONNX outputs against torch before finishing.

Usage: uv run python tools/export_clip.py [--fp32-text]
"""

import argparse
from pathlib import Path

import numpy as np
import onnxruntime as ort
import open_clip
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
NAME, PRETRAINED = "xlm-roberta-base-ViT-B-32", "laion5b_s13b_b90k"
SAMPLE_TEXTS = ["uma foto de praia", "bolo de aniversário", "carro vermelho", "a dog"]
MIN_COSINE = 0.99


class ImageEncoder(torch.nn.Module):
    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, pixels: torch.Tensor) -> torch.Tensor:
        return self.model.encode_image(pixels)


class TextEncoder(torch.nn.Module):
    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        return self.model.encode_text(input_ids)


def cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / np.linalg.norm(a, axis=1, keepdims=True)
    b = b / np.linalg.norm(b, axis=1, keepdims=True)
    return (a * b).sum(axis=1)


def export(module: torch.nn.Module, sample: torch.Tensor, path: Path, input_name: str) -> None:
    torch.onnx.export(
        module,
        (sample,),
        str(path),
        input_names=[input_name],
        output_names=["embedding"],
        dynamic_axes={input_name: {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=17,
        dynamo=False,
        external_data=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fp32-text", action="store_true", help="não quantizar o encoder de texto")
    args = parser.parse_args()

    model, _, _preprocess = open_clip.create_model_and_transforms(NAME, pretrained=PRETRAINED)
    model.eval()
    torch.backends.mha.set_fastpath_enabled(False)  # fused MHA op has no ONNX export
    tokenizer = open_clip.get_tokenizer(NAME)
    MODELS_DIR.mkdir(exist_ok=True)

    tokenizer.tokenizer.backend_tokenizer.save(str(MODELS_DIR / "clip_tokenizer.json"))

    pixels = torch.randn(2, 3, 224, 224)
    ids = tokenizer(SAMPLE_TEXTS)
    with torch.no_grad():
        ref_image = model.encode_image(pixels).numpy()
        ref_text = model.encode_text(ids).numpy()

        image_path = MODELS_DIR / "clip_image.onnx"
        export(ImageEncoder(model), pixels, image_path, "pixels")
        text_path = MODELS_DIR / "clip_text.onnx"
        fp32_text = MODELS_DIR / "clip_text.fp32.onnx"
        export(TextEncoder(model), ids, fp32_text, "input_ids")

    if args.fp32_text:
        fp32_text.replace(text_path)
    else:
        quantize_dynamic(str(fp32_text), str(text_path), weight_type=QuantType.QInt8)
        fp32_text.unlink()

    image_out = ort.InferenceSession(str(image_path)).run(None, {"pixels": pixels.numpy()})[0]
    text_out = ort.InferenceSession(str(text_path)).run(None, {"input_ids": ids.numpy()})[0]
    image_cos, text_cos = cosine(ref_image, image_out).min(), cosine(ref_text, text_out).min()
    print(f"cosseno mínimo ONNX x torch: imagem {image_cos:.4f}, texto {text_cos:.4f}")
    for path in (image_path, text_path):
        print(f"{path.name}: {path.stat().st_size / 1024**2:.0f} MB")
    if min(image_cos, text_cos) < MIN_COSINE:
        raise SystemExit(f"divergência acima do tolerado (< {MIN_COSINE})")


if __name__ == "__main__":
    main()
