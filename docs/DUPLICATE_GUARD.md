<!-- SPDX-License-Identifier: AGPL-3.0-only -->
<!-- Copyright (C) 2026 Project Nox Contributors & Awerkori -->

# Nox Duplicate Guard — Documentação Técnica e Operacional

[← Voltar ao README principal](../README.md)

O **Nox Duplicate Guard** é o sistema híbrido do Project Nox para prevenir a abertura de tickets repetidos e identificar/tratar automaticamente solicitações duplicadas na **Central de Pedidos** (`ProjectNox-org/project-nox-requests`), sem depender de servidores permanentes ou APIs externas pagas.

---

## 1. Arquitetura Híbrida (4 Camadas)

| Camada | Onde executa | Finalidade |
| :--- | :--- | :--- |
| **Camada A — Prevenção** | Tracker (`project-nox-tracker`) | Pesquisa rápida no modal **Abrir Solicitação** (`duplicate-guard.js`) antes de redirecionar o usuário ao formulário do GitHub, facilitando votar 👍 em pedidos existentes. |
| **Camada B — Detector** | GitHub Actions (`duplicate-guard.yml`) | Motor determinístico em Python (`scripts/duplicate_guard/`) executado em eventos de Issue (`opened`, `edited`, `reopened`). |
| **Camada C — Classificação e Resolução** | `scripts/duplicate_guard/matcher.py` + `actions.py` | Classifica em `no_match`, `possible_duplicate` (apenas comenta para revisão humana) ou `confirmed_duplicate` (elegível para encerramento formal seguro com `Duplicado`). |
| **Camada D — Reconciliação** | GitHub Actions (`schedule` a cada 6h + `workflow_dispatch`) | Varre Issues abertas recentes para recuperar falhas transitórias da API, concorrência ou eventos perdidos. |

### Avaliação sobre a sugestão nativa de Issues semelhantes do GitHub

O GitHub exibe sugestões básicas ao digitar o título da Issue na interface web, porém:
1. Usuários que iniciam o fluxo pelo **Project Nox Tracker** só chegam ao formulário do GitHub depois de escolher uma categoria.
2. O formulário oficial usa GitHub Issue Forms onde os dados críticos (nome da fonte, URL, parte afetada, stacktrace, URL antiga/nova) ficam no **corpo** da Issue, e não apenas no título — além de títulos só serem normalizados após a criação pelo workflow `label-issues.yml`.
3. O recurso nativo do GitHub não distingue categorias, Mangá vs Anime, transições de domínio (`#130` vs `#153`) ou regressões entre versões.

Por isso, o Tracker oferece busca prévia tolerante a variação de escrita/URLs antes do formulário oficial (sem reproduzir ou substituir o formulário do GitHub), e o detector pós-criação analisa os campos estruturados reais da Issue.

---

## 2. Critérios de Detecção por Categoria

O motor aplica ordem total determinística `(created_at, number)`: uma Issue mais antiga nunca é encerrada em favor de uma mais nova, impedindo encerramentos mútuos mesmo em aberturas simultâneas. Pull Requests são ignorados.

1. **Nova extensão (`new_extension`):**
   - Nunca mistura `Mangá` e `Anime`.
   - Normaliza acentuação, capitalização, espaçamento, typos leves (ex.: `Wacht` vs `Watch`) e URLs (`http`/`https`, `www.`, barras finais, typos de esquema e plataformas compartilhadas como `github.io`, `workers.dev`, `t.me`, `blogspot`).
   - Domínios distintos nunca encerram automaticamente.
   - `confirmed_duplicate` exige candidato **aberto**, mesmo tipo (`Mangá`/`Anime`), mesmo domínio normalizado e identidade nominal compatível.
2. **Extensão com problema (`bug`):**
   - Exige mesma extensão e tipo compatível; **nunca** encerra apenas por mencionarem a mesma extensão.
   - Rejeita automaticamente códigos HTTP contraditórios (`403` vs `404`), partes afetadas incompatíveis (ex.: `Login` vs `Leitor / Imagens`) e regressões contra tickets `Concluído` de versão anterior ou encerrados há ≥ 3 dias.
   - Requer descrição suficiente (`≥ 15` caracteres úteis ou assinatura técnica explícita).
   - `confirmed_duplicate` exige candidato aberto sem contradições e equivalência demonstrada (mesma parte/adjacente + assinatura técnica idêntica como `No pages found`/`NXDOMAIN`/`JsonDecodingException` ou alta similaridade combinada). Casos ambíguos (ex.: mesma extensão e erro de Cloudflare em partes distintas) geram apenas alerta `possible_duplicate`.
3. **Mudança de domínio (`domain_change`):**
   - Compara extensão, tipo, `URL antiga` e `URL nova` normalizadas.
   - Se a `URL nova` diferir (ex.: `#130` vs `#153` na Fleur Blanche), retorna `no_match`.
   - `confirmed_duplicate` exige candidato aberto com mesma `URL antiga` e mesma `URL nova` válidas.
4. **Fonte indisponível (`dead_source`):**
   - Considera fonte, domínio, janela temporal (padrão 30 dias) e natureza da evidência (encerramento definitivo vs instabilidade/manutenção temporária).
   - Relatos temporários ou distantes no tempo não são encerrados automaticamente.
5. **Sugestão de melhoria (`enhancement`):**
   - Respeita o escopo (`Extensão relacionada (opcional)` ou menção clara no resumo), evitando misturar extensões distintas (ex.: `#144` Blackout Comics vs `#151` Hanami Heaven).
   - Prioriza revisão humana (`possible_duplicate`); só permite encerramento automático quando o ticket candidato está aberto, o escopo é idêntico e a equivalência funcional é praticamente literal.

---

## 3. Comentários, Encerramento Automático Seguro e Overrides

- **Marcador interno estável:** todo comentário do bot inclui `<!-- nox-duplicate-guard:v1 issue=... decision=... canonical=... action=... fp=... -->`.
- **Idempotência:** reexecuções com o mesmo resultado não postam comentários repetidos nem executam escritas redundantes. Se a Issue for editada e mudar de classificação (inclusive para `no_match`), o bot **atualiza o comentário existente**.
- **Prevenção de cadeias e ciclos:** se um candidato já estiver marcado como `Duplicado`, o matcher segue a referência canônica até o ticket original válido sem ciclos.
- **Proteções contra encerramento indevido:**
  - Nunca encerra com base em ticket `Recusado`, `Cancelado` ou `Concluído`.
  - Nunca re-encerra uma Issue que já recebeu `action=auto_closed` e foi reaberta manualmente pela equipe.
  - Nunca encerra se houver labels de triagem/resolução humana (`Aceito`, `Em desenvolvimento`, `Concluído`, `Recusado`, `Cancelado`, `Duplicado`).
  - Comentário contendo `<!-- nox-duplicate-guard:ignore -->` ou `/not-duplicate` bloqueia qualquer ação automática futura na Issue.
- **Sequência formal de encerramento:**
  1. Reconsulta ao vivo tanto a Issue alvo quanto o ticket canônico.
  2. Aplica a label oficial `Duplicado`.
  3. Remove labels de status incompatíveis (`Em análise`, `Aguardando informações`).
  4. Publica/atualiza o comentário explicativo com link para o ticket original e incentivo a reagir com 👍 nele.
  5. Fecha com `state: "closed"` e `state_reason: "duplicate"` (fallback para `"not_planned"` em HTTP 422).

---

## 4. Interação com os Workflows Existentes

- `.github/workflows/label-issues.yml`: normaliza títulos e classifica tipo/categoria/+18/status inicial. Foi endurecido para ignorar Pull Requests e reconsultar `state` e `labels` ao vivo antes de aplicar `Em análise`, evitando condição de corrida caso o Duplicate Guard já tenha encerrado a Issue como `Duplicado`.
- `.github/workflows/close-issue.yml`: mantém o encerramento manual formal pela equipe via `workflow_dispatch` (compatível com as mesmas labels `Duplicado`, `Concluído`, `Recusado`, `Cancelado`).
- `.github/workflows/issue-closed-guard.yml`: continua validando se Issues fechadas possuem label formal de resolução (`Duplicado` satisfaz integralmente essa verificação).

---

## 5. Operação, Dry-Run, Desativação e Diagnóstico

### Configuração central

Os parâmetros ficam em [`.github/duplicate-guard.json`](../.github/duplicate-guard.json).

### Desativação rápida (Kill-switch) ou desativação do auto-close

Sem editar código, configure **Repository Variables** em *Settings → Secrets and variables → Actions → Variables*:
- `NOX_DUPLICATE_GUARD_ENABLED=false` — desativa completamente o detector e a reconciliação.
- `NOX_DUPLICATE_GUARD_AUTO_CLOSE=false` — mantém apenas comentários de alerta para revisão humana, sem encerrar Issues.
- `NOX_DUPLICATE_GUARD_DRY_RUN=true` — executa todo o pipeline em modo de simulação sem alterar Issues.

Também é possível alterar `enabled`, `auto_close_enabled` ou `dry_run` diretamente em `.github/duplicate-guard.json`.

### Execução local em `dry-run`

```bash
# Simular contra fixtures locais (100% offline)
python3 -m scripts.duplicate_guard.cli \
  --mode reconcile \
  --fixtures-file tests/fixtures/real_issues_snapshot.json \
  --lookback-days 3650

# Simular contra as Issues reais do repositório (somente leitura)
GITHUB_TOKEN="$(gh auth token)" python3 -m scripts.duplicate_guard.cli \
  --mode reconcile \
  --dry-run \
  --lookback-days 60
```

### Executar testes e métricas

```bash
python3 -m pip install pytest pyyaml
python3 -m pytest -v -s tests/
```

### Como revisar falsos positivos

1. Se o bot fechou uma Issue incorretamente, reabra a Issue, remova a label `Duplicado`, restaure `Em análise` (ou aplique a label correta) e, se desejar impedir novas avaliações automáticas naquela Issue, comente `/not-duplicate`. (A simples reabertura após `auto_closed` já bloqueia automaticamente um segundo auto-close!)
2. Ajuste os limiares ou adicione um caso de regressão em `tests/test_duplicate_guard.py` e `tests/test_duplicate_guard_metrics.py`.

### Diagnóstico de falhas

- Consulte os logs e o *Job Summary* do workflow **Nox Duplicate Guard** na aba *Actions*: cada execução imprime um JSON estruturado com `classification`, `canonical_issue`, `action_taken`, `skipped_reason` e `partial_errors`.
- Em falhas transitórias da API ou `rate limit`, o cliente executa até 3 tentativas com backoff e aborta de forma conservadora sem encerrar ou corromper Issues; a rotina agendada de reconciliação (`cron: "23 */6 * * *"`) reavalia pendências automaticamente.

---

## ⚖️ Licença e Copyright

Licenciado sob **GNU Affero General Public License v3.0 (`AGPL-3.0-only`)**. Consulte [`LICENSE`](../LICENSE) e [`NOTICE`](../NOTICE).
