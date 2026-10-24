# Contrato temporal do IR (Etapa 14)

Código: `modelo_ir_contrato.py` (funções puras), `modelo_ir_servico.validar_preditivo`,
rota `GET /api/modelo/ir/validacao`. Dados de produção não são alterados por esta rota.

## 1. Instantes e regra de seleção

Para um alvo com **esforço no dia D**:

| Quantidade | Regra | Instante de corte |
|---|---|---|
| Marcos da temporada (P0) | último registo de marcos com `conhecido_em` < início de D | 00:00 de D |
| Curvas de potência (P0) | última versão de cada curva com `conhecido_em` < início de D | 00:00 de D |
| YES (P0) | última transição com `conhecido_em` < início de D | 00:00 de D |
| Treino (observações passadas) | versão com prova de conhecimento (`detetado_em` ou revisão do mesmo valor) **estritamente anterior** a D | 00:00 de D |
| **y do alvo** | última versão **conhecida até ao fim do dia D, inclusive** | fim de D (granularidade de dia) |

Decisões e porquê:

- **y é o resultado que se prevê**, não uma entrada. A sua versão é a que existia no dia do esforço.
  Usa-se "até ao fim de D" porque a deteção de um esforco ocorre, em regra, no próprio dia; exigir
  "antes de D" rejeitaria quase todos os alvos. Isto **não** foi alterado para aumentar o número de
  alvos: é a regra que já estava implícita, agora explícita.
- Uma versão de y **conhecida depois de D** nunca altera o alvo de D nem qualquer alvo anterior
  (teste `RevisaoFuturaDeY`).
- Se a última versão conhecida até D for uma **retirada**, ou não existir nenhuma, o alvo é rejeitado
  com `versao_alvo_indisponivel`. Nunca se usa o valor atual como se fosse antigo.
- Sem registo de marcos/YES, ou sem histórico de curvas que cubra D, o alvo é rejeitado com motivo
  explícito (`marcos_sem_registo_antes_do_alvo`, `yes_sem_registo_antes_do_alvo`,
  `curvas_historicas_indisponiveis`). Nunca se recorre ao estado atual.

Limitação: a granularidade é **de dia**. Versões do mesmo dia não se distinguem.

## 2. Estados dos históricos

| Campo | Valores | Significado |
|---|---|---|
| `historico_temporal_estado` | `disponivel`, `incompleto`, `vazio`, `indisponivel` | estado do histórico de marcos+YES para os alvos avaliados |
| `historico_curvas_estado` | `disponivel`, `incompleto`, `vazio`, `indisponivel` | estado do histórico de curvas para os alvos avaliados |
| `historico_temporal_estado_legado` | `ok`, `tabelas_ausentes`, `sem_base`, `erro` | valor **antigo** de `historico_temporal_estado`, mantido para clientes existentes |

- `disponivel`: há registos e nenhum alvo foi rejeitado por falta deste histórico.
- `incompleto`: há registos, mas alvos foram rejeitados por falta de informação conhecida antes deles.
- `vazio`: tabelas existem sem registos. **Bloqueia** a avaliação temporal.
- `indisponivel`: tabelas ausentes, base indisponível ou erro. **Bloqueia** a avaliação temporal.

## 3. Natureza da avaliação

Campo `natureza_avaliacao` (por modo):

- `retrospectiva_valores_atuais` — modo `retrospectivo`. Usa y e estado **atuais**. **Não é validação temporal.**
- `bloqueada_por_historico_insuficiente` — modo `conhecido_na_epoca` com histórico obrigatório ausente
  ou vazio. O avaliador **não é chamado**; `itens` fica vazio e `motivo_bloqueio` diz qual.
- `temporal_validada` — avaliado com os dois históricos e sem rejeições por falta de histórico.
- `temporal_incompleta` — avaliado, mas com alvos rejeitados por falta de histórico. Os resultados
  cobrem apenas esses alvos.

`resultado_por_modo.<modo>.validacao_temporal` é `true` **só** para as duas últimas.

## 4. Contagens

Por modo, em `resultado_por_modo`: `n_alvos_previstos` (o IR produziu previsão), `n_alvos_avaliaveis`
(com baselines calculados), `n_alvos_rejeitados`, `rejeicoes_por_categoria` e `rejeicoes_por_motivo`.
Cada alvo não previsto conta **uma vez**, na categoria do primeiro estágio que falha.

## 5. Compatibilidade

Mantidas: `status`, `itens`, `historico_temporal`, `natureza_por_modo`, `legado`, `aviso_legado`, e
todas as chaves do corpo de `validar_preditivo`.

Alteradas deliberadamente (os valores antigos continuam disponíveis):

- `historico_temporal_estado`: passa a usar o vocabulário canónico; o valor antigo está em
  `historico_temporal_estado_legado`.
- Sem o registo temporal, `conhecido_na_epoca` **deixa de ser reconstruído por deteção**. Antes
  devolvia itens com `reconstruida_sem_prova_temporal`; agora devolve `itens: []` e
  `bloqueado: true`. Isto cumpre o requisito de não avaliar temporalmente sem históricos obrigatórios.

Novas: `natureza_avaliacao`, `resultado_por_modo`, `historico_curvas_estado`,
`definicoes_estado`, `limitacoes`.

## 6. Limitações que impedem interpretar como validação histórica

- Sem a migração temporal e de curvas **aplicadas** e com registos, nenhum resultado temporal é produzido.
- O histórico de curvas começa na primeira sincronização posterior à migração. Alvos anteriores são rejeitados.
- O endpoint é legado (60/300/1200 s) e não valida o IR baseado em MMP3 (180 s).
- A membership de cada observação (`selecionar_observacoes_auto`) é conferida contra as **curvas atuais**;
  uma revisão de valor pode retirar um alvo da avaliação em vez de o manter com o y antigo.

## 7. Painel IR sombra (Etapa 15)

Rota nova: `GET /api/modelo/ir/sombra?tipo=&duracao=&modo=` (60, 300 ou 1200 s; `modo` = `retrospectivo` ou
`conhecido_na_epoca`). Lê o mesmo estado que `/api/modelo/ir/validacao`, que **não muda**, e devolve uma
vista montada por `modelo_ir_painel.montar`. Não escreve, não recalibra, não promove e não tem cache: cada
pedido lê o estado atual. Só é chamada quando o utilizador clica em "Carregar seleção" no painel.

Campos da vista: `estado` (disponivel | incompleto | vazio | indisponivel), `natureza` e `natureza_rotulo`,
`validacao_temporal`, `nao_validacao_historica`, `bloqueado` e `motivo_bloqueio`, `historico_temporal` e
`historico_curvas` (estado e rótulo), `contagens` (previstos, avaliáveis, rejeitados e previsto não avaliável),
`rejeicoes_por_categoria`, `rejeicoes_por_motivo`, `lacunas`, `serie` (duas séries separadas: `sombra` = previsão
`ir`; `referencia_observada` = `y`), `periodo`, `metricas` (só com alvos avaliáveis), `suficiencia`, `veredicto`
(só com amostra avaliável), `evidencia_para_revisao` (nunca promove), `limitacoes` e `avisos`.

Regras de apresentação:
- A curva operacional (% do P0, `/api/modelo/ir/conjunto`) **não** entra na série em W. Mostra-se apenas o resumo
  do ajuste ativo, a partir do estado IR já carregado.
- Previsão calculada sem avaliação possível é contada como pendente e **não** é desenhada.
- Não há bandas de confiança: nenhum método validado as sustenta nesta vista.
- O modo retrospectivo nunca recebe rótulo de validação temporal nem evidência para revisão.
- Bloqueio conhecido: o IR operacional é 180 s (MMP3) e este endpoint só valida 60/300/1200 s. A vista declara isto
  em `avisos`. Não foi contornado com dados fictícios nem com histórico inventado.
- Não há filtro por período nem por candidato: não existe nenhum candidato múltiplo nem um parâmetro de data
  neste endpoint.
- O modo retrospectivo continua a reconstruir o P0 com as datas de deteção (comportamento pré-existente, não alterado
  nesta etapa). Por isso pode devolver zero previsões mesmo com dados completos.

### 7.1 Correções da Etapa 16

- **Códigos HTTP reais.** Um erro do endpoint de validação (400, 409, 500 ou 503) é propagado com o mesmo código e
  com corpo `{status: 'erro', mensagem}`. Antes, a rota respondia 200 com `status: OK`, o que escondia a falha.
- **Campo `origem`** em toda a vista. Com `estado: indisponivel`, a origem distingue a causa:
  `pedido_falhou` (erro do endpoint), `resposta` (resposta sem o modo pedido ou ambígua), `parametros` (seleção ou
  duração inválida) e `historico` (histórico temporal ou de curvas indisponível). Em vistas normais, `origem` é `null`.
  O estado canónico continua a ser um dos quatro; não se acrescentou um quinto estado.
- **Regra de estado única para os dois modos.** Qualquer alvo rejeitado torna a vista `incompleto`, também no modo
  retrospectivo (antes mostrava `disponivel`, ou `vazio` quando não havia pontos, escondendo as lacunas). Só o modo
  temporal depende dos históricos.
- **Cliente.** O painel mostra a mensagem real do servidor quando o pedido falha, em vez de um texto genérico.

### 8. Registo prospetivo das previsões (Etapa 16)

Módulo `modelo_ir_previsoes.py`, migração `2026-10-24_ir_sombra_previsoes_001` (preparada, **não aplicada**).

- `modelo_ir_sombra_previsoes`: uma linha por previsão, só INSERT. Identidade = hash de (modalidade, duração,
  data-alvo, versão, entradas, motivo de bloqueio). Mesmas entradas dão a mesma linha (idempotente). Entradas
  novas dão uma linha nova; a anterior fica.
- `modelo_ir_sombra_resultados`: resultados observados associados depois, em versões. Só INSERT. Um resultado
  revisto é a versão seguinte; a previsão e as versões anteriores não mudam.
- Campos: `calculado_em` (instante real do cálculo), `data_alvo`, `previsao_ir` e `parametros` (NULL quando
  bloqueada, com `motivo_bloqueio`), `entradas_sig` (hash das entradas: dados, carga, curvas conhecidas e registo
  temporal), `estado_temporal`, `versao_modelo`, `lag_dias`. Resultados: `test_id`, `y_observado`, `observado_em`,
  `registado_em`, `versao`, `resultado_sig`.
- Regras: `data_alvo` tem de ser posterior ao dia do cálculo (sem informação do alvo nas entradas). Um resultado
  não pode ser anterior ao cálculo da previsão nem ao dia do alvo. Consultas as-of filtram por `calculado_em` e por
  `registado_em`.
- Não escreve em `modelo_ir_ajustes` nem em `ultimo_ajuste_ir`. Não promove.
- O produtor e a associação estão descritos na secção 9 (Etapa 17).

### 9. Produtor prospetivo D+1 e associação de resultados (Etapa 17)

Módulo `modelo_ir_produtor_previsoes.py`. Duração **180 s** (o IR operacional). A validação retrospetiva não avalia
esta duração, porque o endpoint só aceita 60/300/1200 s. Por isso o registo prospetivo é o único caminho que
pode avaliar o IR operacional.

**Cálculo D+1.** No instante real `agora`, a previsão de amanhã usa `ajustar_observacoes` + `prever` (a mesma
ferramenta da validação), com `lag_dias >= 1`. Entram só observações com `data <= hoje` e cargas com `data <= hoje`.
A carga de hoje entra tal como é conhecida no instante; fica marcada como `carga_dia_aberto`. O P0 vem de
`resolver_p0` (puro). `garantir_referencia_p0` (que escreve) **não** é chamado. Um bloqueio é registado com
`previsao_ir` NULL e motivo explícito: `sem_observacoes_conhecidas`, `sem_p0_valido`, `lag_invalido` ou o motivo do
ajuste.

**Integração no sync.** `apos_deteccao` chama `integrar_apos_sync` depois de `recalibrar` e da sombra, num bloco
isolado (`try/except`). Falhar aqui não altera o resultado operacional (`r['ok']`, as unidades). Sem a migração
aplicada, o integrador devolve `migracao_pendente` **sem escrever nada**. O custo de cada sync aumenta com quatro
ajustes e uma leitura das entradas (MMP3 até hoje); medir antes de aplicar a migração.

**Associação (regra explícita).** Uma previsão associa-se a um resultado só quando, para a mesma modalidade, duração
e **dia-alvo**, existe **exatamente um** teste atual (última versão de cada `test_id`, em estado `detetada`):
- zero testes → `sem_resultado` (pendente; nada é inventado);
- dois ou mais → `ambiguo` (nada é associado);
- um → `registado`, com `observado_em = detetado_em` da versão atual (o instante em que o sistema conheceu o valor).
Um `test_id` é `modalidade:duracao:activity_id`, por isso é estável. Revisões do mesmo teste geram uma **versão nova**
de resultado. A previsão original não muda. Um teste retirado não apaga resultados já associados.

**Limitações.** O alvo é um teste *detetado*, isto é, um recorde do MMP3. Se não houver recorde no dia-alvo, a
previsão fica pendente para sempre. Só se avalia o dia seguinte: uma previsão mais longa exigiria cargas futuras.

**Painel.** `GET /api/modelo/ir/sombra/prospetiva` (só leitura, sem escrita): estado do registo
(`sem_previsoes`, `so_bloqueadas`, `pendentes`, `com_resultados` ou `migracao_pendente`), contagens, motivos de
bloqueio, métricas só com amostra suficiente (20 alvos, 15 datas e 90 dias) e as 20 últimas previsões com a sua
situação. Nunca mostra uma previsão reconstruída: cada linha tem `calculado_em`, e `retroativa` é sempre `false`.
