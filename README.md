<p align="center">
  <img src="https://raw.githubusercontent.com/ProjectNox-org/.github/main/assets/brand/nox-emblem.webp" alt="Emblema oficial do Project Nox" width="96" />
</p>

<h1 align="center">Project Nox · Central de Pedidos</h1>

<p align="center">
  Canal oficial para solicitar novas extensões, relatar problemas e acompanhar melhorias de mangá e anime no <strong>Project Nox</strong>.
</p>

> [!TIP]
> **[Abrir nova solicitação](https://github.com/ProjectNox-org/project-nox-requests/issues/new/choose)** · **[Consultar pedidos no GitHub](https://github.com/ProjectNox-org/project-nox-requests/issues)** · **[Acompanhar no Tracker](https://projectnox-org.github.io/project-nox-tracker/)**

## Sobre a Central

Este repositório concentra os pedidos da comunidade para extensões de mangá (Mihon e aplicativos compatíveis) e anime (Aniyomi e aplicativos compatíveis) mantidas pelo **Project Nox**.

Aqui você registra novas solicitações (*Issues* no GitHub), relata falhas, informa mudanças de endereço de sites e sugere melhorias. Para **instalar** extensões prontas no aplicativo, utilize os catálogos oficiais de distribuição ([Extensões de Mangá](https://github.com/ProjectNox-org/manga-extensoes) e [Extensões de Anime](https://github.com/ProjectNox-org/anime-extensoes)) — não a Central de Pedidos.

## O que você pode solicitar?

As novas solicitações utilizam cinco formulários oficiais (não é possível abrir pedidos em branco):

1. **Nova extensão** — Solicitar a inclusão de uma nova fonte de mangá ou anime.
2. **Problema na extensão** — Reportar erros em extensão existente (capítulos, imagens, vídeos, busca ou catálogo).
3. **Mudança de domínio** — Informar quando o endereço web oficial de uma extensão mudou.
4. **Fonte indisponível** — Comunicar que um site foi encerrado ou está permanentemente fora do ar.
5. **Sugestão de melhoria** — Propor novos recursos ou ajustes para as extensões e o ecossistema.

## Como abrir uma solicitação

Para abrir uma nova solicitação ou votar em pedidos já registrados, é necessário entrar com uma conta gratuita do GitHub (apenas consultar é aberto sem login):

1. **Pesquise antes:** consulte as [solicitações existentes](https://github.com/ProjectNox-org/project-nox-requests/issues) (abertas e fechadas) para confirmar se alguém já registrou o mesmo caso.
2. **Vote se já existir:** em vez de duplicar o pedido, abra a discussão correspondente e adicione a reação positiva (`👍`). O volume de votos ajuda os mantenedores a conhecer o interesse da comunidade, mas **não garante prioridade nem implementação**.
3. **Escolha o formulário adequado:** se não houver registro anterior, acesse o [seletor de formulários](https://github.com/ProjectNox-org/project-nox-requests/issues/new/choose) e selecione uma das cinco categorias.
4. **Preencha os detalhes:** informe o nome da extensão ou fonte, links públicos e passos claros para reproduzir o comportamento. Após o envio, automações padronizam o título e aplicam as etiquetas iniciais da solicitação.
5. **Acompanhe a evolução:** acompanhe respostas na própria solicitação no GitHub ou pelo [Tracker](https://projectnox-org.github.io/project-nox-tracker/).

## Acompanhar solicitações

O [**Project Nox · Tracker**](https://projectnox-org.github.io/project-nox-tracker/) organiza os pedidos deste repositório em uma interface amigável com busca e filtros por tipo, categoria e situação (`Abertas` e `Fechadas`).

O painel utiliza atualização periódica no repositório combinada a consultas complementares ao GitHub quando a aba está aberta — por isso, alterações ou votos recentes podem levar algum tempo para aparecer no Tracker.

Uma solicitação **fechada** não significa necessariamente que foi implementada: conforme a resolução aplicada pelos mantenedores, ela também pode estar marcada como **Concluído**, **Recusado**, **Duplicado** ou **Cancelado**.

## Nox Duplicate Guard

Este repositório utiliza o **Nox Duplicate Guard** para reduzir tickets repetidos e agilizar a triagem:

- **Prevenção prévia no Tracker:** ao clicar em **Abrir Solicitação** no Tracker, é possível pesquisar demandas parecidas e votar com `👍` antes de abrir uma nova Issue.
- **Triagem e limpeza automática:** após a abertura ou edição de uma Issue (e em reconciliações incrementais e varreduras históricas `backfill`), o workflow [`duplicate-guard.yml`](.github/workflows/duplicate-guard.yml) compara os campos estruturados do formulário (tipo, fonte/extensão, domínio normalizado, parte afetada, erro e contexto temporal).
- **Tratamento seguro:** duplicatas ambíguas ficam abertas com comentário informativo para revisão humana; duplicatas comprovadas contra tickets abertos compatíveis recebem justificativa com link para a Issue original e resolução oficial `Duplicado` (com proteção contra falsificação de comentários, falhas parciais e reaberturas manuais).

Consulte a documentação técnica completa em [**docs/DUPLICATE_GUARD.md**](docs/DUPLICATE_GUARD.md).

## Informações importantes

- **Privacidade:** nunca publique senhas, cookies de sessão, tokens de acesso ou dados pessoais sensíveis nos formulários ou comentários. Tudo o que é enviado nas solicitações fica visível publicamente.
- **Critérios de atendimento:** o envio de uma solicitação não garante sua criação ou correção. O atendimento depende de viabilidade técnica (como proteções anti-scraping ou conteúdo pago restrito), disponibilidade dos mantenedores e respeito aos direitos aplicáveis.

## Projetos relacionados

- [**Central Oficial do Project Nox**](https://projectnox-org.github.io/) — Portal institucional com todos os projetos ativos e avisos da iniciativa.
- [**Project Nox · Tracker**](https://projectnox-org.github.io/project-nox-tracker/) — Painel web para consultar solicitações e filtrar pedidos abertos e fechados.
- [**Extensões de Mangá**](https://github.com/ProjectNox-org/manga-extensoes) · [Código-fonte](https://github.com/ProjectNox-org/manga-fonte-extensoes) — Catálogo oficial para Mihon e forks.
- [**Extensões de Anime**](https://github.com/ProjectNox-org/anime-extensoes) · [Código-fonte](https://github.com/ProjectNox-org/anime-fonte-extensoes) — Catálogo oficial para Aniyomi e forks.

## Créditos e licenciamento

Mantido por **Project Nox Contributors & Awerkori** (veja [`NOTICE`](NOTICE)), com participação contínua da comunidade nos relatos e sugestões.

Os formulários e automações deste repositório são software livre sob a **GNU Affero General Public License v3.0 (`AGPL-3.0-only`)** — consulte [`LICENSE`](LICENSE) para o texto integral e [`NOTICE`](NOTICE) para atribuições, cláusula de interação remota em rede (Seção 13) e preservação das marcas **Project Nox** e **Awerkori** (Seção 7(e)). A licença cobre apenas o código deste repositório, sem garantias e sem conceder direitos sobre sites, extensões de terceiros ou obras protegidas.

```text
Copyright (C) 2026 Project Nox Contributors & Awerkori
Licensed under the GNU Affero General Public License, Version 3.0 (AGPL-3.0-only).
```
