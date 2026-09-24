Leia o `CLAUDE.md` e o `SPEC.md` inteiros.

Vamos construir o AI-PhotoDocsManager fase por fase. Agora, execute somente a **Fase 0 e a Fase 1** da seção 13.

Antes de escrever código:
1. Liste o que você entendeu dos requisitos não funcionais (seção 2) e como vai garantir cada um nesta fase.
2. Verifique e me informe a licença de cada dependência que pretende adicionar (inclusive PyMuPDF — se for AGPL, proponha `pypdfium2`).
3. Apresente o plano de arquivos e testes e aguarde minha aprovação.

Durante a implementação:
- Escreva os testes de `dates.py` primeiro, cobrindo todos os padrões da seção 6, incluindo datas inválidas.
- Implemente o teste de prova de somente-leitura (hash e mtime de todos os arquivos antes/depois da indexação).
- Crie fixtures pequenas geradas pelo próprio teste (imagens com Pillow, PDF e DOCX mínimos) — não versione arquivos pessoais.

Ao terminar:
- Rode lint, tipos e testes.
- Rode `uv run aipdm index "$AIPDM_TEST_DIR"` seguido de `uv run aipdm status` e me mostre o resumo (quantidade por tipo, por fonte de data, erros e tempo total).
- Rode a indexação de novo e confirme que 0 arquivos foram reprocessados.
- Faça os commits e pare. Não comece a Fase 2 sem meu pedido.
