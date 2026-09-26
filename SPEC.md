# AI-PhotoDocsManager — Especificação Técnica

> App desktop (Linux/Windows) que indexa uma pasta local de fotos e documentos
> e permite busca por **pessoa (rosto)**, **data**, **objeto/cena** e **texto (OCR)**, 100% offline.

## 1. Objetivo

O usuário baixa o app, aponta uma pasta (ex.: backup descriptografado do WhatsApp) e em minutos consegue:

1. Ver os rostos encontrados agrupados por pessoa e dar nome a cada grupo.
2. Ser perguntado sobre rostos novos ("É a Maria?" / "Quem é esta pessoa?").
3. Buscar combinando: pessoa(s) + intervalo de datas + texto livre ("bolo", "praia", "carro vermelho").
4. Buscar um nome e receber também os documentos (PDF, DOCX, prints) que contêm esse nome no texto.

## 2. Requisitos não funcionais (invioláveis)

| # | Requisito |
|---|-----------|
| RNF-1 | A pasta de origem é **somente leitura**. Nenhuma escrita, renomeação, remoção ou alteração de metadados nela. |
| RNF-2 | **Zero rede em runtime.** Modelos embutidos no pacote. Nenhuma telemetria, nenhum download automático. |
| RNF-3 | Runtime **sem PyTorch**. Toda inferência via `onnxruntime` (CPU por padrão). Torch só em scripts de dev (`tools/`) para exportar modelos. |
| RNF-4 | Roda em CPU comum. Meta: 1000 fotos indexadas em < 10 min num notebook de 4 núcleos. |
| RNF-5 | Indexação **incremental e retomável**: interrompida, continua de onde parou; reabertura processa só o que mudou. |
| RNF-6 | Todas as licenças de modelos e bibliotecas devem permitir **redistribuição comercial**. Proibido: modelos InsightFace (buffalo_*), qualquer peso "non-commercial/research only". |
| RNF-7 | Interface em **português (pt-BR)**. |
| RNF-8 | Servidor HTTP interno escuta só em `127.0.0.1`, porta aleatória, com token de sessão exigido em toda requisição (cookie `HttpOnly; SameSite=Strict` definido por `/?t=<token>`, ou cabeçalho `X-Token`). O cabeçalho `Host` precisa ser `127.0.0.1:<porta>` (defesa contra DNS rebinding). |
| RNF-9 | Arquivos só são servidos à UI por **ID do banco**, nunca por caminho vindo do cliente (sem path traversal). |

## 3. Stack

- **Python 3.12**, gerenciado com **uv** (`pyproject.toml`, `uv.lock`).
- **UI:** FastAPI (backend local) + HTML/CSS/JS puro (sem build step de frontend) exibido em janela nativa via **pywebview**, que usa o motor web do sistema: WebKitGTK no Linux (backend GTK: `pywebview[gtk]` → PyGObject/pycairo, LGPL, compilados na instalação; exige `webkit2gtk-4.1` do sistema) e Edge WebView2 no Windows. Sem Qt (traria um Chromium de ~240 MB). No Linux o app define `WEBKIT_DISABLE_DMABUF_RENDERER=1` e `WEBKIT_DISABLE_COMPOSITING_MODE=1` (renderização por software): com o driver proprietário da NVIDIA o processo do WebKit caía (SIGSEGV em `libnvidia-gpucomp`, SIGABRT em `libEGL_nvidia` ao sair). O usuário pode sobrescrever as variáveis.
- **Banco:** SQLite (WAL) + **FTS5** para texto. Embeddings como BLOB `float32`; similaridade por força bruta com numpy (volumes até ~50k itens).
- **Imagens:** Pillow + `pillow-heif` (HEIC). Thumbnails em cache próprio.
  Atenção: os wheels do `pillow-heif` são **GPLv2** (incluem libx265); aceito pelo responsável do projeto em 2026-09-24 (alternativa LGPL `pi-heif` foi descontinuada).
- **Rostos:** OpenCV Zoo — detector **YuNet** (`face_detection_yunet_2026may.onnx`, MIT, entrada dinâmica; exige OpenCV 5 — `opencv-python-headless>=5`) via `cv2.FaceDetectorYN`; reconhecimento **SFace** (`face_recognition_sface_2021dec.onnx`, Apache-2.0) via `cv2.FaceRecognizerSF` (alignCrop + feature, 128-d).
- **Objeto/cena:** CLIP multilíngue LAION `xlm-roberta-base-ViT-B-32` / `laion5b_s13b_b90k` (MIT), exportado por `tools/export_clip.py` (open_clip, só dev) para `clip_image.onnx` (fp32) e `clip_text.onnx` (int8 dinâmico; cosseno ≥ 0,99 contra o torch). Tokenizador `xlm-roberta-base` via `tokenizers` (`Tokenizer.from_file`, sem rede), contexto 77, pad id 1. Descartado o par OpenAI + multilingual-v1 (model card da OpenAI restringe uso em produto).
- **OCR:** pacote `rapidocr` 3.x (Apache-2.0) com detector `ch_PP-OCRv5_det_mobile.onnx` e reconhecedor `latin_PP-OCRv5_rec_mobile.onnx` (dicionário embutido no ONNX), sem classificador de orientação. Modelos sempre passados por caminho local (`Det.model_path`/`Rec.model_path`) — sem isso o rapidocr baixa modelos na 1ª execução (viola RNF-2). O `opencv-python` exigido pelo rapidocr é substituído pelo headless via `[tool.uv] override-dependencies`.
- **PDF:** `pypdfium2` (BSD-3/Apache-2.0) — extrai camada de texto; páginas sem texto são renderizadas (200 dpi) e passam pelo OCR. PyMuPDF descartado (AGPL-3.0).
- **DOCX:** python-docx (parágrafos + tabelas).
- **Empacotamento:** PyInstaller → AppImage (Linux) e instalador Inno Setup (Windows). Build via GitHub Actions (matriz ubuntu/windows).
- **Qualidade:** ruff (lint+format), mypy (modo estrito no pacote `aipdm/core`), pytest.

## 4. Estrutura do repositório

```
ai-photodocsmanager/
├── pyproject.toml
├── CLAUDE.md
├── SPEC.md
├── models/                 # .onnx versionados via Git LFS (ou baixados por tools/fetch_models.py em dev)
│   └── LICENSES.md         # licença e origem de cada modelo
├── src/aipdm/
│   ├── core/
│   │   ├── scanner.py      # varredura, assinatura, fila de trabalho
│   │   ├── dates.py        # extração de data (nome WhatsApp → EXIF → mtime)
│   │   ├── faces.py        # detecção, embedding, clustering, atribuição
│   │   ├── clip.py         # embeddings de imagem e de texto
│   │   ├── ocr.py          # OCR de imagens e páginas
│   │   ├── documents.py    # PDF e DOCX
│   │   ├── images.py       # abertura de imagens, EXIF, thumbnails
│   │   ├── search.py       # busca combinada e ranqueamento
│   │   ├── db.py           # schema, migrações, acesso
│   │   ├── cluster.py      # DBSCAN cosseno em blocos (numpy puro)
│   │   └── paths.py        # diretório de dados por SO
│   ├── cli.py              # interface de linha de comando (fases 1-2)
│   ├── server/             # FastAPI (fase 3)
│   └── ui/                 # HTML/JS/CSS estáticos (fase 3)
├── tools/                  # scripts de dev: export ONNX, benchmark, calibração
└── tests/
```

## 5. Tipos de arquivo

| Tipo | Extensões | Processamento |
|------|-----------|---------------|
| Imagem | jpg, jpeg, png, webp, heic, heif, bmp | data, thumbnail, rostos, CLIP, OCR |
| PDF | pdf | data, texto (camada ou OCR), thumbnail da 1ª página |
| Word | docx | data, texto |
| Fora do MVP | mp4, opus, doc (binário), xlsx | apenas listados na tabela `files` com `kind='other'` (sem hash, sem processamento) |

Arquivos **sem extensão** (ex.: `WhatsApp Documents/Sent/DOC-...-WA0056`, `.Links/<hash>`) são classificados pelo cabeçalho: `%PDF-` → pdf; JPEG/PNG/BMP/WEBP/HEIF → image; ZIP com `word/document.xml` → docx; demais → other. Se a regra de classificação mudar, a próxima varredura reclassifica e reprocessa as linhas afetadas.

Figurinhas do WhatsApp (`STK-*.webp`) são indexadas com `is_sticker=1` e ficam **fora** do clustering de rostos e da busca por padrão (filtro opcional).

## 6. Extração de data (ordem de prioridade)

1. Nome no padrão Android do WhatsApp: `^(IMG|VID|AUD|PTT|DOC|STK)-(\d{8})-WA\d+` → data (sem hora).
2. Nome no padrão exportação desktop: `WhatsApp (Image|Video) (\d{4}-\d{2}-\d{2}) at (\d{1,2}\.\d{2}\.\d{2})( ?[AP]M)?` → data e hora (AM/PM opcional, exportação do macOS).
3. Padrões comuns de câmera: `IMG_YYYYMMDD_HHMMSS`, `PXL_YYYYMMDD_HHMMSSmmm`, `YYYYMMDD_HHMMSS`, `Screenshot_YYYYMMDD-HHMMSS`.
4. EXIF `DateTimeOriginal` (o WhatsApp remove EXIF, mas fotos de outras origens têm).
5. Metadados do documento (PDF `creationDate`, DOCX `core_properties.created`).
6. `mtime` do arquivo.

Gravar `date_source` para depuração. Datas fora de 1990..hoje+1 dia são descartadas e passa-se à próxima fonte.

## 6.1 Localização

Estágio `gps` (imagens): latitude/longitude do bloco GPS do EXIF (JPEG, HEIC); inválidos e (0,0) são descartados. Pastas já indexadas recebem só esse estágio, lendo apenas o cabeçalho. Após a indexação, o processo principal nomeia o lugar pela cidade mais próxima da base GeoNames `cities500` (CC-BY 4.0, ~236 mil lugares, `models/places.tsv.gz` gerado por `tools/fetch_models.py`), até 60 km; mais longe fica sem lugar (string vazia, não é refeito). O rótulo "Cidade, Estado, País" entra no FTS (página 0), então a busca por texto encontra lugares. Países vêm em inglês (GeoNames). Sem mapa (tiles offline ficam para depois).

## 7. Schema SQLite (base; ajustar com migração versionada)

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);          -- root_path, model_versions, last_index_*
-- versão do schema: PRAGMA user_version (migrações em db.py)

CREATE TABLE files (
  id INTEGER PRIMARY KEY,
  rel_path TEXT NOT NULL UNIQUE,      -- relativo à raiz
  kind TEXT NOT NULL,                 -- image|pdf|docx|other
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  hash TEXT,                          -- xxh3_64, calculado só quando size/mtime mudam
  taken_at TEXT,                      -- ISO 8601
  date_source TEXT,
  width INTEGER, height INTEGER,
  is_sticker INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|done|error|missing
  error TEXT,
  stages_done TEXT DEFAULT ''         -- ex.: 'date,thumb,faces,clip,ocr'
);

CREATE TABLE people (
  id INTEGER PRIMARY KEY,
  name TEXT,                          -- NULL = grupo ainda sem nome
  hidden INTEGER DEFAULT 0,           -- "ignorar esta pessoa"
  cover_face_id INTEGER
);

CREATE TABLE faces (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  bbox TEXT NOT NULL,                 -- x,y,w,h em pixels da imagem original
  det_score REAL NOT NULL,
  embedding BLOB NOT NULL,            -- 128 x float32, normalizado L2
  person_id INTEGER REFERENCES people(id),
  assign_source TEXT,                 -- cluster|auto|user|suggested
  assign_score REAL
);

CREATE TABLE clip_embeddings (
  file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
  embedding BLOB NOT NULL
);

CREATE VIRTUAL TABLE texts USING fts5(
  content, file_id UNINDEXED, page UNINDEXED,
  tokenize = 'unicode61 remove_diacritics 2'   -- "joao" encontra "João"
);
```

Índices em `files(taken_at)`, `files(status)`, `faces(person_id)`, `faces(file_id)`.

Migrações: v1 (files, meta, texts), v2 (people, faces, face_negatives, clip_embeddings), v3 (`files.lat, lon, city, state, country` + índice por lugar).

## 8. Pipeline de indexação

1. **Scan:** percorre a raiz (segue symlinks: não; **pastas ocultas** — nome iniciado por `.`, ex. `.Links`, `.Statuses` — são ignoradas e, se já estiverem no banco, removidas dele), compara `size+mtime` com o banco; novos/alterados → `pending`; ausentes → `missing` (não apaga, permite reaparecer).
2. **Estágios por arquivo**, cada um registrado em `stages_done` para retomada: `date → thumb → faces → clip → ocr/text`.
3. Execução em pool de processos (`os.cpu_count() - 1`, no máximo 8 — cada worker ocupa ~0,5 GB de RAM), lotes para CLIP. Escrita no SQLite apenas pelo processo principal, a cada lote de 8 arquivos concluído (fila limitada a 2 lotes por worker; um arquivo lento não deixa os outros workers ociosos). Cada worker carrega os modelos uma vez (~0,5 GB de RAM por worker) e usa 1 thread por sessão de inferência.
   Estágios por tipo: imagem `date,thumb,faces,clip,ocr`; PDF `date,thumb,text,ocr`; DOCX `date,text`. Figurinhas pulam `faces` e `ocr`. `--only faces,clip,ocr` restringe os estágios pesados; bancos antigos recebem só os estágios que faltam.
4. Erros por arquivo não param a indexação (`status='error'`, mensagem gravada).
5. Progresso emitido como eventos (CLI: barra; UI: SSE ou polling).
6. Ctrl+C / fechar janela: termina o lote corrente e sai limpo.

Imagens são reduzidas para no máx. 1600 px no lado maior antes de rostos/OCR (bbox reescalado ao original). O detector de texto do RapidOCR faz o papel de detector leve: o reconhecimento só roda nas caixas detectadas (medido: ~390 ms/imagem com OCR em média na pasta real, é o estágio mais caro). PDFs: páginas sem camada de texto são renderizadas a 200 dpi e passam pelo OCR.

## 9. Rostos: agrupamento e atribuição

- Descartar detecções com `det_score < 0.8` ou rosto menor que 40 px (ambos configuráveis).
- **Primeira indexação:** DBSCAN (métrica cosseno, implementação própria em numpy calculando vizinhança em blocos de 2048 — nunca materializa N×N; paridade verificada contra `sklearn.cluster.DBSCAN` em teste) sobre todos os embeddings sem pessoa → cada cluster vira um `people` sem nome. Ruído fica sem pessoa ("rostos não agrupados").
- **Rostos novos depois que existem pessoas nomeadas:** comparar com os embeddings das faces confirmadas de cada pessoa (máx. ou média dos top-k):
  - `score ≥ T_auto` → atribui (`assign_source='auto'`)
  - `T_suggest ≤ score < T_auto` → `suggested`, entra na fila "É a Maria?"
  - abaixo → re-clustering apenas dos órfãos.
- A comparação com `T_auto` usa todas as pessoas (com ou sem nome), para rostos novos não fragmentarem grupos existentes; `suggested` só para pessoas com nome. Rostos `suggested` não contam como a pessoa na busca.
- Calibrado na pasta real (30 pessoas nomeadas, 3.527 rostos): `eps` do DBSCAN = 0.3 (similaridade ≥ 0.7; com 0.35 pessoas diferentes se fundem, ARI 0.98 → 0.68), `min_samples` = 3, `T_auto` = 0.7 (nenhum par de pessoas diferentes chegou a 0.687), `T_suggest` = 0.6 (7,7% dos rostos têm alguém de outra pessoa acima — aceitável para uma pergunta). A calibração usa semelhança entre pessoas *diferentes*: medir "mesma pessoa" nos grupos nomeados seria circular, pois eles vieram do próprio DBSCAN. Com `eps` 0.5 (≈ limiar 0.363 do OpenCV) 80% dos rostos encadearam num único grupo. `aipdm faces regroup` refaz os grupos sem nome com os limiares atuais (mantém nomes, decisões do usuário e negativas). `T_auto` e `T_suggest` **devem ser calibrados** com `tools/calibrate_faces.py` na pasta de teste real (relatar precisão/recall).
- Ações do usuário: nomear, renomear, mesclar pessoas, remover rosto de uma pessoa, marcar "não é esta pessoa" (gravar negativa para não sugerir de novo), ocultar pessoa.
- Ações do usuário (`assign_source='user'`) nunca são sobrescritas por processamento automático.

## 10. Busca

Entrada: `people_ids[]` (AND — todas na mesma foto), `date_from`, `date_to`, `text`, `kinds[]`.

1. Filtros SQL restringem o conjunto candidato (pessoas, datas, tipos).
2. Se `text` presente, dois sinais:
   - **CLIP:** similaridade entre embedding do texto e das imagens candidatas.
   - **FTS5:** `bm25` sobre OCR/texto de documentos.
3. Normalizar os dois scores para 0..1 e combinar (peso inicial 0.5/0.5, ajustável). CLIP abaixo de um limiar mínimo não entra (inicial: similaridade 0.2; calibrar — na pasta real a mediana é ~0.10 e até consultas sem sentido chegam a 0.26, então um limiar absoluto discrimina pouco).
4. Se o texto corresponder exatamente ao nome de uma pessoa cadastrada, tratar também como filtro de pessoa **e** buscar o nome no FTS (retorna fotos dela + documentos que a citam), apresentando em seções separadas.
5. Ordenação padrão: relevância; alternativa: data.

Meta: resposta < 300 ms para 20k arquivos.

## 11. CLI (fases 1-2)

```
aipdm index <pasta> [--db CAMINHO] [--workers N] [--only faces,clip,ocr] [--force]
aipdm status [--db ...]
aipdm people list | people name <id> "Nome" | people merge <id> <id>
aipdm faces export <person_id> <dir_saida>      # recortes para inspeção visual
aipdm search [--person "Nome"]... [--from AAAA-MM-DD] [--to AAAA-MM-DD] [--text "bolo"] [--limit 20]
aipdm doctor                                      # verifica modelos, versões, licenças
aipdm ui [pasta] [--browser]                      # interface (fase 3); --browser abre no navegador padrão
aipdm faces regroup                               # refaz grupos sem nome com os limiares atuais
```

**Monitoramento:** com a interface aberta, a pasta atual é verificada a cada 60 s só lendo (comparação de tamanho/mtime com o banco). A indexação incremental só começa quando duas verificações seguidas veem as mesmas mudanças (cópia/sincronização terminou). Pode ser desligado por pasta (`meta.monitor = 0`). Verificação periódica em vez de inotify/watchdog: sem dependência, funciona igual em Linux/Windows, pen drive e disco de rede.

**Re-scan:** rodar `aipdm index` de novo na mesma pasta re-varre e processa só o que mudou (novos, alterados, ausentes). `--force` reprocessa todos os arquivos. Na UI (fase 3), botão "Re-escanear pasta" com o mesmo comportamento.

`status` e `search` sem `--db` usam o banco indexado mais recentemente.

Diretório de dados: Linux `~/.local/share/ai-photodocsmanager/`, Windows `%LOCALAPPDATA%\AI-PhotoDocsManager\`. Um banco por pasta indexada (nome derivado do hash do caminho absoluto). Opção `--portable` grava em `<pasta>/.aipdm/` — única exceção ao RNF-1, e só quando o usuário escolhe explicitamente.

## 12. UI (fase 3)

1. **Boas-vindas:** escolher pasta (diálogo nativo do pywebview), lista de pastas recentes.
2. **Indexando:** progresso por estágio, busca já utilizável com o que estiver pronto.
3. **Pessoas:** grade de grupos (rosto de capa + contagem); clicar → nomear, ver fotos, mesclar, remover rostos errados. Fila "Rostos novos" e "É a Maria?" (sim / não / outra pessoa).
4. **Busca:** campo de texto + chips (pessoas com autocompletar, período, tipo). Resultados em grade de thumbnails; documentos em lista com trecho destacado (`snippet()` do FTS5). Duplo clique abre no visualizador padrão do SO.
5. Tema claro/escuro seguindo o sistema.

## 13. Fases e critérios de aceite

**Fase 0 — Esqueleto**
- `uv` + `pyproject`, ruff, mypy, pytest, estrutura de pastas, `aipdm doctor`.
- `tools/fetch_models.py` baixa YuNet e SFace, grava SHA-256 em `models/LICENSES.md`.
- ✅ `uv run pytest` passa; `uv run aipdm doctor` lista modelos e licenças.

**Fase 1 — Núcleo sem IA pesada**
- scanner incremental, datas, thumbnails, schema e migrações, PDF/DOCX texto + FTS5, CLI `index/status/search --text`.
- ✅ Testes unitários de `dates.py` cobrindo todos os padrões da seção 6.
- ✅ Reindexar a mesma pasta sem mudanças processa 0 arquivos.
- ✅ Prova de somente-leitura: teste que indexa uma pasta temporária e compara hash/mtime de todos os arquivos antes e depois.

**Fase 2 — Rostos, CLIP e OCR**
- faces + clustering + atribuição, CLIP (script de export ONNX em `tools/`), OCR latino, busca combinada.
- `tools/calibrate_faces.py` e `tools/benchmark.py`.
- ✅ Na pasta de teste real: relatório de clusters, contagem de rostos, tempo por estágio.
- ✅ `search --person X --text "praia"` e `search --text "Nome"` retornam resultados coerentes (validação manual pelo usuário).
- ✅ OCR reconhece corretamente acentos em uma imagem de teste em português.

**Fase 3 — Interface**
- FastAPI + pywebview + telas da seção 12.
- ✅ Teste de segurança: requisições sem token → 401; tentativa de acessar arquivo por caminho → 404.

**Fase 4 — Empacotamento**
- PyInstaller, AppImage, Inno Setup, GitHub Actions, modelos embutidos.
- ✅ Instala e roda em máquina limpa Windows 11 e Ubuntu/Zorin sem Python instalado, sem acesso à rede.

**Fase 5 — Refinamentos (backlog)**
- ~~monitoramento da pasta~~ (feito), ~~busca por local se houver GPS~~ (feito, sem mapa), cifragem do banco (SQLCipher), vídeos (frame-chave), exportar resultado para pasta, mapa offline.

## 14. Fora de escopo

Descriptografar backups `.crypt*` do WhatsApp; sincronização em nuvem; multiusuário; edição de fotos.
