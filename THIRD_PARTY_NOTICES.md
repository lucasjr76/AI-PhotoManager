# Avisos de terceiros

O AI-PhotoDocsManager inclui os componentes abaixo, cada um sob sua própria licença.

## Bibliotecas

| Pacote | Versão | Licença |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| antlr4-python3-runtime | 4.9.3 | BSD |
| anyio | 4.15.1 | MIT |
| bottle | 0.13.4 | MIT License |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) |
| charset-normalizer | 3.5.1 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| colorlog | 6.12.0 | MIT License |
| fastapi | 0.141.1 | MIT |
| filelock | 4.0.3 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| fsspec | 2026.9.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT License |
| hf-xet | 1.6.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| huggingface-hub | 1.33.0 | Apache Software License |
| idna | 3.20 | BSD-3-Clause |
| lxml | 6.1.3 | BSD-3-Clause |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| omegaconf | 2.3.1 | BSD License |
| onnxruntime | 1.30.0 | MIT License |
| opencv-python-headless | 5.0.0.93 | Apache Software License |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 12.3.0 | MIT-CMU |
| pillow-heif | 1.8.0 | BSD-3-Clause |
| protobuf | 7.36.2 | 3-Clause BSD License |
| proxy-tools | 0.1.0 | MIT License |
| pyclipper | 1.4.0 | OSI Approved, MIT License |
| pydantic | 2.13.5 | MIT |
| pydantic-core | 2.46.5 | MIT |
| pypdfium2 | 5.13.0 | BSD-3-Clause, Apache-2.0, dependency licenses |
| python-docx | 1.2.0 | MIT License |
| pywebview | 6.2.1 | BSD License |
| pyyaml | 6.0.3 | MIT License |
| rapidocr | 3.9.2 | Apache-2.0 |
| requests | 2.34.2 | Apache Software License |
| shapely | 2.1.2 | BSD License |
| six | 1.17.0 | MIT License |
| starlette | 1.7.0 | BSD-3-Clause |
| tokenizers | 0.23.2 | Apache Software License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| xxhash | 4.0.1 | BSD-2-Clause |

**pillow-heif:** os binários incluem libheif/libde265 (LGPL-3.0) e x265 (GPL-2.0); o código-fonte está em https://github.com/bigcat88/pillow_heif.

## Modelos e dados

Gerado por `tools/fetch_models.py`. `aipdm doctor` confere os SHA-256 abaixo.
Os arquivos de modelo não são versionados no git.

| Arquivo | Licença | Origem | SHA-256 |
|---|---|---|---|
| face_detection_yunet_2026may.onnx | MIT | https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2026may.onnx | ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0 |
| face_recognition_sface_2021dec.onnx | Apache-2.0 | https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx | 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 |
| ch_PP-OCRv5_det_mobile.onnx | Apache-2.0 | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx | 4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae |
| latin_PP-OCRv5_rec_mobile.onnx | Apache-2.0 | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/latin_PP-OCRv5_rec_mobile.onnx | b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a |
| ch_ppocr_mobile_v2.0_cls_mobile.onnx | Apache-2.0 | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx | e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c |
| clip_image.onnx | MIT | https://huggingface.co/laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B-s13B-b90k (tools/export_clip.py) | 0503378424987d488423549fb975941076e477dda4bc6318d1c83cf6417a4574 |
| clip_text.onnx | MIT | https://huggingface.co/laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B-s13B-b90k (tools/export_clip.py), int8 | f0f6d17c96c95c6053d9472947ae2a972992d42db93d4ae9acc5aa776a03a26c |
| clip_tokenizer.json | MIT | https://huggingface.co/FacebookAI/xlm-roberta-base | acbd420e2269cdc1ef45332d3d5c418be4aef6b8cb5a0b7ccae0893485307153 |
| places.tsv.gz | CC-BY-4.0 (GeoNames) | https://download.geonames.org/export/dump/cities500.zip | 45634aef65bab219f83e01cf129af4fee4e575c2c68a57cdd237760775b496a2 |

Dados de lugares: GeoNames (https://www.geonames.org), licença CC BY 4.0.
