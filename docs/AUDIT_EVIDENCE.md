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

- **PR de implementação da `v1.7.0`:** [PR #166](https://github.com/ProjectNox-org/project-nox-requests/pull/166)
- **Commit de merge da implementação em `main`:** `11b569a2f14d7780e55b7926fd937fdcf059d1ae`
- **Checks no PR #166 (`3ddf43e`) — todos aprovados (`4/4`):**
  - CI (`Executar Testes das Automações` / `test`): [Job 136805081497](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890511184/job/136805081497) — `success`.
  - CodeQL (`Analyze (actions)`): [Job 136805078553](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890509221/job/136805078553) — `success`.
  - CodeQL (`Analyze (python)`): [Job 136805078342](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890509221/job/136805078342) — `success`.
  - CodeQL Check Run: [Run 136805191249](https://github.com/ProjectNox-org/project-nox-requests/runs/136805191249) — `success` (0 alertas abertos).
- **Workflows de `push` em `main` após merge do PR #166 (com contenção `NOX_DUPLICATE_GUARD_AUTO_CLOSE=false`):**
  - Duplicate Guard (`push` em `main`, [Run 44890621443](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890621443)): `success` — `"effective_mode": "backfill"`, `"dry_run": false`, `"auto_close_enabled": false`, `"rule_version": "1.7.0"`, `"evaluated_count": 15`, `"skipped_cached_count": 0` (invalidação automática do cache `v1.6.0`), `"would_or_did_close": 0`, `"error_count": 0`.
  - CI (`Executar Testes das Automações`, [Run 44890621532](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890621532)): `success`.
  - CodeQL (`Push on main`, [Run 44890621344](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890621344)): `success` (`Analyze (actions)` + `Analyze (python)` concluídos sem alertas).
- **Validações operacionais em produção na `main` (`v1.7.0`):**
  - `backfill` em `dry_run=true` ([Run 44890749595](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890749595)): `success` — `"effective_mode": "backfill"`, `"dry_run": true`, `"auto_close_enabled": false`, `"rule_version": "1.7.0"`, `"evaluated_count": 15`, `"skipped_cached_count": 0`, `"would_or_did_close": 0`, `"error_count": 0`.
  - `reconcile` protegido (`auto_close_enabled=false`, [Run 44890797059](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890797059)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": false`, `"rule_version": "1.7.0"`, `"evaluated_count": 15`, `"skipped_cached_count": 15` (cache hit), `"would_or_did_close": 0`, `"error_count": 0`.
  - **Reativação controlada do fechamento automático:** variável `NOX_DUPLICATE_GUARD_AUTO_CLOSE` removida via `gh variable delete`, restaurando `.github/duplicate-guard.json` (`auto_close_enabled: true`).
  - `reconcile` real #1 pós-reativação ([Run 44890855157](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890855157)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": true`, `"rule_version": "1.7.0"`, `"evaluated_count": 15`, `"skipped_cached_count": 0` (reavaliação automática pela mudança de `auto_close_enabled`), `"would_or_did_close": 0`, `"error_count": 0`.
  - `reconcile` real #2 com cache hit ([Run 44890915395](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44890915395)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": true`, `"rule_version": "1.7.0"`, `"evaluated_count": 15`, `"skipped_cached_count": 15`, `"would_or_did_close": 0`, `"error_count": 0`.
  - **Integridade das Issues reais e labels confirmada:** todas as **15 Issues abertas** (`#123`, `#125`, `#128`, `#132`, `#134`, `#138`, `#139`, `#140`, `#144`, `#145`, `#146`, `#148`, `#150`, `#151`, `#156`) permaneceram abertas e com suas labels originais (`Nova extensão`, `Problema na extensão`, `Mudança de domínio`, `Sugestão`, `Anime`, `Mangá`, `Conteúdo +18`, `Em análise`) preservadas.
  - **CodeQL:** `0` alertas abertos em `/repos/ProjectNox-org/project-nox-requests/code-scanning/alerts`.
  - **Sync de evidências (`PR #167`, commit `aae6a9e`):** CI ([Run 44891012487](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44891012487)) e CodeQL ([Run 44891011971](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44891011971)) aprovados.

---

## 6. Correção Final `v1.7.0` → `v1.7.1` (Polaridade, Negações, Restrições e Modalidade em Observações)

### 6.1. Problema Identificado e Reprodução Real Antes (`v1.7.0`, commit `aae6a9e0c47df023142a8c996bd3dde564fe274f`)

- **Causa-raiz:** Em [`scripts/duplicate_guard/matcher.py`](../scripts/duplicate_guard/matcher.py), `_OBS_BOILERPLATE_STOPWORDS` descartava operadores críticos como `com`, `sem`, `apenas`, `somente`, `obrigatorio`, `permitir`, `exibir`, `incluir` e `adicionar`. Além disso, `_extract_observation_requirement_tokens()` e `_substantive_observations_equivalent()` tratavam os tokens como saco de palavras simples, sem codificar polaridade (`polarity:neg`), vínculo entre negação/ação/objeto, direção (`crescente` vs `decrescente`), visibilidade (`exibir` vs `ocultar`) nem modalidade (`obrigatorio`/`exigir` vs `opcional`/`dispensar`).
- **Reprodução real executada antes da correção sobre `aae6a9e0c47df023142a8c996bd3dde564fe274f` (simulação offline):**
  1. **Com filtro vs Sem filtro (`"Quero a extensão com filtro por autor."` vs `"Quero a extensão sem filtro por autor."`):**
     - `toks = (frozenset({'autor', 'filtrar'}), frozenset({'autor', 'filtrar'}))`
     - `equiv = True` → **`confirmed_duplicate`**, **`auto_close = True`** (FALSO POSITIVO REPRODUZIDO).
  2. **Exibir vs Ocultar conteúdo adulto (`"Preciso exibir conteúdo adulto na listagem."` vs `"Preciso ocultar conteúdo adulto na listagem."`):**
     - `toks = (frozenset({'listagem', 'conteudo', 'adulto'}), frozenset({'listagem', 'ocultar', 'conteudo', 'adulto'}))`
     - Como nenhum dos tokens integrava `_OBS_ACTION_OR_AXIS_TOKENS` e a sobreposição era `1.0` (`jaccard = 0.75`), retornou `equiv = True` → **`confirmed_duplicate`**, **`auto_close = True`** (SEGUNDO FALSO POSITIVO REPRODUZIDO).
  3. Demais cenários contraditórios verificados na `v1.7.0`: `permitir_vs_bloquear_login` (`possible_duplicate`, `False`), `crescente_vs_decrescente` (`possible_duplicate`, `False`), `exigir_vs_dispensar_auth` (`possible_duplicate`, `False`), `obrigatorio_vs_opcional` (`possible_duplicate`, `False`) e `apenas_pt_vs_pt_e_en` (`no_match`, `False` por `existing_only_languages` indevido sem contexto de extensão já existente).

### 6.2. Correção Implementada (`v1.7.1`)

- [`scripts/duplicate_guard/models.py`](../scripts/duplicate_guard/models.py): atualização de `RULE_ENGINE_VERSION = "1.7.1"` para invalidar caches gerados com a semântica `1.7.0`.
- [`scripts/duplicate_guard/matcher.py`](../scripts/duplicate_guard/matcher.py):
  - Remoção de operadores semânticos (`com`, `sem`, `apenas`, `somente`, `obrigatorio`, `obrigatoria`, `permitir`, `permite`, `exibir`, `mostrar`, `incluir`, `inclua`, `incluam`, `adicionar`, `adicione`, `adicionem`) de `_OBS_BOILERPLATE_STOPWORDS`.
  - Mapeamento canônico de negações (`nao`, `sem`, `nunca`, `jamais`, `nem`), acesso (`permitir` vs `bloquear`/`proibir`/`impedir`/`desativar`), visibilidade (`exibir`/`mostrar` vs `ocultar`/`esconder`/`omitir`), inclusão/exclusão (`com`/`incluir` vs `excluir`/`remover`), direção (`crescente`/`ascendente` vs `decrescente`/`descendente`), restrição (`apenas`/`somente`/`exclusivamente`/`unicamente`) e modalidade (`obrigatorio`/`exigir` vs `opcional`/`facultativo`/`dispensar`).
  - Análise contextual por cláusula em `_extract_observation_requirement_tokens()`, emitindo marcadores dimensionais (`polarity:neg`, `neg:<alvo>`, `acesso:permitir`/`acesso:bloquear`, `visibilidade:exibir`/`visibilidade:ocultar`, `restricao:apenas`, `only:<alvo>`, `ordem:crescente`/`ordem:decrescente`, `modalidade:obrigatorio`/`modalidade:opcional`, `inclusao:excluir`), verificados estritamente em `_substantive_observations_equivalent()`.
- [`scripts/duplicate_guard/normalize.py`](../scripts/duplicate_guard/normalize.py):
  - Em `extract_language_context()`, `existing_only_languages` só é populado quando há contexto de extensão preexistente ou contraste com outro idioma desejado (mantendo `#143` íntegro e permitindo que `"apenas português"` vs `"português e inglês"` em `Nova extensão` seja retido como `possible_duplicate`, `should_auto_close=False`).
  - Em `has_substantive_observations_text()`, reconhecimento ampliado de frases com negação, bloqueio, ocultação, exclusividade, direção e modalidade sem falsos positivos em agradecimentos puros.

### 6.3. Reprodução Real Depois (`v1.7.1`, Simulação Offline)

Resultados observados após a correção:
- **Com filtro vs Sem filtro:** `toks = ({'filtrar', 'autor'}, {'autor', 'filtrar', 'neg:filtrar', 'polarity:neg', 'neg:autor'})`, `equiv = False` → **`possible_duplicate`**, **`auto_close = False`**.
- **Permitir vs Bloquear login:** `toks = ({'acesso:permitir', 'login'}, {'login', 'polarity:neg', 'acesso:bloquear', 'neg:login'})`, `equiv = False` → **`possible_duplicate`**, **`auto_close = False`**.
- **Exibir vs Ocultar adulto:** `toks = ({'adulto', 'visibilidade:exibir', 'conteudo'}, {'adulto', 'polarity:neg', 'visibilidade:ocultar', 'neg:conteudo', 'conteudo', 'neg:adulto'})`, `equiv = False` → **`possible_duplicate`**, **`auto_close = False`**.
- **Apenas português vs Português e inglês:** `equiv = False` → **`possible_duplicate`**, **`auto_close = False`**.
- **Ordenação crescente vs Decrescente:** `toks = ({'ordem:crescente', 'capitulo', 'ordenar'}, {'ordem:decrescente', 'capitulo', 'ordenar'})`, `equiv = False` → **`possible_duplicate`**, **`auto_close = False`**.
- **Exigir vs Dispensar autenticação:** `equiv = False` → **`possible_duplicate`**, **`auto_close = False`**.
- **Login obrigatório vs Opcional:** `toks = ({'modalidade:obrigatorio', 'login'}, {'login', 'modalidade:opcional'})`, `equiv = False` → **`possible_duplicate`**, **`auto_close = False`**.
- **Paráfrases equivalentes (afirmativas e negativas) e duplicatas puras sem observações:** preservam `confirmed_duplicate` e `should_auto_close=True`.

### 6.4. Testes, Benchmark e Publicação `v1.7.1`

- **Suíte local completa (simulada):**
  - `python3 -m compileall -q scripts tests`: exit code `0`.
  - `pytest -q`: **`383 passed in 2.00s`** (373 anteriores preservados + 10 novos testes adversariais `test_v171_adv_01`..`10` em [`tests/test_v170_corrective_regressions.py`](../tests/test_v170_corrective_regressions.py)).
  - `python3 -m unittest discover -s tests -v`: **`Ran 13 tests in 0.009s — OK`**.
  - **Benchmark (`compute_metrics()` em [`tests/test_duplicate_guard_metrics.py`](../tests/test_duplicate_guard_metrics.py) — 98 casos):**
    - `auto_close`: `tp=19`, **`fp=0`**, `fn=0`, `tn=79`, **`precision=1.0`**, **`recall=1.0`**.
    - `alert_or_close`: `tp=49`, **`fp=0`**, `fn=0`, `tn=49`, **`precision=1.0`**, **`recall=1.0`**.
    - `failures`: `[]`.
- **Execuções de publicação e produção (`v1.7.1`):**
  - **PR de implementação da `v1.7.1`:** [PR #168](https://github.com/ProjectNox-org/project-nox-requests/pull/168) (`62dd444`) → merge em `main` no commit `5e8ce7fc7cb87760c13bdb0202e31696bb6cbb3a`.
  - **Checks no PR #168 (todos aprovados — `4/4`):**
    - CI (`Executar Testes das Automações` / `test`): [Job 136810157763](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892162890/job/136810157763) — `success`.
    - CodeQL (`Analyze (actions)`): [Job 136810155047](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892160767/job/136810155047) — `success`.
    - CodeQL (`Analyze (python)`): [Job 136810155158](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892160767/job/136810155158) — `success`.
    - CodeQL Check Run: [Run 136810286781](https://github.com/ProjectNox-org/project-nox-requests/runs/136810286781) — `success`.
  - **Workflows de `push` em `main` após merge do PR #168 (com `NOX_DUPLICATE_GUARD_AUTO_CLOSE=false`):**
    - CI (`Executar Testes das Automações`, [Run 44892293723](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892293723)): `success`.
    - CodeQL (`Push on main`, [Run 44892293635](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892293635)): `success`.
    - Duplicate Guard (`push` em `main`, [Run 44892293798](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892293798)): `success` — `"effective_mode": "backfill"`, `"dry_run": false`, `"auto_close_enabled": false`, `"rule_version": "1.7.1"`, `"evaluated_count": 15`, `"skipped_cached_count": 0` (invalidação automática do cache `v1.7.0`), `"would_or_did_close": 0`, `"error_count": 0`.
  - **Validações operacionais em produção na `main` (`v1.7.1`):**
    - `backfill` em `dry_run=true` ([Run 44892403821](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892403821)): `success` — `"effective_mode": "backfill"`, `"dry_run": true`, `"auto_close_enabled": false`, `"rule_version": "1.7.1"`, `"evaluated_count": 15`, `"skipped_cached_count": 0`, `"would_or_did_close": 0`, `"error_count": 0`.
    - `reconcile` protegido (`auto_close_enabled=false`, [Run 44892460414](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892460414)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": false`, `"rule_version": "1.7.1"`, `"evaluated_count": 15`, `"skipped_cached_count": 15` (cache hit), `"would_or_did_close": 0`, `"error_count": 0`.
    - **Reativação controlada:** variável `NOX_DUPLICATE_GUARD_AUTO_CLOSE` removida via `gh variable delete`, restaurando `auto_close_enabled: true`.
    - `reconcile` real #1 pós-reativação ([Run 44892517380](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892517380)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": true`, `"rule_version": "1.7.1"`, `"evaluated_count": 15`, `"skipped_cached_count": 0` (reavaliação pela mudança de `auto_close_enabled`), `"would_or_did_close": 0`, `"error_count": 0`.
    - `reconcile` real #2 com cache hit ([Run 44892563298](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892563298)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": true`, `"rule_version": "1.7.1"`, `"evaluated_count": 15`, `"skipped_cached_count": 15`, `"would_or_did_close": 0`, `"error_count": 0`.
  - **Integridade final:** `0` alertas abertos no CodeQL e todas as **15 Issues reais abertas** (`#123`, `#125`, `#128`, `#132`, `#134`, `#138`, `#139`, `#140`, `#144`, `#145`, `#146`, `#148`, `#150`, `#151`, `#156`) preservadas abertas com suas labels originais.
  - **Sync final `v1.7.1` ([PR #169](https://github.com/ProjectNox-org/project-nox-requests/pull/169), commit `dd2ef78c723bf52e880babe785d0db85caa1335b`):** CI ([Run 44892673202](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892673202)) e CodeQL ([Run 44892672847](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44892672847)) — `success`.

---

## 7. Correção Pontual de Dupla Negação e Múltiplas Exigências (`v1.7.1` → `v1.7.2`)

### 7.1. Problema Identificado e Reprodução Real Antes (`v1.7.1`, commit `dd2ef78c723bf52e880babe785d0db85caa1335b`)

- **Causa-raiz:** Em [`scripts/duplicate_guard/matcher.py`](../scripts/duplicate_guard/matcher.py), `_extract_observation_requirement_tokens()` verificava `prev_neg` nos ramos de `permitir`, `exibir`, `obrigatorio`, `opcional` e `crescente`/`decrescente`, mas **não verificava `prev_neg` nos ramos de `bloquear`, `ocultar` e `excluir`**, nem impedia que `_OBS_NEGATORS` emitisse `polarity:neg` + `neg:<alvo>` antes de um operador negativo imediato (`não bloquear` gerava exatamente os mesmos marcadores de `bloquear`). Além disso, múltiplas exigências ligadas por `e`/`ou` compartilhavam o mesmo escopo de cláusula.
- **Reprodução real na `v1.7.1` (simulação offline com o matcher completo):**
  - **Caso A (`"Preciso bloquear login na extensão."` vs `"Preciso não bloquear login na extensão."`):** ambos geravam `frozenset({'login', 'polarity:neg', 'acesso:bloquear', 'neg:login'})` (`equiv=True`) → **`confirmed_duplicate`, `auto_close=True`**.
  - **Caso B (`"Quero ocultar conteúdo adulto."` vs `"Quero não ocultar conteúdo adulto."`):** ambos geravam `frozenset({'conteudo', 'polarity:neg', 'neg:conteudo', 'neg:adulto', 'adulto', 'visibilidade:ocultar'})` (`equiv=True`) → **`confirmed_duplicate`, `auto_close=True`**.
  - **Caso C (`"Preciso excluir obras em inglês."` vs `"Preciso não excluir obras em inglês."`):** ambos geravam `frozenset({'inclusao:excluir', 'neg:ingles', 'polarity:neg', 'ingles'})` (`equiv=True`) → **`confirmed_duplicate`, `auto_close=True`**.
  - **Caso G — múltiplas exigências (`"Quero filtro por autor e não ocultar capítulos extras."` vs `"Quero filtro por autor e ocultar capítulos extras."`):** ambos geravam os mesmos tokens com `visibilidade:ocultar` + `polarity:neg` (`equiv=True`) → **`confirmed_duplicate`, `auto_close=True`**.
  - Casos `permitir` vs `não permitir`, `exibir` vs `não exibir` e `incluir` vs `não incluir` já resultavam em `possible_duplicate` (`auto_close=False`).

### 7.2. Correção Implementada (`v1.7.2`)

- [`scripts/duplicate_guard/models.py`](../scripts/duplicate_guard/models.py): atualização de `RULE_ENGINE_VERSION = "1.7.2"` para invalidar caches gerados sob `1.7.1`.
- [`scripts/duplicate_guard/matcher.py`](../scripts/duplicate_guard/matcher.py):
  - Segmentação de sub-cláusulas em conjunções coordenativas (`e`/`ou`) quando seguidas de operador ou ação, impedindo que a negação aplicada a uma exigência se propague ou se perca diante de outra exigência na mesma frase.
  - Tratamento simétrico de `prev_neg` (com contagem de paridade `neg_count % 2 == 1`) em todos os operadores invertíveis (`_OBS_INVERTIBLE_OPERATORS`): `não bloquear` → `acesso:permitir` (`allow:<obj>`), `não ocultar` → `visibilidade:exibir` (`show:<obj>`), `não excluir` → `inclusao:incluir` (`incl:<obj>`), `não permitir` → `acesso:bloquear`, `não exibir` → `visibilidade:ocultar`, `não incluir` → `inclusao:excluir`, `não dispensar` → `modalidade:obrigatorio`, sem emitir `polarity:neg` indevido antes de operador invertível.
- [`scripts/duplicate_guard/normalize.py`](../scripts/duplicate_guard/normalize.py): ampliação da detecção de requisitos em `has_substantive_observations_text()` para contemplar verbos/formas como `excluir`, `remover`, `omitir`, `nunca`, `jamais` e negações associadas.

### 7.3. Reprodução Real Depois (`v1.7.2`, Simulação Offline)

Resultados observados com o matcher completo após a correção:
- **Caso A (`bloquear` vs `não bloquear login`):** `toks = ({'polarity:neg', 'login', 'neg:login', 'acesso:bloquear'}, {'allow:login', 'login', 'acesso:permitir'})`, `equiv=False` → **`possible_duplicate`**, **`auto_close=False`**.
- **Caso B (`ocultar` vs `não ocultar conteúdo adulto`):** `toks = ({'conteudo', 'polarity:neg', 'visibilidade:ocultar', 'neg:conteudo', 'adulto', 'neg:adulto'}, {'show:adulto', 'visibilidade:exibir', 'show:conteudo', 'conteudo', 'adulto'})`, `equiv=False` → **`possible_duplicate`**, **`auto_close=False`**.
- **Caso C (`excluir` vs `não excluir obras em inglês`):** `toks = ({'polarity:neg', 'inclusao:excluir', 'ingles', 'neg:ingles'}, {'ingles', 'inclusao:incluir', 'incl:ingles'})`, `equiv=False` → **`possible_duplicate`**, **`auto_close=False`**.
- **Caso D (`permitir` vs `não permitir login`):** `equiv=False` → **`possible_duplicate`**, **`auto_close=False`**.
- **Caso E (`exibir` vs `não exibir conteúdo adulto`):** `equiv=False` → **`possible_duplicate`**, **`auto_close=False`**.
- **Caso F (`incluir` vs `não incluir filtro por autor`):** `equiv=False` → **`possible_duplicate`**, **`auto_close=False`**.
- **Caso G (múltiplas exigências com negação em apenas uma delas):** `equiv=False` → **`possible_duplicate`**, **`auto_close=False`**.
- **Formulações equivalentes (`"não permitir login"` ≡ `"bloquear login"`; `"não ocultar conteúdo adulto"` ≡ `"exibir conteúdo adulto"`) e duplicatas puras sem observações:** preservam `confirmed_duplicate` e `should_auto_close=True`.

### 7.4. Testes, Benchmark e Produção (`v1.7.2`)

- **Suíte local completa (simulada offline):**
  - `python3 -m compileall -q scripts tests`: exit code `0`.
  - `pytest -q`: **`393 passed in 2.01s`** (383 anteriores preservados + 10 novos testes `test_v172_dn_01`..`10` em [`tests/test_v170_corrective_regressions.py`](../tests/test_v170_corrective_regressions.py)).
  - `python3 -m unittest discover -s tests -v`: **`Ran 13 tests in 0.009s — OK`**.
  - **Benchmark (`compute_metrics()` em [`tests/test_duplicate_guard_metrics.py`](../tests/test_duplicate_guard_metrics.py) — 103 casos):**
    - `auto_close`: `tp=20`, **`fp=0`**, `fn=0`, `tn=83`, **`precision=1.0`**, **`recall=1.0`**.
    - `alert_or_close`: `tp=54`, **`fp=0`**, `fn=0`, `tn=49`, **`precision=1.0`**, **`recall=1.0`**.
    - `failures`: `[]`.
- **Publicação e validação em produção (`v1.7.2`):**
  - **PR de implementação da `v1.7.2`:** [PR #170](https://github.com/ProjectNox-org/project-nox-requests/pull/170) (`2a33d88`) → merge em `main` no commit `f63537e7caf2009d82d9f4d60873d0dc1cb2c0a9`.
  - **Checks no PR #170 (todos aprovados — `4/4`):**
    - CI (`Executar Testes das Automações` / `test`): [Job 136814380844](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893576435/job/136814380844) — `success`.
    - CodeQL (`Analyze (actions)`): [Job 136814361804](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893569482/job/136814361804) — `success`.
    - CodeQL (`Analyze (python)`): [Job 136814362005](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893569482/job/136814362005) — `success`.
    - CodeQL Check Run: [Run 136814497304](https://github.com/ProjectNox-org/project-nox-requests/runs/136814497304) — `success`.
  - **Workflows de `push` em `main` após merge do PR #170 (com `NOX_DUPLICATE_GUARD_AUTO_CLOSE=false`):**
    - CI (`Executar Testes das Automações`, [Run 44893681950](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893681950)): `success`.
    - CodeQL (`Push on main`, [Run 44893681898](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893681898)): `success`.
    - Duplicate Guard (`push` em `main`, [Run 44893681880](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893681880)): `success` — `"effective_mode": "backfill"`, `"dry_run": false`, `"auto_close_enabled": false`, `"rule_version": "1.7.2"`, `"evaluated_count": 15`, `"skipped_cached_count": 0` (invalidação automática do cache `v1.7.1`), `"would_or_did_close": 0`, `"error_count": 0`.
  - **Validações operacionais em produção na `main` (`v1.7.2`):**
    - `backfill` em `dry_run=true` ([Run 44893792373](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893792373)): `success` — `"effective_mode": "backfill"`, `"dry_run": true`, `"auto_close_enabled": false`, `"rule_version": "1.7.2"`, `"evaluated_count": 15`, `"skipped_cached_count": 0`, `"would_or_did_close": 0`, `"error_count": 0`.
    - `reconcile` protegido (`auto_close_enabled=false`, [Run 44893831898](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893831898)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": false`, `"rule_version": "1.7.2"`, `"evaluated_count": 15`, `"skipped_cached_count": 15` (cache hit), `"would_or_did_close": 0`, `"error_count": 0`.
    - **Reativação controlada:** variável `NOX_DUPLICATE_GUARD_AUTO_CLOSE` removida via `gh variable delete`, restaurando `auto_close_enabled: true`.
    - `reconcile` real #1 pós-reativação ([Run 44893898436](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893898436)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": true`, `"rule_version": "1.7.2"`, `"evaluated_count": 15`, `"skipped_cached_count": 0` (reavaliação pela mudança de `auto_close_enabled`), `"would_or_did_close": 0`, `"error_count": 0`.
    - `reconcile` real #2 com cache hit ([Run 44893945366](https://github.com/ProjectNox-org/project-nox-requests/actions/runs/44893945366)): `success` — `"effective_mode": "reconcile"`, `"dry_run": false`, `"auto_close_enabled": true`, `"rule_version": "1.7.2"`, `"evaluated_count": 15`, `"skipped_cached_count": 15`, `"would_or_did_close": 0`, `"error_count": 0`.
  - **Integridade final:** `0` alertas abertos no CodeQL e todas as **15 Issues reais abertas** (`#123`, `#125`, `#128`, `#132`, `#134`, `#138`, `#139`, `#140`, `#144`, `#145`, `#146`, `#148`, `#150`, `#151`, `#156`) preservadas abertas com suas labels originais.
