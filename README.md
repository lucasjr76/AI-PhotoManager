# AI-PhotoDocsManager

A desktop app (Linux and Windows) that indexes a folder of photos and documents — for
example, a WhatsApp backup — and lets you find anything **by person (face), date, place,
object/scene and text**, **100% offline**.

> The app's user interface is in **Brazilian Portuguese**.

## Features

- **People:** detects and groups faces; you name the groups and the app recognizes new
  photos (with an **AI** badge and an "Is this person?" queue for uncertain matches). Faces
  the detector missed can be marked by hand.
- **Search:** free text ("beach", "cake", "red car" — Portuguese or English), combined with
  people, date range, place and file type. Searching a person's name returns their photos
  and the documents that mention them.
- **Text:** reads PDF, Word (DOCX) and the text inside screenshots and photos (OCR with
  Portuguese accents).
- **Dates and places:** date from WhatsApp/camera file names or EXIF; city from the photo's
  GPS, without internet access.
- **Browsing:** by year, person and place; a built-in viewer with zoom and photo details,
  HEIC included.
- **Monitoring:** while the app is open, new photos in the folder are indexed automatically.

## Privacy

- The indexed folder is **read-only**: nothing in it is ever written, moved or changed.
- **No network:** all AI models ship with the app; nothing is sent anywhere.
- The index lives in `~/.local/share/ai-photodocsmanager/` (Linux) or
  `%LOCALAPPDATA%\AI-PhotoDocsManager\` (Windows).

## Installation

- **Windows 10/11:** run `AI-PhotoDocsManager-<version>-windows-setup.exe` (no administrator
  rights needed). Startup log: `%LOCALAPPDATA%\AI-PhotoDocsManager\aipdm.log`.
- **Linux (Ubuntu 22.04+, Zorin 17+):** make the `.AppImage` executable and run it. The window
  uses the system's WebKitGTK (`sudo apt install gir1.2-webkit2-4.1`); without it the app
  opens in your default browser.

## How it works

Everything runs locally with ONNX Runtime on the CPU — no PyTorch at runtime.

| Task | Model / library |
|---|---|
| Face detection and recognition | YuNet + SFace (OpenCV Zoo) |
| Scene search (multilingual) | CLIP `xlm-roberta-base-ViT-B-32` (LAION), exported to ONNX |
| OCR | RapidOCR with PP-OCRv5 Latin recognition |
| PDF / DOCX | pypdfium2, python-docx |
| Places | GeoNames `cities500` (nearest town to the GPS point) |
| Storage and text search | SQLite with FTS5 |
| Interface | FastAPI on `127.0.0.1` with a session token, shown with pywebview |

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
uv run python tools/fetch_models.py   # face, OCR and places models
uv run python tools/export_clip.py    # multilingual CLIP → ONNX (uses torch, dev only)
uv run pytest
uv run aipdm ui                       # interface
uv run aipdm --help                   # command line
```

Packages are built by GitHub Actions (`.github/workflows/build.yml`): tests on Linux and
Windows, then an AppImage and a Windows installer. The full (Portuguese) specification is
in [`SPEC.md`](SPEC.md).

## License

Copyright © 2026 Luiz Carlos da Silveira Junior.

This program is free software: you can redistribute it and/or modify it under the terms of
the **GNU General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version**. It is distributed in the
hope that it will be useful, but **without any warranty**. See [`LICENSE`](LICENSE).

Third-party components (libraries, models and the GeoNames places data, CC BY 4.0) keep
their own licenses, listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and
[`models/LICENSES.md`](models/LICENSES.md).
