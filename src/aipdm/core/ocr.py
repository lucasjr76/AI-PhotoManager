"""OCR with RapidOCR (PP-OCRv5 det + latin rec), models loaded from local paths only.

Passing explicit model paths keeps RapidOCR from downloading anything (RNF-2).
The text detector runs first; recognition only runs on detected boxes, so images
without text only pay for detection.
"""

import logging
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from rapidocr import RapidOCR
from rapidocr.utils.output import RapidOCROutput

DET_FILE = "ch_PP-OCRv5_det_mobile.onnx"
REC_FILE = "latin_PP-OCRv5_rec_mobile.onnx"
CLS_FILE = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"  # unused, but RapidOCR always loads one
MIN_TEXT_SCORE = 0.5


class OcrModel:
    def __init__(self, models_dir: Path, threads: int) -> None:
        self.engine = RapidOCR(
            params={
                "Global.use_cls": False,
                "Global.text_score": MIN_TEXT_SCORE,
                "Global.log_level": "error",
                # Every model by explicit local path, and our folder as its root: RapidOCR
                # downloads any model it cannot find (would break RNF-2).
                "Global.model_root_dir": str(models_dir),
                "Det.model_path": str(models_dir / DET_FILE),
                "Cls.model_path": str(models_dir / CLS_FILE),
                "Rec.model_path": str(models_dir / REC_FILE),
                "EngineConfig.onnxruntime.intra_op_num_threads": threads,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)

    def read(self, rgb: NDArray[np.uint8]) -> str:
        """Recognized lines, top to bottom, joined with newlines ('' if none)."""
        result = self.engine(np.ascontiguousarray(rgb[:, :, ::-1]))  # RapidOCR expects BGR
        lines = result.txts if isinstance(result, RapidOCROutput) and result.txts else ()
        return "\n".join(line for line in lines if line.strip())
