# Estimador experimental de nível de potência — Fase 2 do modelo progressivo

**Estado: experimental, não validado preditivamente (isso é a Fase 3).** Nenhum cálculo de
produção (PMC, CTL, ATL, TSB, CTLγ, FMT, homeostático, calibração por âncora) lê este
módulo. Confirmar, rejeitar ou invalidar um teste **não atualiza parâmetros**: o estado é
recalculado, só em leitura, a cada pedido a `GET /api/modelo/estimador`. Nada é persistido.

Código: `modelo_estimador.py` (puro: sem BD, Flask, rede nem relógio), rota em `app.py`,
painel de diagnóstico em `tabs/tab_pmc.py` (fechado por omissão, só `GET`).

## O que é uma observação (verificado no código)
* A potência de um teste é o **melhor valor da duração em toda a atividade**
  (`power_curves`, preenchida por `/activity-power-curves`). Não há índices de stream nem
  início/fim do trecho.
* `icu_ignore_power` e `icu_ignore_hr` são campos de **nível da atividade** em
  `activities.raw`. Nenhum código lê um flag por trecho.
* `ignore_parts` é sincronizado (`config.py`) mas nada o usa e o formato **não foi
  verificado**. Se vier não vazio, não é associável ao trecho → `desconhecida`.
* Não foi possível confirmar na base real a presença nem o significado exato destes campos
  (sem chamadas externas). Se a Intervals.icu já aplicar `icu_ignore_power` ao calcular as
  curvas, isso também **não foi verificado**.

## Regra de elegibilidade para aprendizagem
A elegibilidade é **relida das atividades atuais** no momento do cálculo (não é a foto do
registo da Fase 1). Uma revisão entra se, e só se:

1. a revisão **atual** do `test_id` é `confirmado` (histórico completo validado);
2. modalidade ∈ {Bike, Row, Ski, Run}, duração ∈ {60, 300, 1200}, data válida;
3. potência finita e positiva (não booleana);
4. confirmação explícita (`confirmado_em` e `metodo_confirmacao=manual`);
5. `icu_ignore_power` é **`false` explícito** e `ignore_parts` está ausente/vazio;
6. a curva atual da atividade coincide com os watts registados (`conforme`);
7. data ≤ data de referência `ate`.

| Situação | Resultado |
|---|---|
| `icu_ignore_power=true` | excluído (`inelegivel:icu_ignore_power=true`) |
| ausente / `None` / não booleano / atividade não encontrada / atividades indisponíveis | excluído (`elegibilidade_desconhecida:<motivo>`), nunca tratado como `false` |
| `false` + `ignore_parts` não vazio | excluído (`ignore_parts_nao_associavel_ao_trecho`) |
| `false` explícito | passa esta verificação; **não prova** que o esforço seja um teste válido |
| `icu_ignore_hr` (qualquer valor) | **nunca exclui**; é só informativo |
| confirmado com opt-in de elegibilidade desconhecida (Fase 1) | continua excluído enquanto o campo for desconhecido; se entretanto passar a `false` explícito, entra |
| curva alterada por re-sync / sem curva | excluído (`dados_divergente` / `dados_sem_curva`) |

Motivos e contagens aparecem por modalidade × duração. Nada vem do cliente.

## Modelo
Para cada (modalidade, duração), independente das outras:

```
y_k  = mu_k + e_k,            e_k ~ N(0, s2)               [W]
mu_k = mu_{k-1} + h_k,        h_k ~ N(0, q · s2 · dt_k)    [dt_k em dias]
```
Variâncias relativas a `s2` (desconhecida): `P* = P/s2`.

* 1.ª observação: `mu = y_1`, `P* = 1` (prior difuso: **sem** prior fisiológico).
* Passo k ≥ 2: `P*_pred = P*_{k-1} + q·dt`; `F* = P*_pred + 1`; `v = y_k − mu_{k-1}`;
  `K = P*_pred / F*`; `mu_k = mu_{k-1} + K·v`; `P*_k = P*_pred / F*`.
* `s2_hat = (1/m) · Σ v²/F*`, `m = n − 1`.
* Ao dia `ate`, `dias` depois da última observação:
  `sd = sqrt(s2_hat) · sqrt(P*_k + q·dias)`; `IC95 = mu ± t(0.975, m) · sd`.

`q = 0.01` por dia (`Q_DIA`): **convenção de suavização, não um parâmetro individual nem
fisiológico**; não foi estimada nem validada. Interpretação: a variância do nível cresce
`q` vezes a variância do ruído por dia. A Fase 3 deve avaliá-la.

Filtro **causal**: cada estimativa usa só observações até à sua data; não há suavizador.
A trajetória devolvida é causal por construção (testado).

## Incerteza
| Observações | Estado | Estimativa | Incerteza |
|---|---|---|---|
| 0 | `nao_calibrado` | — | — |
| 1 | `estimativa_unica` | o valor medido | **não estimável** (sem variabilidade observada) |
| ≥ 2 | `experimental` | `mu` | `sd` e IC95 t com `m` g.l. |
| ≥ 2, todas iguais | `experimental` | `mu` | indeterminada (`variabilidade_observada_nula`), nunca ≈ 0 |

Limitações: é a incerteza do **nível sob o modelo**, não um intervalo de previsão de um novo
teste; não inclui a incerteza de `q`; `s2_hat` mistura ruído de medida e variação real do
nível; com poucos dados o IC é muito largo (t com 1 g.l. = 12.7); com `q` fixo há um **piso**
(mais dados não a levam a zero); cresce com os dias sem teste; assume ruído gaussiano
independente. Valores atípicos são **sinalizados** (`|z| > 3`, com ≥ 3 inovações) mas não
excluídos — o teste foi confirmado por uma pessoa; aumentam `s2_hat`.

## Ordem, mesmo dia e reprodução
* Ordem: `(data, hora_local, activity_id, test_id)`. Independente da ordem de consulta.
* Mesmo dia: `dt = 0` → repetições da mesma medida; o resultado final não depende da ordem.
* `calcular_estado(historico, atividades, curvas, ate, q)` é função pura; `ate` explícito.
  Devolve `sequencia_usada` (test_id, revisão, data, watts) e uma `assinatura` SHA-256 da
  sequência + versão + `q` + `ate`: a mesma assinatura reproduz o mesmo resultado.
* Revisão atual: o histórico completo é validado (revisões 1..n, transições e eventos
  coerentes, `test_id` coerente). Se for ambíguo → `HistoricoAmbiguo` (a rota devolve 409) e
  não se calcula nada. Invalidação/rejeição removem a observação; reconfirmação repõe-na
  (uma única vez). O histórico append-only não é alterado.

## Independência
Cada (modalidade, duração) tem estado próprio; um teste só atualiza a sua chave
(testado para as 12). Nenhum parâmetro é partilhado entre modalidades. CP e W′ não são
estimados.

## Fora de âmbito (Fases seguintes)
Validação rolling-origin contra baselines (3), persistência/versões em shadow (4),
critérios de aceitação/promoção (5), camada de carga (6). **Um estimador funcional não prova
que prevê melhor.** Os testes sintéticos (`tests/pmc/test_fase2_estimador_unit.py`, com
potência verdadeira conhecida) verificam o algoritmo, não o atleta.
