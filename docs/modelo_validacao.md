# Validação temporal do estimador experimental — Fase 3

**Avaliação, não promoção.** O estimador da Fase 2 continua experimental. Esta fase não altera
`q`, parâmetros, critérios de elegibilidade, o contrato de testes nem nenhum cálculo de
produção. Código: `modelo_validacao.py` (puro), rota `GET /api/modelo/validacao` (só leitura,
`app.py`). Nada é persistido.

## Pergunta
Por modalidade × duração (12 unidades independentes; nada é misturado nem extrapolado), o
estimador prevê a **próxima observação** melhor do que baselines simples?

**O alvo não é um teste padronizado:** é o melhor valor da duração em toda a atividade
(`power_curves`), sem trecho identificado, tal como o estimador o define (ver
`docs/modelo_estimador.md`). Só entram observações atualmente confirmadas e elegíveis
(`icu_ignore_power=false` explícito; desconhecido/inelegível/curva divergente/revisão
rejeitada ficam de fora, com motivo e contagem por regra; `icu_ignore_hr` nunca exclui).

## Métodos (mesmas origens, mesmos alvos)
| | Previsão para o alvo |
|---|---|
| M0 | média das observações anteriores (média histórica expansiva) |
| M1 | última observação anterior |
| M2 | nível do filtro da Fase 2 com `q = 0.01` |

Origens em que algum método não prevê (p.ex. 1.ª observação, sem histórico) ficam fora de
**todos** e são contadas com o motivo (`origens_excluidas`).

## Walk-forward sem fuga
* Informação por alvo = observações com data **estritamente anterior** ao alvo. O filtro é
  aplicado dentro de `previsoes_metodos`, independentemente do chamador. Alvos na mesma data
  partilham o mesmo treino e não se veem entre si.
* O estimador é recalculado do zero com essa sequência (mesma ordenação `(data, hora_local,
  activity_id, test_id)` da Fase 2). Testado: acrescentar ou alterar valores futuros não
  muda nenhuma previsão passada; a seleção aninhada de `q` também só vê o passado.

## Dois modos — limitação central
| Modo | O que usa | Limitação |
|---|---|---|
| `retrospectivo` | estado **atual** do registo, ordenado pela data da observação | aproximação: a decisão de confirmar foi tomada depois, com conhecimento posterior (**viés de seleção**); a elegibilidade é a atual |
| `conhecido_na_epoca` | só revisões com `revisado_em` anterior ao alvo (reconstruído do histórico append-only da Fase 1) | se os testes foram confirmados muito depois, quase não há treino (resultado honesto); os **alvos** continuam definidos em retrospetiva |

Em nenhum dos modos é possível reconstituir o que se sabia **na época** sobre
`icu_ignore_power`: só existe o valor atual das atividades. Se a diferença entre os modos for
grande, a validação retrospetiva não é fiável como estimativa de desempenho real.

## Métricas
Erro = previsão − observado (positivo = sobrestima). Por método: n, MAE, RMSE, erro médio com
sinal (viés). Diferenças M2−Mx: `dMAE = média(|e2|−|ex|)` e `dRMSE = RMSE2−RMSEx`
(negativo favorece M2), com `dMAE` relativo à MAE do baseline.

**Incerteza das diferenças** (só em amostra `avaliavel`): bootstrap em **blocos móveis**
(dependência temporal), bloco = `round(n^(1/3))`, 2000 réplicas, semente fixa por
modo/unidade/baseline, IC95 por percentis. Reporta-se também a autocorrelação lag-1 das
diferenças absolutas (n ≥ 10); a independência **não** é assumida. Alvos na mesma data são
dependentes por construção.

**Calibração (exploratória):** o IC do nível da Fase 2 **não** é um intervalo de previsão.
Para calibração usa-se um intervalo preditivo **derivado do mesmo modelo**
(`sd² = sd_nível² + σ²_ruído`, t com m g.l.), rotulado “derivado do modelo, não validado”.
A cobertura só é reportada com ≥ 10 intervalos.

## Suficiência (fixada antes de ver dados)
| Condição | Estado |
|---|---|
| 0 alvos | `inconclusivo_sem_alvos` |
| < 20 alvos **ou** < 15 datas distintas **ou** < 90 dias entre o 1.º e o último alvo | `descritivo` (só métricas descritivas; sem IC nem veredicto) |
| caso contrário | `avaliavel` |

Veredicto (só `avaliavel`): `m2_melhor_que_ambos_baselines` exige, face a M0 **e** M1, IC95 de
dMAE < 0, melhoria relativa de MAE ≥ 5 % e dRMSE < 0; o simétrico dá
`m2_pior_que_algum_baseline`; senão `diferenca_nao_demonstrada`. Os limiares (20/15/90 e
5 %) são **convenções declaradas**, não valores derivados dos dados. Um resultado
`avaliavel` numa unidade **não** se extrapola a outras unidades.

## `q`
1. **Avaliação do predefinido** (`q = 0.01`): é o que entra nos métodos, diferenças e veredicto.
2. **Sensibilidade** (grelha 0.001, 0.003, **0.01**, 0.03, 0.1, mais o valor corrente se
   diferente): MAE/RMSE/viés de M2 nos **mesmos alvos**. Exploratória; não seleciona nada.
3. **Seleção aninhada (exploratória)**: em cada origem escolhe-se `q` só com os erros
   walk-forward **dentro do treino** (≥ 6 observações e ≥ 3 erros), prevê-se o alvo e compara-se
   com o `q` predefinido **no mesmo subconjunto**. Nunca entra no veredicto. Não altera o `q`
   de produção.

## Reprodução
`validar()` é uma função pura; devolve configuração, `assinatura` SHA-256 e, por unidade,
a lista de previsões (`test_id`, data, observado, M0, M1, M2, intervalo preditivo).
A rota refaz tudo a cada pedido.

## Dados sintéticos vs reais
Os testes (`tests/pmc/test_fase3_validacao_*.py`) usam dados **sintéticos** para verificar
o pipeline (fuga, contas, determinismo, suficiência, modos). **Não demonstram validade
preditiva no atleta.** Um estimador funcional e um pipeline correto não implicam que o
estimador preveja melhor: isso só se conclui com observações reais suficientes, por unidade.
