<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright (C) 2026 Project Nox Contributors & Awerkori -->

# Nox Duplicate Guard — Evidências de Auditoria e Correção (`v1.6.0` → `v1.7.0`)

Este documento consolida as evidências verificáveis da missão corretiva executada sobre a versão **v1.6.0** ([PR #165](https://github.com/ProjectNox-org/project-nox-requests/pull/165), commit `1491b262fd1c7d5ddb5547918045d0007e275494`), resultando na versão **v1.7.0** (`RULE_ENGINE_VERSION = "1.7.0"`).

> [!IMPORTANT]
> **Separação estrita entre testes simulados e validações em produção:**
> - Todas as reproduções de regressão, testes de concorrência (`InMemoryGitHubApi`), cálculos de `content_fingerprint` e métricas do benchmark rotulado foram executados **em ambiente simulado offline**, sem criar, editar ou encerrar nenhuma Issue pública real.
> - Todas as execuções listadas com IDs de workflow do GitHub Actions ocorreram **em produção real** sobre o repositório `ProjectNox-org/project-nox-requests`, preservando todas as Issues legítimas abertas.

---

## 1. Contenção Preventiva Ativada Antes das Mudanças

Antes do início das alterações de código da `v1.7.0`, o fechamento automático foi bloqueado preventivamente em produção por meio da variável oficial do repositório:

```bash
gh variable set NOX_DUPLICATE_GUARD_AUTO_CLOSE --body "false" --repo ProjectNox-org/project-nox-requests
NOX_DUPLICATE_GUARD_AUTO_CLOSE=false python3 -c "from scripts.duplicate_guard.config import load_config; c=load_config(); assert c.enabled and not c.auto_close_enabled and not c.dry_run, c; print('OK:', c)"
```

Saída observada:
- `NOX_DUPLICATE_GUARD_AUTO_CLOSE=false` registrado no repositório.
- `GuardConfig(enabled=True, auto_close_enabled=False, dry_run=False, ...)` confirmado programaticamente.

---

## 2. Problemas Investigados, Causas-Raiz e Reprodução Antes × Depois

### 2.1. Seção 1 — Falsos Positivos com Observações Substantivas Diferentes em `_match_new_extension()`

- **Problema identificado:** Dois pedidos de `Nova extensão` para a mesma fonte e idioma com `has_substantive_observations=True`, mas `feature_topics` iguais ou vazios (`frozenset()`), prosseguiam até `confirmed_duplicate` (`should_auto_close=True`), ignorando divergências funcionais nas observações. Além disso, observações declarativas sem verbo introdutório da lista anterior (ex.: `"Capítulos ordenados pela data de publicação."`) nem sempre eram detectadas como substantivas.
- **Causa-raiz:** Em [`scripts/duplicate_guard/matcher.py`](../scripts/duplicate_guard/matcher.py), `_match_new_extension()` comparava apenas `target.has_substantive_observations != cand.has_substantive_observations` e `feat_t != feat_c`. Quando ambos tinham `has_substantive_observations=True` e `feat_t == feat_c`, o algoritmo não comparava os requisitos efetivos do texto de observações. Em [`scripts/duplicate_guard/normalize.py`](../scripts/duplicate_guard/normalize.py), `has_substantive_observations_text()` não reconhecia expressões particípiais/nominais de ordenação, agrupamento e filtros sem verbo imperativo prévio.
- **Cenário obrigatório reproduzido (`Sushi Manga`, `https://sushimanga.com`, `Português`):**
  - Ticket A — Observação: `"Preciso que os capítulos sejam ordenados pela data de publicação."`
  - Ticket B — Observação: `"Preciso que os capítulos sejam agrupados pelo nome do autor."`
  - **Antes (`v1.6.0`, commit `1491b262fd1c7d5ddb5547918045d0007e275494` — simulado):** `confirmed_duplicate`, `should_auto_close=True`, `has_obs=(True, True)`, `feature_topics=(frozenset(), frozenset())`.
  - **Depois (`v1.7.0` — simulado):** `possible_duplicate`, `should_auto_close=False`, contradição `('scope_or_observation_divergence',)`.
- **Correção aplicada:**
  - Ampliação de `has_substantive_observations_text()` em [`scripts/duplicate_guard/normalize.py`](../scripts/duplicate_guard/normalize.py) para capturar requisitos com ordenação/agrupamento/filtragem/separação e restrições explícitas, mantendo elogios genéricos como não substantivos.
  - Adição de `_canonicalize_obs_token()`, `_extract_observation_requirement_tokens()` e `_substantive_observations_equivalent()` em [`scripts/duplicate_guard/matcher.py`](../scripts/duplicate_guard/matcher.py), exigindo compatibilidade de tópico, idioma e eixos funcionais + alta sobreposição de requisitos antes de permitir fechamento automático (e preservando `confirmed_duplicate` para paráfrases equivalentes ou elogios não funcionais).

### 2.2. Seção 2 — `content_fingerprint` Incompleto em `parse_issue()`

- **Problema identificado:** O `content_fingerprint` calculado em `parse_issue()` não incluía diretamente `notes` (`Observações`), `has_substantive_observations`, `feature_topics`, `symptom_topics`, `error_signatures`, `technical_tokens`, `secondary_intents`, `category_mismatch`, `extension_already_exists`, `has_regression_signal`, `dead_ev`, `extra_urls`, `app_name`, `summary`, `purpose`, flags (`login`, `vip`, `nsfw`), nem a capitalização de path/query nas chaves de URL.
- **Causa-raiz:** Em [`scripts/duplicate_guard/models.py`](../scripts/duplicate_guard/models.py) faltava o atributo dedicado `notes` em `ParsedIssue`, e em [`scripts/duplicate_guard/parser.py`](../scripts/duplicate_guard/parser.py) `fp_parts` omitia esses campos e utilizava apenas versões em minúsculas de chaves/textos selecionados.
- **Reprodução Antes × Depois (`v1.6.0` × `v1.7.0` — simulado):**
  - **Antes:** editar flags (`login`, `vip`, `nsfw`) ou determinadas alterações em `notes` poderia deixar de invalidar decisões antigas/cache em barreiras de concorrência.
  - **Depois:** alterações isoladas em `notes`, `login`, `vip`, `nsfw`, `/Manga` vs `/manga`, `?id=ABC123` vs `?id=abc123` alteram diretamente `content_fingerprint`; edições apenas de `updated_at` ou labels mantêm o fingerprint estável.
  - Em testes simulados de concorrência usando `_revalidate_live_decision()` e `_verify_concurrent_barrier()` ([`scripts/duplicate_guard/actions.py`](../scripts/duplicate_guard/actions.py)), edições em `notes` antes do comentário, entre comentário e label (`aborted_concurrent_edit_before_label`) e entre label `Duplicado` e `close_issue()` (`aborted_concurrent_edit_before_close`) são detectadas, revertem a label `Duplicado` via `remove_label()` e garantem que `close_issue()` nunca seja chamado.

### 2.3. Seção 3 — Capitalização de Caminhos e Parâmetros Funcionais em `normalize_url()`

- **Problema identificado:** `normalize_url()` e `_normalize_functional_query()` convertiam automaticamente `norm_path` e valores de query funcionais para minúsculas, colapsando recursos potencialmente distintos (`/Manga` vs `/manga`, `?id=ABC123` vs `?id=abc123`) para o mesmo `destination_key` e `source_identity_key`.
- **Causa-raiz:** `path = re.sub(...).strip("/").lower()` em `normalize_url()` e `v.lower()` em `_normalize_functional_query()` dentro de [`scripts/duplicate_guard/normalize.py`](../scripts/duplicate_guard/normalize.py).
- **Reprodução Antes × Depois (simulado):**
  - **Antes (`v1.6.0`):** `https://site.com/Manga` e `https://site.com/manga` produziam ambos `dest:site.com/manga`; `?id=ABC123` e `?id=abc123` produziam `id=abc123`.
  - **Depois (`v1.7.0`):**
    - `https://site.com/Manga` → `dest:site.com/Manga` | `https://site.com/manga` → `dest:site.com/manga` (`possible_duplicate`, `should_auto_close=False`, contradição `path_case_divergence`).
    - `https://site.com/watch?id=ABC123` → `functional_query="id=ABC123"` | `https://site.com/watch?id=abc123` → `functional_query="id=abc123"` (`possible_duplicate`, `should_auto_close=False`).
    - Scheme e host continuam insensíveis a maiúsculas/minúsculas (`HTTP://SUSHIMANGA.COM/web%74oons/` ≡ `https://www.sushimanga.com/webtoons`) e parâmetros voláteis/tracking (`utm_*`, `cb`, `_t`) seguem normalizados/ignorados.
  - **Atualização justificada de teste anterior:** Em [`tests/test_v160_audit_regressions.py`](../tests/test_v160_audit_regressions.py), o teste `test_c3_09` anteriormente misturava path com `W` maiúsculo (`/Web%74oons/` vs `/webtoons`) na validação de percent-encoding/case-folding; como paths passaram a ser sensíveis a maiúsculas/minúsculas conforme a Seção 3, o `test_c3_09` foi atualizado para testar equivalência de scheme/host em caixa alta + percent-encoding (`HTTP://SUSHIMANGA.COM/web%74oons/` vs `https://www.sushimanga.com/webtoons`), e casos explícitos de divergência de case em path foram cobertos na nova suíte `v1.7.0`.

### 2.4. Seção 4 — Versionamento e Invalidação do Cache

- **Alteração:** `RULE_ENGINE_VERSION` atualizado de `"1.6.0"` para `"1.7.0"` em [`scripts/duplicate_guard/models.py`](../scripts/duplicate_guard/models.py).
- **Efeito:** Na inicialização do CLI (`load_cache`), qualquer cache com `rule_version != "1.7.0"` invalida entradas incompatíveis, força reavaliação segura (`effective_mode == "backfill"` quando necessário), impede reutilização de decisões antigas e preserva cache hit integral (`skipped_cached_count`) em execuções subsequentes quando nada material mudou.

---

## 3. Arquivos Alterados na Missão Corretiva `v1.7.0`

| Arquivo | Resumo das alterações |
| :--- | :--- |
| [`scripts/duplicate_guard/models.py`](../scripts/duplicate_guard/models.py) | `RULE_ENGINE_VERSION = "1.7.0"`; adição de `notes: str = ""` em `ParsedIssue`. |
| [`scripts/duplicate_guard/normalize.py`](../scripts/duplicate_guard/normalize.py) | Preservação de case em path/query funcional em `_compute_scope_path()`, `_normalize_functional_query()`, `normalize_url()`, `compare_source_urls()` (`path_case_divergence`) e query values em `_canonicalize_endpoint()`; ampliação de `has_substantive_observations_text()`. |
| [`scripts/duplicate_guard/parser.py`](../scripts/duplicate_guard/parser.py) | Extração dedicada de `notes_raw`, preenchimento de `ParsedIssue.notes` e inclusão completa de campos materiais e URLs sensíveis ao caso em `fp_parts`. |
| [`scripts/duplicate_guard/matcher.py`](../scripts/duplicate_guard/matcher.py) | Implementação de `_canonicalize_obs_token()`, `_extract_observation_requirement_tokens()` e `_substantive_observations_equivalent()`, integrados em `_match_new_extension()`. |
| [`tests/test_v160_audit_regressions.py`](../tests/test_v160_audit_regressions.py) | Atualização justificada de `test_c3_09` para scheme/host em maiúsculas + `%74` após preservação de case em paths. |
| [`tests/test_v170_corrective_regressions.py`](../tests/test_v170_corrective_regressions.py) | Nova suíte com 14 testes cobrindo integralmente os 12 requisitos da Seção 5 e verificação direta de `_revalidate_live_decision()` e `_verify_concurrent_barrier()`. |
| [`tests/test_duplicate_guard_metrics.py`](../tests/test_duplicate_guard_metrics.py) | Expansão da matriz rotulada de 85 para 90 casos cobrindo observações divergentes/equivalentes, `/Manga` vs `/manga` e `?id=ABC123` vs `?id=abc123`. |
| [`docs/DUPLICATE_GUARD.md`](DUPLICATE_GUARD.md) | Atualização técnica para `v1.7.0`. |
| [`docs/AUDIT_EVIDENCE.md`](AUDIT_EVIDENCE.md) | Este relatório verificável de evidências. |

---

## 4. Testes Automatizados e Benchmark (Simulação Local `v1.7.0`)

Comandos executados após a última alteração de código:

```bash
python3 -m compileall -q scripts tests
pytest -q
python3 -m unittest discover -s tests -v
python3 -c "from tests.test_duplicate_guard_metrics import compute_metrics; m = compute_metrics(); print(m); assert m['auto_close']['fp'] == 0 and m['auto_close']['fn'] == 0"
```

Resultados reais observados:
- **`pytest -q`:** **`373 passed in 1.88s`** (todos os 359 testes anteriores preservados com apenas `test_c3_09` atualizado por exigência da Seção 3 + 14 novos testes em [`tests/test_v170_corrective_regressions.py`](../tests/test_v170_corrective_regressions.py)).
- **`unittest` (`test_issue_automation.py`):** **`Ran 13 tests in 0.009s — OK`**.
- **Benchmark rotulado (`tests/test_duplicate_guard_metrics.py` — 90 casos):**
  - `auto_close`: `tp=18`, **`fp=0`**, `fn=0`, `tn=72`, **`precision=1.0`**, **`recall=1.0`**.
  - `alert_or_close`: `tp=41`, **`fp=0`**, `fn=0`, `tn=49`, **`precision=1.0`**, **`recall=1.0`**.
  - `failures`: `[]`.

---

## 5. Histórico Verificável de Execuções em Produção (`v1.6.0` e `v1.7.0`)

### 5.1. Evidências Recuperadas da `v1.6.0` (Base: [PR #165](https://github.com/ProjectNox-org/project-nox-requests/pull/165), Commit `1491b262fd1c7d5ddb5547918045d0007e275494`)

- **CI no PR #165 (`Executar Testes das Automações`):** [Run 44887958295](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44887958295) — `success`.
- **CodeQL no PR #165:** [Run 44887956131](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44887956131) — `success`.
- **Push em `main` (PR #165):**
  - CI: [Run 44888082546](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44888082546) — `success`.
  - CodeQL: [Run 44888082824](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44888082824) — `success`.
  - Duplicate Guard (`push` em `main`): [Run 44888082538](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44888082538) — `success`.
- **Validações operacionais da `v1.6.0` em `main`:**
  - `backfill` `dry_run=true` ([Run 44888186698](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44888186698)): `"rule_version": "1.6.0"`, `"dry_run": true`, `"evaluated_count": 15`, `"skipped_cached_count": 0`, `"would_or_did_close": 0`, `"error_count": 0`.
  - `reconcile` protegido (`auto_close_enabled=false`, [Run 44888233749](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44888233749)): `"dry_run": false`, `"auto_close_enabled": false`, `"evaluated_count": 15`, `"skipped_cached_count": 15`, `"would_or_did_close": 0`, `"error_count": 0`.
  - `reconcile` real #1 ([Run 44888275104](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44888275104)): `"dry_run": false`, `"auto_close_enabled": true`, `"evaluated_count": 15`, `"skipped_cached_count": 0`, `"would_or_did_close": 0`, `"error_count": 0`.
  - `reconcile` real #2 com cache hit ([Run 44888321955](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44888321955)): `"dry_run": false`, `"auto_close_enabled": true`, `"evaluated_count": 15`, `"skipped_cached_count": 15`, `"would_or_did_close": 0`, `"error_count": 0`.

### 5.2. Validações da Correção `v1.7.0`

- **PR de correção (`v1.7.0`):** registrado após abertura e merge da branch `fix/duplicate-guard-v170-corrective-audit`.
- **Execuções pós-merge (`main`):** atualizadas nesta seção com os IDs e URLs reais de CI, CodeQL, `backfill` (`dry_run=true`), `reconcile` protegido (`NOX_DUPLICATE_GUARD_AUTO_CLOSE=false`) e `reconcile` real pós-reativação antes da declaração de veredito final.
