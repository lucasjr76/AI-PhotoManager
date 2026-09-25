# Modelos — licenças e origem

Gerado por `tools/fetch_models.py`. `aipdm doctor` confere os SHA-256 abaixo.
Os arquivos de modelo não são versionados no git.

| Arquivo | Licença | Origem | SHA-256 |
|---|---|---|---|
| face_detection_yunet_2026may.onnx | MIT | https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2026may.onnx | ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0 |
| face_recognition_sface_2021dec.onnx | Apache-2.0 | https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx | 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 |
| ch_PP-OCRv5_det_mobile.onnx | Apache-2.0 | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx | 4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae |
| latin_PP-OCRv5_rec_mobile.onnx | Apache-2.0 | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/latin_PP-OCRv5_rec_mobile.onnx | b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a |
| clip_image.onnx | MIT | https://huggingface.co/laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B-s13B-b90k (tools/export_clip.py) | 0503378424987d488423549fb975941076e477dda4bc6318d1c83cf6417a4574 |
| clip_text.onnx | MIT | https://huggingface.co/laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B-s13B-b90k (tools/export_clip.py), int8 | f0f6d17c96c95c6053d9472947ae2a972992d42db93d4ae9acc5aa776a03a26c |
| clip_tokenizer.json | MIT | https://huggingface.co/FacebookAI/xlm-roberta-base | acbd420e2269cdc1ef45332d3d5c418be4aef6b8cb5a0b7ccae0893485307153 |
