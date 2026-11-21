<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright (C) 2026 Project Nox Contributors & Awerkori -->

# Nox Duplicate Guard — Documentação Técnica e Operacional

[← Voltar ao README principal](../README.md)

O **Nox Duplicate Guard** é o sistema híbrido do Project Nox para prevenir a abertura de tickets repetidos e gerenciar automaticamente solicitações duplicadas (novas, editadas ou históricas) na **Central de Pedidos** (`ProjectNox-org/project-nox-requests`), sem servidor permanente nem APIs pagas.

---

## 1. Arquitetura Escolhida (3 Níveis no Backend + Prevenção no Tracker)

| Camada | Gatilho / Onde executa | Frequência / Latência | Finalidade |
| :--- | :--- | :--- | :--- |
| **Camada A — Prevenção** | Tracker (`project-nox-tracker`) | Tempo real no navegador | Pesquisa rápida no modal **Abrir Solicitação** (`duplicate-guard.js`) antes de abrir o formulário do GitHub, incentivando voto 👍 em tickets existentes. |
| **Nível 1 — Evento Imediato** | `issues: [opened, edited, reopened]` | ~5–15s após o evento | Analisa apenas a Issue do evento (`--mode single`) contra todo o histórico paginado (abertas e fechadas). |
| **Nível 2 — Reconciliação Incremental** | `schedule: "12,42 * * * *"` (30 min) | A cada 30 min | Recupera eventos perdidos/editações e avalia Issues alteradas nos últimos 30 dias ou ainda sem entrada válida no cache (`.cache/nox-duplicate-guard/state.json`), podando registros antigos de Issues fechadas. Se nunca houve backfill ou se `RULE_ENGINE_VERSION` mudou, promove automaticamente para `backfill`. |
| **Nível 3 — Auditoria & Backfill Histórico** | `push` em `main` + `schedule: "17 4 * * *"` + `workflow_dispatch` (`mode: backfill`) | No merge para `main`, diário às 04:17 UTC e sob demanda | Varre **todas as Issues abertas independentemente da data** em ordem cronológica ascendente (`oldest → newest`), colapsando cadeias no mesmo lote, processando em lotes (`batch_size: 50`) e pulando tickets inalterados já validados no cache. |

> [!NOTE]
> **Disponibilidade no GitHub Actions:** workflows por eventos (`issues`) e agendamentos (`schedule`) rodam sem servidor dedicado. Entretanto, em repositórios públicos do GitHub, workflows agendados (`cron`) são automaticamente suspensos se o repositório permanecer **60 dias consecutivos sem commits**. Além disso, qualquer falha operacional ou parcial no lote retorna código de saída `2` com alerta no *Job Summary* para notificação automática aos mantenedores.

### Por que 30 minutos + Cache Incremental em vez de Cron de 5 minutos?

Um cron de 5 minutos executaria 288 jobs/dia no GitHub Actions, consumindo minutos desnecessariamente e sofrendo atrasos/descartes de fila do scheduler gratuito. A combinação **evento imediato + reconciliação incremental a cada 30 min com cache por `(rule_version, updated_at, content_fingerprint, estado do canônico, catalog_digest)` + auditoria completa diária** mantém reação rápida (~segundos na abertura), recupera falhas temporárias em até 30 min e reduz chamadas redundantes da API para quase zero quando nada mudou. Ao atualizar `index.min.json`, a mudança em `catalog_digest` invalida entradas de cache de `Nova extensão` mesmo sem edição das Issues abertas.

---

## 2. Interpretação Semântica, Catálogo Oficial (`v1.5.0`) e Critérios de Detecção

O motor (`RULE_ENGINE_VERSION = "1.5.0"`) aplica ordem total determinística `(created_at, number)`: uma Issue mais antiga nunca é fechada em favor de uma mais nova, evitando fechamento mútuo em aberturas simultâneas. Pull Requests são ignorados.

- **Camada Semântica e Verificação de Catálogo (`ExtensionCatalog`):**
  - Analisa `### Idioma`, `### Observações`, descrições, funcionalidades solicitadas, tópicos de sintoma, obras no título ou corpo (`specific_work_hint`, `_title_subjects_diverge`), números explícitos de capítulos/episódios, endpoints completos (URLs absolutas, caminhos relativos, método HTTP, parâmetros funcionais com redigimento automático de tokens/credenciais), sinais de regressão e evidências de que a extensão já existe, além de detectar contradições entre o dropdown `Parte afetada` e a descrição em texto livre.
  - Nome, domínio e tipo idênticos **não** encerram automaticamente quando há divergência material de intenção, idioma, subdomínio ou funcionalidade (ex.: `#141` criação inicial do HentaiHaven vs `#143` solicitação de suporte ao conteúdo em português na extensão existente).
  - Consulta `index.min.json` de `ProjectNox-org/manga-extensoes` e `ProjectNox-org/anime-extensoes` (com cache incremental de 6h para pré-seleção) e **revalida obrigatoriamente o catálogo oficial ao vivo antes de qualquer fechamento `already_fulfilled_creation`** (fail-closed diante de HTTP 404/erro, `RateLimitError`, pacote removido, múltiplas correspondências ambíguas, idioma diverge/parcial ou metadados incompletos).

1. **Nova extensão (`new_extension`):** nunca mistura `Mangá` e `Anime`; normaliza Unicode, espaços, typos leves (`Wacht` vs `Watch`), idiomas e URLs (`http`/`https`, `www.`, barras finais, typos reais de esquema e plataformas compartilhadas como `github.io`, `workers.dev`, `t.me`), preservando distinção entre subdomínios significativos (`pt.` vs `en.`, `api.` vs `www.`, `v1.` vs `v2.`). Metadados incompletos no catálogo (`languages` vazio), idioma não reconhecido, pedido de `"Todos os idiomas"` contra catálogo parcial (ex.: apenas `en` e `es`) ou múltiplos pacotes distintos correspondentes **nunca** encerram automaticamente (`supports_languages` exige cobertura inequívoca). Domínios distintos, idiomas distintos, observações com escopo divergente ou tickets `Concluído` sem comprovação viva no catálogo oficial nunca fecham automaticamente.
2. **Extensão com problema (`bug`):** distingue cinco pilares com **evidências independentes (sem dupla contagem)** — (i) **duplicata do incidente atual em aberto** exige evidência técnica discriminante compartilhada **mais** contexto independente adicional (os tokens da assinatura como `pages`/`found` e os nomes da parte declarada como `leitor` são excluídos da contagem de contexto; relatos curtos sem contexto como *"No pages found."* isolado nunca fecham automaticamente), (ii) **isolamento de hostname e exceções genéricas** (`host:*` e `gexc:IOException`/`RuntimeException`/`Exception`/`HttpException` **nunca** comprovam duplicidade por si sós — `host:*` só contribui em `nxdomain_dns` confirmado para o mesmo host), (iii) **escopo específico e bloqueio unilateral** (obras distintas inclusive apenas no título como `Naruto` vs `One Piece`, capítulos distintos `12` vs `18`, episódios `5` vs `9`, endpoints extraídos de URLs absolutas ou caminhos relativos como `https://api.../api/search` vs `/api/details`, métodos HTTP `GET` vs `POST`, subdomínios de endpoint, parâmetros funcionais, ou assinatura técnica unilateral entre bug específico e texto vago **nunca** fecham automaticamente), (iv) **regressão após correção ou falha recorrente** (ticket anterior `Concluído`, correção intermediária, versão diferente ou intervalo > 30 dias sem continuidade comprovada) e (v) **erros genéricos ou textos curtos**.
3. **Mudança de domínio (`domain_change`):** separa a identidade geral (`identity_key`) da identidade exata de destino (`destination_key` com `exact_host`, porta não-padrão e caminho normalizado); destinos com subdomínios distintos (`pt.site.com` vs `en.site.com`, `api.site.com` vs `www.site.com`, `v1.site.com` vs `v2.site.com`, `www2.` vs `www.`, `m.` vs raiz) ou caminhos distintos (`/br` vs `/en`) são preservados abertos para revisão humana (`possible_duplicate`), enquanto equivalências seguras de `www.` e protocolo seguem funcionando.
4. **Fonte indisponível (`dead_source`):** exige mesma fonte, destino URL compatível, janela temporal (30 dias), mesma intenção e separa encerramento definitivo de instabilidade/manutenção temporária (evita classificar relatos como *"faz semanas que dá 403"* como encerramento definitivo sem evidência inequívoca).
5. **Sugestão de melhoria (`enhancement`):** isola por extensão relacionada (`#144` Blackout Comics vs `#151` Hanami Heaven), tópicos funcionais (`len >= 1` igual entre ambos) e idiomas, exigindo sobreposição textual substancial além de simples verbos genéricos.

---

## 3. Segurança de Comentários, Fechamento Transacional e Ciclo de Labels

- **Validação estrita de autoria e esclarecimentos em comentários:**
  - Apenas comentários de `github-actions[bot]` (`type: Bot`) com marcador `<!-- nox-duplicate-guard:v1 issue=<N> ... -->` são reconhecidos e editados pelo bot. Marcadores falsificados ou copiados por usuários comuns são ignorados.
  - Comandos de override (`/not-duplicate` ou `<!-- nox-duplicate-guard:ignore -->`) só são aceitos quando publicados por contas com `author_association` em `OWNER`, `MEMBER` ou `COLLABORATOR`, ou quando uma label controlada (`ignorar-duplicata` / `duplicate-guard:ignore`) está aplicada na Issue.
  - **Esclarecimentos do autor e mantenedores (regra assimétrica):** comentários verificáveis do **autor da Issue** ou de **mantenedores** (`OWNER`, `MEMBER`, `COLLABORATOR`) que neguem duplicidade ou adicionem parte afetada, sintoma, versão, idioma, obra, capítulo, episódio, endpoint ou objetivo divergente bloqueiam ou rebaixam um fechamento automático (`confirmed_duplicate` → `possible_duplicate`). Comentários de terceiros sem permissão e do próprio bot são ignorados, e comentários nunca elevam confiança para fechar uma Issue cujo corpo original não justificava o fechamento.
- **Proteção contra edição concorrente e limites inevitáveis da API REST do GitHub:**
  - Como operações REST do GitHub (ler Issue, ler catálogo ao vivo, comentar, rotular e fechar) não formam transação atômica com CAS no servidor, subsiste apenas a janela residual de milissegundos entre a última leitura HTTP e `close_issue`. O bot reduz ao máximo esse risco com revalidação ao vivo em três pontos: (a) antes de iniciar ações (recalcula decisão e verifica catálogo oficial ao vivo), (b) antes de aplicar a label `Duplicado`, e (c) barreira pré-fechamento imediatamente antes de `close_issue` (reconsultando Issue alvo, comentários, canônico e `index.min.json` ao vivo; se algo mudou concorrentemente ou falhou, reverte a label adicionada, rebaixa o comentário e aborta o fechamento com segurança).
- **Higiene histórica conservadora:**
  - Em execuções normais de `backfill` e `reconcile`, o bot processa apenas Issues **abertas** e **não** altera retroativamente decisões históricas humanas em Issues já fechadas (ex.: `#127` fechada com `state_reason=duplicate` e label `Concluído`, ou `#143` fechada com `completed` mantendo `Em análise`).
- **Sequência transacional do encerramento automático (`auto_closed`):**
  1. Reconsulta ao vivo a Issue alvo e o ticket original (canônico precisa estar `open` e não ser `Duplicado`, exceto na detecção de criação já atendida via catálogo oficial).
  2. **Publica/atualiza primeiro o comentário explicativo** com a justificativa detalhada, número (`#X`) e link completo do ticket original/catálogo. Se falhar, aborta imediatamente (`aborted_comment_failed`) sem rotular nem fechar.
  3. Revalida estado/fingerprint/comentários ao vivo e aplica a label oficial `Duplicado` (ou `Concluído` quando comprovada apenas pelo catálogo sem ticket anterior). Se falhar, rebaixa o comentário para informativo e aborta antes de fechar.
  4. Executa barreira pré-fechamento imediatamente antes de fechar a Issue com `state_reason: "duplicate"` (fallback automático para `"not_planned"` em HTTP 422). Se ocorrer edição/comentário/mudança concorrente ou falha HTTP no fechamento, **reverte a label aplicada** e ajusta o comentário.
  5. **Somente após confirmar o fechamento**, reconsulta a Issue ao vivo e remove `Em análise`, `Aguardando informações` e equivalentes legados (evitando corrida com `label-issues.yml`), preservando categoria (`Nova extensão`, `Problema na extensão` etc.), tipo (`Anime`/`Mangá`) e `Conteúdo +18`.
- **Preservação de intervenções humanas:** nunca fecha nem remove `Aceito`, `Em desenvolvimento`, `Concluído`, `Recusado` ou `Cancelado`; nunca re-fecha Issues reabertas por humanos (`state_reason == "reopened"` ou reabertura após `auto_closed`); nunca envia solicitação genérica para votar 👍 em tickets já encerrados quando o objetivo pode ser distinto.

---

## 4. Limpeza Retroativa (`backfill`), Precedência e Operação

### Precedência de configuração

1. Base: [`.github/duplicate-guard.json`](../.github/duplicate-guard.json) (`enabled: true`, `auto_close_enabled: true`, `dry_run: false`, `batch_size: 50`).
2. Variáveis de repositório (*Settings → Actions → Variables*): `NOX_DUPLICATE_GUARD_ENABLED`, `NOX_DUPLICATE_GUARD_AUTO_CLOSE`, `NOX_DUPLICATE_GUARD_DRY_RUN`.
3. Parâmetros manuais de `workflow_dispatch` ou flags CLI (`--auto-close`, `--dry-run`/`--no-dry-run`, `--batch-size`).

### Executar simulação e limpeza retroativa manualmente

1. **Simulação completa (`dry-run`):** no GitHub Actions, execute **Nox Duplicate Guard → Run workflow** com `mode=backfill`, `dry_run=true`, `auto_close=config`. O *Job Summary* listará exatamente quais Issues antigas e novas seriam fechadas e quais ficariam abertas como `possible_duplicate`.
2. **Limpeza real em lote (`backfill`):** após revisar o relatório da simulação, execute `mode=backfill`, `dry_run=false`, `auto_close=true`, `batch_size=50`.
3. **Execução offline/local segura:**

```bash
# Validar contra fixtures locais
python3 -m scripts.duplicate_guard.cli \
  --mode backfill \
  --fixtures-file tests/fixtures/real_issues_snapshot.json

# Simular contra o repositório real (somente leitura)
GH_TOKEN="$(gh auth token)" python3 -m scripts.duplicate_guard.cli \
  --mode backfill \
  --dry-run \
  --repo ProjectNox-org/project-nox-requests
```

### Desativação rápida (Kill-switch)

- `NOX_DUPLICATE_GUARD_ENABLED=false` — desativa o bot inteiro.
- `NOX_DUPLICATE_GUARD_AUTO_CLOSE=false` — mantém detecção e comentários informativos sem fechar nenhuma Issue automaticamente.

### Testes e métricas

```bash
python3 -m pip install pytest pyyaml
python3 -m pytest -v -s tests/
```

---

## 5. Interação com Workflows Existentes

- `.github/workflows/duplicate-guard.yml`, `.github/workflows/ci-tests.yml`, `.github/workflows/label-issues.yml`, `.github/workflows/close-issue.yml` e `.github/workflows/issue-closed-guard.yml` usam actions compatíveis com Node 24 (`actions/checkout@v7`, `actions/setup-python@v7`, `actions/cache@v6`, `actions/github-script@v9`).
- `.github/workflows/label-issues.yml`: normaliza títulos/labels, ignora PRs e reconsulta `state`/`labels` ao vivo antes de aplicar `Em análise`.
- `.github/workflows/close-issue.yml` e `.github/workflows/issue-closed-guard.yml`: operam normalmente com a label oficial `Duplicado`.

---

## ⚖️ Licença e Copyright

Licenciado sob **GNU Affero General Public License v3.0 (`AGPL-3.0-only`)**. Consulte [`LICENSE`](../LICENSE) e [`NOTICE`](../NOTICE).
