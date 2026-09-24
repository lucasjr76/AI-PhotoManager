# CLAUDE.md — Projeto AI-PhotoDocsManager

A especificação completa está em `SPEC.md`. Leia antes de qualquer tarefa. Em caso de conflito entre um pedido e a SPEC, pergunte.

## Regras invioláveis
- A pasta indexada é **somente leitura**. Nunca escreva, mova, renomeie ou altere nada nela (exceto `--portable`, explícito).
- **Sem rede em runtime** e **sem PyTorch em runtime**. Torch só em `tools/`, como dependência do grupo `dev`.
- Só use modelos e bibliotecas com licença que permita redistribuição comercial. Registre cada modelo em `models/LICENSES.md`. Na dúvida sobre uma licença, pare e pergunte.
- Nunca rode comandos contra a pasta de teste real com flags que possam gravar nela. Use `AIPDM_TEST_DIR` só para leitura.

## Ambiente
- Python 3.12 com `uv`. Rodar tudo via `uv run ...`.
- Adicionar dependência: `uv add <pkg>` — **peça confirmação antes**, informando licença e tamanho.
- Pasta de teste real: variável de ambiente `AIPDM_TEST_DIR`. Testes que dependem dela usam `@pytest.mark.real` e são pulados se a variável não existir.
- Workstation de dev tem GPU NVIDIA, mas o alvo é CPU. Não assuma CUDA. `onnxruntime-gpu` pode entrar como extra opcional de dev para acelerar benchmarks, nunca como padrão.

## Fluxo de trabalho
- Trabalhe **uma fase por vez** (SPEC seção 13). Comece cada fase em plan mode, apresente o plano e aguarde aprovação.
- Ao fim de cada fase: `uv run ruff check . && uv run ruff format --check . && uv run mypy src/aipdm/core && uv run pytest`, depois mostre como validar manualmente os critérios de aceite.
- Commits pequenos, mensagem em português no imperativo (`adiciona extração de data do WhatsApp`). Um commit por etapa lógica. Não faça push.
- Ao encontrar algo que a SPEC não cobre ou que se mostrou errado na prática (ex.: limiar, nome de pacote), proponha a alteração na SPEC em vez de decidir sozinho em silêncio.

## Estilo de código
- Type hints em tudo; `dataclasses` para estruturas; sem variáveis globais mutáveis.
- Funções puras em `core/` sempre que possível; I/O e banco isolados em `db.py`/`scanner.py`.
- Logs com `logging` (nunca `print` fora do `cli.py`). Nunca logue conteúdo de OCR ou embeddings, apenas IDs e caminhos relativos.
- Strings de interface em pt-BR; identificadores e comentários de código em inglês.
- SQL sempre parametrizado.
