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

Um cron de 5 minutos executaria 288 jobs/dia no GitHub Actions, consumindo minutos desnecessariamente e sofrendo atrasos/descartes de fila do scheduler gratuito. A combinação **evento imediato + reconciliação incremental a cada 30 min com cache por `(rule_version, updated_at, content_fingerprint, estado do canônico)` + auditoria completa diária** mantém reação rápida (~segundos na abertura), recupera falhas temporárias em até 30 min e reduz chamadas redundantes da API para quase zero quando nada mudou.

---

## 2. Critérios de Detecção por Categoria

O motor aplica ordem total determinística `(created_at, number)`: uma Issue mais antiga nunca é fechada em favor de uma mais nova, evitando fechamento mútuo em aberturas simultâneas. Pull Requests são ignorados.

1. **Nova extensão (`new_extension`):** nunca mistura `Mangá` e `Anime`; normaliza Unicode, espaços, typos leves (`Wacht` vs `Watch`) e URLs (`http`/`https`, `www.`, barras finais, typos reais de esquema e plataformas compartilhadas como `github.io`, `workers.dev`, `t.me`). Domínios distintos nunca fecham automaticamente.
2. **Extensão com problema (`bug`):** exige mesma extensão e tipo compatível; nunca fecha apenas pelo nome da extensão. Rejeita códigos HTTP contraditórios (`403` vs `404`), partes afetadas incompatíveis (`Login` vs `Leitor`), descrições insuficientes (`< 15` chars sem erro técnico) e regressões contra tickets `Concluído` de versão anterior ou fechados há ≥ 3 dias.
3. **Mudança de domínio (`domain_change`):** compara extensão, tipo, `URL antiga` e `URL nova`; se a `URL nova` diferir (ex.: `#130` vs `#153`), retorna `no_match`.
4. **Fonte indisponível (`dead_source`):** exige mesma fonte, janela temporal (30 dias) e separa encerramento definitivo de instabilidade/manutenção temporária.
5. **Sugestão de melhoria (`enhancement`):** isola por extensão relacionada (`#144` Blackout Comics vs `#151` Hanami Heaven) e prioriza revisão humana (`possible_duplicate`).

---

## 3. Segurança de Comentários, Fechamento Transacional e Proteções

- **Validação estrita de autoria:**
  - Apenas comentários de `github-actions[bot]` (`type: Bot`) com marcador `<!-- nox-duplicate-guard:v1 issue=<N> ... -->` são reconhecidos e editados pelo bot. Marcadores falsificados ou copiados por usuários comuns são ignorados.
  - Comandos de override (`/not-duplicate` ou `<!-- nox-duplicate-guard:ignore -->`) só são aceitos quando publicados por contas com `author_association` em `OWNER`, `MEMBER` ou `COLLABORATOR`, ou quando uma label controlada (`ignorar-duplicata` / `duplicate-guard:ignore`) está aplicada na Issue.
- **Sequência transacional do encerramento automático (`auto_closed`):**
  1. Reconsulta ao vivo a Issue alvo e o ticket original (canônico precisa estar `open` e não ser `Duplicado`).
  2. **Publica/atualiza primeiro o comentário explicativo** com a justificativa e link para o original. Se falhar, aborta imediatamente (`aborted_comment_failed`) sem rotular nem fechar.
  3. Aplica a label oficial `Duplicado`. Se falhar, rebaixa o comentário para `commented` e aborta antes de fechar.
  4. Fecha a Issue com `state_reason: "duplicate"` (fallback automático para `"not_planned"` em HTTP 422). Se o fechamento falhar, **reverte a label `Duplicado`** e ajusta o comentário, evitando Issues abertas com label de resolução inconsistente.
  5. Remove labels iniciais (`Em análise`, `Aguardando informações`) apenas após confirmação do fechamento.
- **Preservação de intervenções humanas:** nunca fecha nem remove `Aceito`, `Em desenvolvimento`, `Concluído`, `Recusado` ou `Cancelado`; nunca re-fecha Issues reabertas por humanos (`state_reason == "reopened"` ou reabertura após `auto_closed`).

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

- `.github/workflows/label-issues.yml`: normaliza títulos/labels, ignora PRs e reconsulta `state`/`labels` ao vivo antes de aplicar `Em análise`.
- `.github/workflows/close-issue.yml` e `.github/workflows/issue-closed-guard.yml`: operam normalmente com a label oficial `Duplicado`.

---

## ⚖️ Licença e Copyright

Licenciado sob **GNU Affero General Public License v3.0 (`AGPL-3.0-only`)**. Consulte [`LICENSE`](../LICENSE) e [`NOTICE`](../NOTICE).
