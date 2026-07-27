# Relatório técnico — avaliação arquitetural do StateWeave

> Este relatório registra o snapshot diagnóstico anterior à implementação da
> fatia de continuidade iniciada em 2026-07-27. Os estados F-01 a F-11 abaixo
> são evidência histórica do ponto de partida, não descrição do worktree atual.
> O fechamento implementado e seus limites estão em
> `docs/verification-report-2026-07-27.md`.

**Data:** 2026-07-27
**Snapshot avaliado:** `75ed9f33caebc601803e61ff52142020f3ba5544`
**Escopo:** arquitetura, memória contínua, integração com agentes, integridade,
segurança, desempenho, experiência de desenvolvimento e maturidade.

## 1. Sumário executivo

O StateWeave tem uma base técnica coerente e incomumente disciplinada para um
projeto em estágio `0.0.0.dev0`. O núcleo atual resolve bem a parte mais difícil
de confiar em uma memória de projeto: contratos fechados, proveniência,
relações verificáveis, conflitos estruturados, expiração, escrita atômica por
arquivo, exclusão de escritor, migração, backup e restauração segura.

A avaliação, porém, precisa separar duas propostas:

1. **núcleo governado de memória persistente:** já existe e está bem
   implementado para o estágio atual;
2. **framework de memória contínua, utilizável e otimizada por agentes:** ainda
   não existe de ponta a ponta.

Hoje o StateWeave recebe registros que outro componente já decidiu criar,
valida-os e preserva suas invariantes. Ele ainda não captura automaticamente o
trabalho do agente, não recupera memórias por relevância, não monta contexto
sob orçamento de tokens e não fecha o ciclo entre execução, recibo, avaliação
e promoção de novo conhecimento.

Minha recomendação é **continuar o projeto sem reescrever o núcleo** e
posicioná-lo, no curto prazo, como um **plano de controle de memória e
continuidade para agentes**. Essa posição é mais diferenciada e defensável que
“framework geral de agentes”. A próxima entrega deve ser uma fatia vertical em
que uma sessão registra conhecimento, outra sessão o recupera com proveniência
e orçamento, e o resultado volta como recibo auditável.

### Avaliação sintética

| Dimensão | Avaliação | Estado |
|---|---:|---|
| Neutralidade do núcleo | 9/10 | comprovado localmente |
| Integridade de contratos e grafo | 8,5/10 | comprovado localmente |
| Segurança de filesystem e recuperação | 8/10 | comprovado, com limites |
| Governança e limites de autoridade | 8/10 | comprovado como decisão consultiva |
| Usabilidade direta por agentes | 4/10 | parcial |
| Memória contínua de ponta a ponta | 3/10 | não implementada |
| Recuperação e otimização de contexto | 2/10 | não implementada |
| Concorrência e escala | 4,5/10 | parcial |
| Prontidão de produto | 4/10 | pré-alpha e com gates externos |

As notas não são uma declaração de maturidade. Elas apenas distinguem a força
do substrato já implementado da distância até o produto pretendido.

## 2. Método e evidências

Foram inspecionados o plano, os ADRs, a arquitetura, os contratos JSON Schema,
o código-fonte, os testes, os consumidores sintéticos, o workflow de CI e os
relatórios anteriores.

Evidência executada nesta avaliação:

- `bash scripts/check.sh`: passou;
- contratos de extração e neutralidade: passaram;
- `python3 -m unittest discover -s tests -v` com `PYTHONPATH=src`: **57 testes,
  57 aprovados**;
- drill de migração, backup e restauração: passou;
- `git diff --check`: passou;
- `main...origin/main`: `0 0` no estado local das referências;
- busca no `stateweave.core` por nomes de runtimes, modelos, projetos e paths
  privados: nenhuma ocorrência;
- benchmark sintético local de 100 e 1.000 fatos, descrito na seção 7.

O comando literal `python -m unittest ...` não pôde ser executado porque este
ambiente não expõe um binário chamado `python`. A execução equivalente com
`python3` sem instalar o pacote também não encontra o layout `src/`; o gate
canônico resolve corretamente isso com `PYTHONPATH=src`.

Não foram reverificados nesta análise:

- estado atual dos jobs hospedados no GitHub;
- comportamento em macOS ou Windows fora da evidência versionada;
- instalação limpa da distribuição;
- parecer jurídico, licença definitiva ou disponibilidade do nome;
- execução real de um agente por um adapter.

## 3. O que está tecnicamente forte

### 3.1 Núcleo realmente neutro

`stateweave.core` não conhece Codex, modelos concretos, autoridade humana fixa,
layout de um projeto de origem ou serviços externos. Paths, papéis, TTLs,
classificações e limites pertencem à configuração do consumidor. Essa
separação está alinhada ao objetivo e foi confirmada por inspeção e pelo gate
de contratos.

Essa é uma decisão arquitetural valiosa: permite que o núcleo continue estável
enquanto captura, indexação, runtimes e políticas evoluem em módulos
separados.

### 3.2 Contrato de memória explícito e verificável

Fatos, decisões e estado corrente têm schemas Draft 2020-12 fechados. A
validação oficial é aplicada ao carregamento da configuração, auditoria,
mutação e pós-migração. Invariantes entre documentos ficam separadas da
validação estrutural.

O grafo implementa:

- backlinks determinísticos;
- `supersedes` e `superseded_by`;
- proibição de supersessão entre tipos;
- detecção de ciclos;
- conflitos por `subject/predicate/scope`;
- TTL configurável e fila de revisão;
- papéis e classificações definidos pelo consumidor.

Isso evita transformar um conjunto de arquivos JSON em uma “memória” apenas
por convenção.

### 3.3 Postura conservadora de integridade

O projeto falha de modo fechado em paths ambíguos, schemas desconhecidos,
locks existentes, referências quebradas e archives hostis. A restauração
valida nomes, tipos, tamanhos, hashes e a lista exata de membros, sem
`extractall`.

A política de não roubar automaticamente um lock considerado antigo também é
correta para preservar autoria e evitar dois escritores simultâneos. O limite
é operacional: ainda falta um protocolo seguro de recuperação.

### 3.4 Governança desacoplada de efeitos

Workflow, policy pack, grafo de tarefas, roteamento, envelopes e adapters
descrevem e validam decisões sem produzir efeitos externos por importação ou
validação. Identificadores concretos de modelos aparecem somente em
observações de recibos.

Essa fronteira é adequada para um sistema que pretende operar em vários
runtimes e manter controle humano sobre publicação, rede, credenciais, custos
e ações destrutivas.

### 3.5 Privacidade por minimização

Os exemplos são sintéticos, a telemetria é opt-in, local, limitada e baseada
em allow-list, e o pacote não tenta coletar prompts, chats ou logs brutos. A
fronteira de extração e o manifesto de transformação estão bem documentados.

## 4. O que o StateWeave é hoje

O modelo atual cobre quatro classes de memória:

| Classe | Representação atual | Cobertura |
|---|---|---|
| Semântica | fatos e claims estruturados | forte |
| Decisória | decisões, consequências e supersessão | forte |
| Estado de trabalho | `STATE-current` | básica |
| Procedimental | policy packs e contratos de workflow | parcial |
| Episódica | tasks, manifests, receipts e evaluations | apenas contratos em memória |

Isso forma um **kernel de confiança** para memória de projeto. Não forma ainda
o ciclo contínuo:

```text
observar -> propor -> validar -> consolidar -> indexar -> recuperar
         -> montar contexto -> executar -> avaliar -> escrever de volta
```

Atualmente o projeto é forte em `validar`, `consolidar explicitamente` e
`auditar`. Os demais estágios estão ausentes ou são apenas contratos passivos.

## 5. Lacunas críticas e prioritárias

### F-01 — Não existe recuperação orientada à tarefa

**Prioridade:** crítica
**Estado:** não implementado

A CLI expõe `audit`, `review` e `backlinks`, mas não oferece consulta por
domínio, recência, status, relação, evidência ou relevância. Não existe API de
busca, índice derivado, ranking ou explicação de por que um registro foi
selecionado.

Sem recuperação, a memória só é utilizável se o agente já souber os
identificadores ou ler o repositório inteiro. Isso não escala cognitivamente
nem em tokens.

### F-02 — Não existe compilador de contexto

**Prioridade:** crítica
**Estado:** não implementado

Um framework otimizado para agentes precisa transformar uma consulta e um
orçamento em um pacote pequeno, determinístico e rastreável. O pacote deveria
informar:

- registros e trechos escolhidos;
- motivo da seleção;
- IDs, revisões e hashes;
- fatos vencidos ou disputados;
- conflitos conhecidos;
- fontes e grau de confiança;
- itens excluídos e motivo;
- estimativa de tamanho ou tokens.

Sem esse contrato, cada adapter terá de improvisar sua própria montagem de
prompt, criando divergência de comportamento e risco de contexto obsoleto.

### F-03 — Captura e write-back ainda são manuais

**Prioridade:** crítica
**Estado:** não implementado

O core armazena apenas o que o consumidor escreve explicitamente. O adapter
Codex prepara um documento com `execution_authorized: false`, mas não executa,
não observa o término, não produz recibo completo e não promove resultados
para fatos, decisões ou estado.

Faltam contratos para:

- capturar eventos de Git, filesystem, CI e runtime;
- criar candidatos idempotentes;
- separar observação não confiável de memória promovida;
- exigir revisão conforme risco e classificação;
- ligar a saída ao digest dos inputs;
- atualizar estado e gerar propostas de supersessão.

### F-04 — A transação em lote não é atômica contra queda de processo

**Prioridade:** alta
**Estado:** parcial

`put_records` grava arquivos sequencialmente e executa rollback quando uma
exceção retorna ao processo. Uma interrupção abrupta, `kill -9`, queda de
energia ou falha do host entre dois `os.replace` pode deixar metade de um lote
aplicada. Isso é particularmente sensível para pares recíprocos de
supersessão.

O lock remanescente faz a auditoria falhar fechada, o que ajuda a detectar a
situação, mas não restaura automaticamente a transação nem oferece um comando
de recuperação. A descrição “lote atômico” deve significar atomicidade
durável, ou ser qualificada como rollback em processo.

### F-05 — O audit e o backup ignoram entradas inesperadas

**Prioridade:** alta
**Estado:** comprovado por probe local

O carregamento e o backup usam `glob("*.json")` apenas no nível imediato das
pastas configuradas. Um JSON inválido colocado em uma subpasta de `facts` foi
ignorado: a auditoria retornou `ok: true`, um registro e zero erros.

Para uma memória confiável, conteúdo fora do layout não deve desaparecer
silenciosamente da auditoria e do backup. O sistema deve rejeitar ou reportar
arquivos, subdiretórios e extensões inesperadas nas áreas canônicas.

### F-06 — O modelo de concorrência é de escritor único sem recuperação

**Prioridade:** alta
**Estado:** parcial

O lock por diretório é apropriado para um store local pequeno, mas ainda não
há:

- revisão otimista por `expected_revision` ou `expected_sha256`;
- idempotency key de mutação;
- lease renovável;
- diagnóstico de processo/host ainda vivo;
- comando humano seguro para inspecionar e resolver lock órfão;
- snapshot consistente para leitores;
- protocolo de merge entre agentes.

Além disso, uma queda entre a criação do diretório de lock e a escrita de
`owner.json` produz um lock sem idade calculável. A política correta é não
roubá-lo, mas o produto precisa fornecer recuperação governada.

### F-07 — Workflow e orquestração são contratos, não um ledger operacional

**Prioridade:** alta
**Estado:** parcial

Os módulos opcionais validam iteráveis em memória. Não há store, versionamento,
CLI, migração, backup ou ligação persistente desses documentos ao
`memory-core`. O backup do projeto cobre configuração, fatos, decisões e
estado; não cobre tasks, handoffs, receipts ou evaluations.

O framework, portanto, ainda não consegue provar uma cadeia persistente
“pedido -> execução -> evidência -> aceitação -> memória”.

### F-08 — O caminho seguro de conteúdo ainda depende do consumidor

**Prioridade:** média-alta
**Estado:** parcial

Há boa proteção de paths e schemas, mas textos válidos podem conter segredo,
PII, instrução maliciosa ou conteúdo copiado indevidamente. A classificação é
um rótulo, não uma inspeção. O filtro de telemetria rejeita nomes de campos
sensíveis, mas não comprova que valores escalares estejam livres de conteúdo
sensível.

Memória recuperada também deve ser tratada como dado não confiável, nunca como
nova autoridade. Esse ponto é central para evitar prompt injection persistente.

Recomenda-se uma interface de policy hook para inspeção de conteúdo no ingresso
e na recuperação, mantendo implementações específicas fora do core.

### F-09 — A experiência de escrita é baixa para um agente

**Prioridade:** média
**Estado:** parcial

Não há comandos `remember`, `decide`, `state update`, `query` ou `context`.
Criar um fato exige montar manualmente um documento extenso, incluindo fonte,
datas, classe, confiança, referências e campos de supersessão.

O rigor do schema é correto; o que falta é uma camada ergonômica que construa
um candidato válido, mostre o diff semântico e só então o promova.

### F-10 — A proveniência precisa de vínculo mais forte com artefatos de código

**Prioridade:** média
**Estado:** parcial

Fatos têm URIs de fonte, mas ainda não há contrato comum para:

- commit e tree digest;
- path e seletor de linhas ou símbolo;
- hash do artefato observado;
- validade temporal `observed_at/as_of`;
- método de extração;
- runtime ou ferramenta que observou o dado;
- cadeia de derivação entre observação, resumo e fato promovido.

Esses campos são essenciais para desenvolvimento por agentes, em que um path
ou uma conclusão pode ficar obsoleto após um commit.

### F-11 — Há drift documental no estado de publicação

**Prioridade:** média
**Estado:** comprovado no snapshot

`docs/versioning.md` ainda afirma que o repositório permanece local e que
nenhuma execução hospedada foi observada. O relatório de publicação registra
repositório público e descreve uma primeira execução hospedada. O quickstart
também ainda chama o checkout de extração local.

O drift não quebra o código, mas enfraquece precisamente a proposta de memória
confiável do projeto. Estado atual e relatórios históricos precisam ser
claramente separados.

### F-12 — Uso externo continua juridicamente bloqueado

**Prioridade:** gate externo
**Estado:** pendente humano/jurídico

O repositório é público, mas não concede direitos de uso ou redistribuição e o
nome continua provisório. Isso é corretamente declarado, porém significa que
“utilizável” atualmente deve ser entendido como tecnicamente avaliável pelo
proprietário, não adotável por terceiros.

## 6. Arquitetura-alvo recomendada

O core atual deve permanecer pequeno. A continuidade deve ser construída ao
redor dele:

```text
Git / FS / CI / runtime / humano
              |
       adapters de captura
              |
   inbox de candidatos não confiáveis
              |
 validação + política + aprovação humana
              |
  +-----------+-------------------+
  |                               |
memória canônica          journal append-only
facts/decisions/state     observações/receipts
  |                               |
  +-----------+-------------------+
              |
       índices derivados
              |
 query + ranking + graph expansion
              |
     compilador de contexto
              |
 ContextBundle com IDs, hashes,
 conflitos, validade e orçamento
              |
        runtime adapter
              |
 receipt + evaluation + propostas
              |
      novo ciclo de promoção
```

### Contratos novos mínimos

1. **`MemoryCandidate`**
   Evento ainda não promovido, com fonte, hash, classificação, confiança,
   idempotency key e método de captura.

2. **`MemoryQuery`**
   Objetivo, domínio, janela temporal, tipos, estados permitidos, trust floor,
   expansão de relações e orçamento.

3. **`ContextBundle`**
   Seleção determinística com justificativas, digests, warnings, conflitos e
   tamanho estimado.

4. **`MutationPlan`**
   Criação, atualização e supersessão com precondições por revisão/hash,
   preview e efeitos esperados no grafo.

5. **`ExecutionReceipt` persistente**
   Vínculo com task, manifest, bundle, adapter, observações de runtime, efeitos
   realizados, outputs e avaliação.

6. **`RecoveryJournal`**
   Fases duráveis de uma transação, arquivos preparados, hashes anterior/novo
   e decisão explícita de completar ou reverter.

Identificadores concretos de modelos devem continuar fora da política central,
aparecendo somente em observações de runtime.

## 7. Desempenho e escala

Foi executado um probe sintético em diretório temporário local, Python 3.12.3,
criando fatos provisionais válidos em um único `put_records`.

| Fatos | Registros com estado | Escrita + validação | Auditoria isolada | RSS máximo do processo |
|---:|---:|---:|---:|---:|
| 100 | 101 | 1,4542 s | 0,0495 s | 26.828 KiB |
| 1.000 | 1.001 | 18,4152 s | 0,3914 s | 33.740 KiB |

Uma tentativa cumulativa incluindo 5.000 fatos não concluiu dentro de mais de
120 segundos e foi interrompida; ela não deve ser usada como número exato.

Interpretação:

- a auditoria completa mostrou comportamento aproximadamente linear no
  intervalo medido e ainda é aceitável para 1.000 registros;
- o caminho de escrita é dominado por validação, `fsync` por arquivo e
  auditoria completa após o lote;
- este foi o melhor caso de um único lote; inserções unitárias repetidas
  reexecutam a auditoria total e tendem a custo quadrático acumulado;
- o resultado é indicativo do ambiente local, não benchmark multiplataforma.

Antes de declarar otimização, o projeto precisa de uma suíte de desempenho
versionada com:

- 100, 1.000 e 10.000 registros;
- grafo esparso e denso;
- escrita unitária e em lote;
- construção e atualização de índice;
- consultas frias e quentes;
- contenção entre agentes;
- falha forçada em cada fase de transação;
- Linux, macOS e Windows.

Uma solução coerente seria manter JSON como fonte canônica e introduzir um
manifesto/index derivado e reconstruível, baseado em hash e metadados de
arquivo. O índice não pode virar fonte de verdade.

## 8. Roadmap recomendado

### Gate A — Definir a promessa de produto

**Objetivo:** impedir expansão ambígua de escopo.

- adotar a descrição “plano de controle de memória e continuidade para
  agentes” enquanto a execução permanecer externa;
- registrar ADR para memória semântica, episódica, de estado e procedimental;
- definir o que “contínua”, “recuperada”, “promovida” e “otimizada” significam;
- definir trust boundary de conteúdo recuperado.

**Aceite:** glossário e contratos deixam claro o que é core, módulo opcional,
adapter e responsabilidade do host.

### Gate B — Fatia vertical entre duas sessões

**Objetivo:** provar utilidade real por agente.

- criar inbox de candidatos;
- implementar `remember`, `query` e `context` em API/CLI;
- produzir `ContextBundle` sob orçamento;
- persistir receipt e evaluation;
- propor atualização de estado e supersessão;
- criar um consumidor sintético executado em duas sessões independentes.

**Aceite:** a sessão B recupera uma decisão da sessão A sem conhecer seu ID,
recebe proveniência e validade, respeita orçamento e gera um receipt ligado aos
inputs.

### Gate C — Durabilidade e concorrência

**Objetivo:** tornar o store seguro para uso repetido por vários agentes.

- journal transacional durável;
- precondição por revisão/hash;
- idempotency keys;
- comando de inspeção e recuperação de lock;
- detecção de arquivos inesperados;
- snapshot consistente para leitores;
- testes de processo morto em todas as fases.

**Aceite:** nenhuma falha forçada deixa um lote silenciosamente parcial; o
estado seguinte é completar, reverter ou bloquear com diagnóstico recuperável.

### Gate D — Recuperação e desempenho

**Objetivo:** evitar leitura integral e desperdício de tokens.

- índice derivado reconstruível;
- filtros estruturados e expansão de grafo;
- ranking explicável;
- deduplicação e diversidade de fontes;
- política para stale/disputed/deprecated;
- benchmark e SLOs por tamanho.

**Aceite:** contexto determinístico, dentro do orçamento e com justificativa
para cada item selecionado.

### Gate E — Adapters ativos e ecossistema

**Objetivo:** fechar o ciclo sem contaminar o core.

- bridge de host para Codex;
- protocolo comum de lifecycle;
- receipts observados, não inferidos;
- plugins de captura e indexação;
- armazenamento opcional para records episódicos;
- guias de integração e compatibilidade.

**Aceite:** nenhum adapter concede autoridade sozinho e todo efeito observado
é reconciliado com policy, approval e receipt.

### Gate F — Segurança, legal e publicação

- scanner/policy hook de conteúdo no ingresso e na recuperação;
- testes de prompt injection persistente;
- política de disclosure;
- evidência hospedada da matriz suportada;
- licença, contribuição e marca aprovadas;
- aprovação humana separada para qualquer tag, release ou pacote.

## 9. Métricas de sucesso sugeridas

### Correção

- 100% dos bundles com IDs, revisões e hashes;
- 100% dos receipts ligados ao digest do input manifest e do contexto;
- zero memória stale/deprecated inserida sem warning explícito;
- conflitos conhecidos sempre visíveis ao compilador de contexto;
- recuperação determinística para o mesmo snapshot e a mesma query.

### Qualidade da recuperação

- recall@k sobre cenários sintéticos com ground truth;
- precisão de itens úteis;
- diversidade de fontes;
- taxa de fatos recuperados que foram depois rejeitados;
- redução de tokens frente à leitura integral.

### Operação

- p50/p95 de query, bundle, audit e mutation;
- contenção e tempo de espera por lock;
- taxa de candidatos duplicados eliminados por idempotência;
- tempo de recuperação após interrupção;
- crescimento de store, índice, backup e fila de revisão.

### Governança

- cobertura de proveniência;
- percentual de efeitos com aprovação exigida e receipt;
- tempo de resolução de stale, disputed e conflicts;
- incidentes de conteúdo sensível aceito ou recuperado.

## 10. Decisão recomendada

**Continuar:** sim.
**Reescrever o core:** não.
**Declarar framework completo de agentes:** não neste estágio.
**Prioridade seguinte:** recuperação + compilação de contexto + write-back
persistente em uma fatia vertical de duas sessões.

O projeto já possui um alicerce melhor que muitas soluções que começam por
embeddings e só depois tentam acrescentar governança. O risco agora é o
oposto: continuar aprofundando contratos sem provar a experiência concreta de
um agente que aprende entre sessões.

A tese mais forte para o StateWeave é:

> memória local-first, auditável, governada e independente de runtime, capaz de
> fornecer contexto mínimo e verificável a agentes e registrar de volta apenas
> conhecimento promovido.

Se a próxima fase provar essa tese de ponta a ponta, o StateWeave deixa de ser
apenas uma extração tecnicamente sólida e passa a ser um produto claramente
diferenciado.
