# AI-PhotoDocsManager

Aplicativo de computador (Linux e Windows) que indexa uma pasta de fotos e documentos — por
exemplo, um backup do WhatsApp — e permite encontrar tudo **por pessoa (rosto), data, lugar,
objeto/cena e texto**, **100% offline**.

## O que faz

- **Pessoas:** detecta e agrupa rostos; você dá nome aos grupos e o app reconhece as fotos
  novas (com selo **IA** e fila "É esta pessoa?" para os casos em dúvida). Rostos que o
  detector não achou podem ser marcados à mão.
- **Busca:** texto livre ("praia", "bolo", "carro vermelho"), combinado com pessoas, período,
  lugar e tipo. Buscar um nome traz as fotos da pessoa e os documentos que a citam.
- **Texto:** lê PDF, Word (DOCX) e o texto de prints e fotos (OCR com acentos do português).
- **Datas e lugares:** data pelo nome do arquivo do WhatsApp/câmera ou EXIF; cidade pelo GPS
  da foto, sem internet.
- **Navegação:** por ano, por pessoa e por lugar; visualizador com zoom e dados da foto,
  inclusive HEIC.
- **Monitoramento:** com o app aberto, fotos novas na pasta são indexadas sozinhas.

## Privacidade

- A pasta indexada é **somente leitura**: nada é escrito, movido ou alterado nela.
- **Sem rede:** todos os modelos vêm dentro do app; nada é enviado para lugar nenhum.
- Os dados do índice ficam em `~/.local/share/ai-photodocsmanager/` (Linux) ou
  `%LOCALAPPDATA%\AI-PhotoDocsManager\` (Windows).

## Instalação

- **Windows 10/11:** execute `AI-PhotoDocsManager-<versão>-windows-setup.exe` (não pede
  administrador).
- **Linux (Ubuntu 22.04+, Zorin 17+):** torne o `.AppImage` executável e abra. A janela usa o
  WebKitGTK do sistema (`sudo apt install gir1.2-webkit2-4.1`); sem ele, o app abre no
  navegador.

## Desenvolvimento

Requer [uv](https://docs.astral.sh/uv/) e Python 3.12.

```bash
uv sync
uv run python tools/fetch_models.py   # modelos de rosto, OCR e lugares
uv run python tools/export_clip.py    # CLIP multilíngue → ONNX (usa torch, só em dev)
uv run pytest
uv run aipdm ui                       # interface
uv run aipdm --help                   # linha de comando
```

Especificação completa em [`SPEC.md`](SPEC.md).

## Licença

Copyright © 2026 Luiz Carlos da Silveira Junior.

Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob os termos da
**GNU General Public License, versão 3 ou (a seu critério) qualquer versão posterior**,
publicada pela Free Software Foundation. Ele é distribuído na esperança de ser útil, mas
**sem nenhuma garantia**. Veja [`LICENSE`](LICENSE).

Componentes de terceiros (bibliotecas, modelos e a base de lugares GeoNames, CC BY 4.0) mantêm
suas próprias licenças, listadas em [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) e
[`models/LICENSES.md`](models/LICENSES.md).
