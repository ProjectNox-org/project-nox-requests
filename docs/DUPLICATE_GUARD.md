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

## 2. Interpretação Semântica, Catálogo Oficial (`v1.6.0`) e Critérios de Detecção

O motor (`RULE_ENGINE_VERSION = "1.6.0"`) aplica ordem total determinística `(created_at, number)` e uma **política única e consistente de identidade específica de fonte e cobertura** em todos os caminhos (`verify_issue_fulfillment`, `compare_source_urls`, `_match_new_extension`, `_match_domain_change`, `_match_dead_source`, seleção de canônico e revalidação ao vivo). Pull Requests são ignorados.

- **Camada Semântica e Verificação de Catálogo por Fonte (`ExtensionCatalog` / `CatalogSource`):**
  - Analisa `### Idioma`, `### Observações`, descrições, funcionalidades solicitadas, tópicos de sintoma, obras no título ou corpo (`specific_work_hint`, `_title_subjects_diverge`, expressões como `"na série X"`, `"no título X"`, `"na história X"`, `"ao abrir X"`, `"exclusivo de X"`, múltiplas obras `"X e Y"`, escopos globais `"todas as obras"` e escopos indefinidos `"um capítulo específico"`), capítulos/episódios, endpoints completos e sinais de regressão.
  - Separa **identidade geral de descoberta (`identity_key`)** da **identidade específica da fonte (`source_identity_key`, com `exact_host`, porta, `scope_path` e query funcional)** e associa idiomas **por entrada `CatalogSource` em `index.min.json`** — um APK multi-fonte com `en.site` (`en`) + `pt.site` (`pt-BR`) nunca propaga português para a fonte `en.site`, e `free.site` nunca comprova cobertura para `vip.site`.
  - Consulta `index.min.json` de `ProjectNox-org/manga-extensoes` e `ProjectNox-org/anime-extensoes` (cache incremental de 6h com `catalog_digest` sensível por fonte) e **revalida obrigatoriamente com `verify_issue_fulfillment` ao vivo** antes de qualquer encerramento `already_fulfilled_creation` (fail-closed diante de remoção de fonte/idioma, subdomínio/caminho divergente, metadados contraditórios, múltiplos pacotes ou erro HTTP/rate limit).
  - Na seleção de ticket canônico em `already_fulfilled_creation`, exige pedido equivalente (`_is_equivalent_creation_request`: mesma fonte/URL, idioma compatível e escopo); quando não há ticket equivalente mas o catálogo comprova atendimento, aplica `Concluído` sem inventar referência falsa a ticket anterior.

1. **Nova extensão (`new_extension`):** nunca mistura `Mangá` e `Anime`; usa `compare_source_urls()` preservando diferenças entre subdomínios (`www2.` vs `www.`, `m.` vs raiz, `pt.` vs `en.`, `api.` vs raiz, `v1.` vs `v2.`), portas não padrão (`:8443` vs padrão), query strings funcionais e caminhos distintos (`/webtoons` vs `/novels`, `/free` vs `/vip`, `/pt` vs `/en`, `/manga` vs `/anime`), enquanto mantém equivalências seguras (`http`/`https`, `www.`, `:443`, `/home`, barra final e parâmetros voláteis).
2. **Extensão com problema (`bug`):** distingue cinco pilares com **evidências independentes (sem dupla contagem)** — (i) tokens da assinatura e parte declarada excluídos de `shared_indep`, (ii) isolamento de `host:*` e exceções genéricas, (iii) escopos específicos (`different_source_subdomains`, obras inclusive em expressões naturais e títulos, capítulos, episódios, endpoints e métodos HTTP com prioridade absoluta das contradições duras), (iv) regressão pós-correção e (v) relatos genéricos/curtos.
3. **Mudança de domínio (`domain_change`):** utiliza `compare_source_urls()` para `old_url` e `new_url` e bloqueia fechamento quando idiomas solicitados divergem.
4. **Fonte indisponível (`dead_source`):** utiliza `compare_source_urls()`, separa áreas específicas (`vip_paywall`), janela temporal (30 dias) e encerramento definitivo vs instabilidade temporária.
5. **Sugestão de melhoria (`enhancement`):** isola por extensão relacionada, tópicos funcionais, idiomas e distinção explícita entre `login` e `cadastro`.

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
