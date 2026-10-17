# Contrato dos testes de performance — Fase 1 do modelo progressivo

**Âmbito:** registar, identificar, confirmar e consultar testes. **Não estima nada.**
Nenhum parâmetro, série ou resultado do PMC (CTL, ATL, TSB, CTLγ, FMT, homeostático,
âncora) lê este registo. Confirmar um teste **não calibra nem atualiza o modelo**.
Código: `modelo_testes.py` (lógica pura), `db.py` (tabela e SQL), `app.py` (rotas),
`tabs/tab_pmc.py` (UI).

## Como complementa o que já existia
| Já existia | Papel | Alterado? |
|---|---|---|
| `protocolo.detectar_testes` | descoberta automática de esforços (60/300/1200 s, ≥92 % do melhor dos 180 dias) | não |
| `protocolo.anotar_elegibilidade` (B2) | `icu_ignore_power` → `elegivel`/`inelegivel`/`desconhecida` | não (só é lida) |
| `/api/protocolo`, sugestões da Fase A | diagnóstico e urgência | não |
| `calibrar_com_ancora`, `serie_ancora` | diagnóstico da âncora | não |

Não existia nenhum histórico de testes de performance (o `utils/calibracao_*.json`
pertence ao aquecimento). A tabela nova é a única estrutura de histórico.

## Estados
| Estado | Significado | Gravado? |
|---|---|---|
| detetado | esforço encontrado automaticamente | não |
| pendente | detetado e sem decisão; **nunca** é aprovação | não (derivado) |
| elegível / inelegível / desconhecida | regra `icu_ignore_power` da B2; ausência de informação nunca vira `false` | guardada como foto no momento da decisão |
| confirmado | decisão explícita do utilizador | sim |
| rejeitado | decisão explícita, com motivo | sim |

Detetar ≠ confirmar ≠ padronizado. Só existe método `manual`: não há protocolo de teste
verificável no projeto (`metodo_confirmacao=protocolo` está reservado e é recusado).

## Regras
1. `inelegivel` (`icu_ignore_power=true`): não pode ser confirmado (409); pode ser rejeitado.
2. `desconhecida`: só se confirma com `aceita_elegibilidade_desconhecida=true` **e** motivo;
   fica `desconhecida` no registo (`icu_ignore_power` = NULL), nunca `elegivel`.
3. Toda a decisão exige `confirmacao_explicita=true`, `motivo` e `Content-Type: application/json`.
4. O servidor volta a derivar o candidato dos dados atuais; watts, data, elegibilidade e
   `hora_local` nunca vêm do cliente. Candidato inexistente → 404.
5. Nada é gravado ao ler (GET) nem ao abrir a página.
6. Mesma decisão repetida → sem revisão nova (`alterado:false`).
7. Estado principal futuro: potência observada por duração (P60, P300, P1200) e por
   modalidade; Bike, Row, Ski e Run nunca se combinam.

## Tabela `modelo_testes_revisoes` (append-only)
Chave `(test_id, revisao)`; `test_id = modalidade:duracao_s:activity_id` (determinístico →
sem duplicados). Estado atual = última revisão. Corrigir/invalidar = nova revisão
(`invalidacao`: confirmado→rejeitado; `reconfirmacao`: rejeitado→confirmado); o código não
tem UPDATE nem DELETE sobre esta tabela. Sem chaves estrangeiras: um re-sync ou
`recriar_power_curves` não a toca.

Campos: `evento`, `estado`, `estado_anterior`, `modalidade`, `duracao_s`, `activity_id`,
`data`, `hora_local` (de `start_date_local`, NULL se ausente), `watts`, `pct_do_melhor`,
`melhor_recente`, `deteccao` (`limiar_esforco`, `janela_dias` no momento), `elegibilidade`,
`motivo_elegibilidade`, `icu_ignore_power` (0/1/NULL), `aceita_elegibilidade_desconhecida`,
`metodo_confirmacao`, `motivo`, `responsavel` (texto declarado, **não autenticado**),
`origem_registo` (`api`/`ui`/`script`), `versao_contrato`, `registado_em` (1.ª decisão),
`revisado_em`, `confirmado_em`.

## Endpoints (novos; os existentes não mudam)
| Rota | Função |
|---|---|
| `GET /api/protocolo/candidatos?tipo&duracao&estado` | candidatos, elegibilidade, estado no registo |
| `GET /api/protocolo/testes?tipo&duracao&estado` | testes registados (+ `situacao_dados`: conforme/divergente/sem_curva) |
| `GET /api/protocolo/testes/historico?test_id&tipo&duracao` | todas as revisões |
| `POST /api/protocolo/testes/decisao` | confirmar/rejeitar um candidato |

Erros: `{"status":"erro","mensagem":...,"campo":...}` com 400/404/409/415/503.

## Limitações
* `icu_ignore_power` na base real: **não verificado**. Enquanto não houver o campo, todos os
  candidatos são `desconhecida` e só se confirmam com o aceite explícito.
* A app não tem autenticação: `responsavel` é declarativo. A exigência de JSON e de
  `confirmacao_explicita` evita escritas acidentais, não é controlo de acesso.
* Append-only é garantido pelo código e pela chave primária, não por triggers.
* O SQL foi escrito para os dois drivers (`?`→`%s`, JSON como `JSONB`, booleanos como `INTEGER`)
  e testado só em SQLite: o caminho Postgres não pôde ser exercitado offline e deve ser
  verificado pelo Saurio antes do deploy (`init_schema` cria a tabela no arranque).
* O registo não é usado por nenhum cálculo: as Fases 2 em diante decidirão como (e se).

## Versão do valor e revisão de candidatos (aditivo)

- **Identidade** do candidato: `test_id` = `modalidade:duracao_s:activity_id` (inalterada).
  **Versão do valor** = `watts` atual da curva em `power_curves`, comparado com
  `math.isclose(rel_tol=1e-9, abs_tol=1e-6)`. Mudanças só de metadados (nome, data, flags) não
  são mudança de potência. Nada é persistido como "pendente": calcula-se por pedido,
  comparando a curva atual com a última revisão. Sem alteração de esquema.
- Valor mudado desde a última revisão ⇒ o candidato volta a `estado_registo = 'pendente'`
  (campos aditivos `valor_atualizado`, `estado_ultima_revisao`, `decisao_anterior`,
  `valor_igual_a_revisao`). As revisões anteriores permanecem intactas (append-only);
  a decisão nova entra como revisão seguinte, mesmo com estado igual ao anterior (o evento
  segue a regra de `_evento`), desde que o `watts` seja diferente.
  Voltar a um valor antigo conta como valor novo (pendente), sem apagar revisões.
- Elegibilidade do estimador (aditivo): `elegibilidade_estimador`,
  `motivo_elegibilidade_estimador`, `elegivel_para_estimador`. Pode diferir da do registo
  (`ignore_parts` não vazio ⇒ desconhecida para o estimador). Regras de confirmação inalteradas.
- `GET /api/protocolo/candidatos` devolve também `resumo_pendencias`.
- `POST /api/protocolo/testes/decisao` aceita `watts_vistos` (opcional): se a curva já mudou,
  responde 409 sem gravar.
- O estimador/validação só usam revisão confirmada, elegível e cujo `watts` coincide com a
  curva atual; pendentes e revisões desatualizadas ficam de fora (`dados_divergente`).
