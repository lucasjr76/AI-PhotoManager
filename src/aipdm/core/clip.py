"""Multilingual CLIP (LAION xlm-roberta-base-ViT-B-32) via onnxruntime.

Preprocessing mirrors open_clip's: shortest side to 224 (bicubic), center crop,
OpenAI mean/std. Tokenization mirrors its HFTokenizer: whitespace cleanup,
xlm-roberta-base, fixed length 77 padded with <pad> (id 1).
"""

from pathlib import Path

import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray
from PIL import Image
from tokenizers import Tokenizer

IMAGE_FILE = "clip_image.onnx"
TEXT_FILE = "clip_text.onnx"
TOKENIZER_FILE = "clip_tokenizer.json"
SIZE = 224
CONTEXT_LENGTH = 77
PAD_ID = 1
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
DIM = 512


def session(path: Path, threads: int) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def preprocess(img: Image.Image) -> NDArray[np.float32]:
    """RGB PIL image -> (3, 224, 224) float32."""
    w, h = img.size
    size = (SIZE, int(SIZE * h / w)) if w <= h else (int(SIZE * w / h), SIZE)
    resized = img.convert("RGB").resize(size, Image.Resampling.BICUBIC)
    left = round((size[0] - SIZE) / 2)
    top = round((size[1] - SIZE) / 2)
    crop = resized.crop((left, top, left + SIZE, top + SIZE))
    pixels = (np.asarray(crop, dtype=np.float32) / 255.0 - MEAN) / STD
    out: NDArray[np.float32] = pixels.transpose(2, 0, 1).astype(np.float32)
    return out


def normalize_rows(x: NDArray[np.float32]) -> NDArray[np.float32]:
    out: NDArray[np.float32] = (
        x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    ).astype(np.float32)
    return out


def load_tokenizer(models_dir: Path) -> Tokenizer:
    tokenizer = Tokenizer.from_file(str(models_dir / TOKENIZER_FILE))
    tokenizer.enable_truncation(CONTEXT_LENGTH)
    tokenizer.enable_padding(pad_id=PAD_ID, pad_token="<pad>", length=CONTEXT_LENGTH)
    return tokenizer


def tokenize(tokenizer: Tokenizer, texts: list[str]) -> NDArray[np.int64]:
    encoded = tokenizer.encode_batch([" ".join(t.split()) for t in texts])
    return np.array([e.ids for e in encoded], dtype=np.int64)


class ClipImageModel:
    def __init__(self, models_dir: Path, threads: int) -> None:
        self.session = session(models_dir / IMAGE_FILE, threads)

    def encode(self, pixels: NDArray[np.float32]) -> NDArray[np.float32]:
        """(N, 3, 224, 224) from `preprocess` -> (N, 512) L2-normalized."""
        (out,) = self.session.run(None, {"pixels": pixels})
        return normalize_rows(np.asarray(out, dtype=np.float32))


class ClipTextModel:
    def __init__(self, models_dir: Path, threads: int = 0) -> None:
        self.session = session(models_dir / TEXT_FILE, threads)
        self.tokenizer = load_tokenizer(models_dir)

    def encode(self, text: str) -> NDArray[np.float32]:
        (out,) = self.session.run(None, {"input_ids": tokenize(self.tokenizer, [text])})
        embedding: NDArray[np.float32] = normalize_rows(np.asarray(out, dtype=np.float32))[0]
        return embedding
