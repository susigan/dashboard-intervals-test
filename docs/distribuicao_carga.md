# Distribuicao da carga por modalidade (aba PMC)

Painel experimental e independente: `GET /api/carga/distribuicao` + painel `distCargaExp` (bloco JS
`<<distribuicao-carga>>`). Calculo em `distribuicao_carga.py` (puro: sem BD, sem rede). So le.

## Conceitos que nao se misturam
- **A. Distribuicao da carga** (este painel): quanto da carga REGISTADA foi atribuida a cada modalidade numa janela.
  E observacao, nao influencia fisiologica nem equilibrio ideal.
- **B. Curvas de influencia do modelo IR**: decomposicao da carga passada pela equacao do ultimo ajuste (watts).
- **C. Desempenho observado**: melhores esforcos MMP e `ev_norm` (comparacao de duas janelas por mediana).

Nao existe indice unico e nenhuma afirmacao causal.

## Metodo
- Elegiveis: `config.CICLICOS` (Bike, Row, Run, Ski) via procura exata em `config.TYPE_MAP`. Musculacao
  (WeightTraining, Workout, Crossfit) e tipos nao mapeados saem em `agregar`, antes de qualquer soma:
  nao entram no numerador, denominador, series, totais nem no periodo apresentado.
- Carga: `icu_training_load`; valida = finita e >= 0; varias atividades no dia somam; mesmo `id` conta uma vez.
- Janela `[t-W+1, t]` (inclui t), W em {7, 28, 42, 90}; so janelas completas; fim = ultima atividade elegivel
  (ou `ate`). `Q_m(t) = 100 * L_m(t) / sum_m L_m(t)`; soma 100 com denominador > 0.
- Dia sem atividade elegivel = treino nulo (0). Atividade elegivel sem carga valida = dado ausente (nunca 0);
  denominador <= 0 => sem percentagens (`sem_treino` ou `carga_ausente`).
- Comparabilidade: `icu_training_load` mistura potencia/FC/ritmo calibrados por desporto. Nao ha conversao.
  O painel mostra a composicao por fonte e, no segundo metodo (TRIMP, abaixo), a diferenca (pp) entre as duas
  distribuicoes. Diferencas grandes = a distribuicao depende da variavel de carga.

## Segundo metodo: TRIMP (fase "TRIMP vs Training Load")
`GET /api/carga/distribuicao?metodo=training_load|trimp` (por omissao `training_load`). O painel tem seletor
de metodo; a secao "Comparacao entre metodos" usa a mesma resposta e e identica nos dois metodos.

### Que TRIMP existe no projeto (auditado)
1. **Campo `trimp` da atividade** (vem do Intervals.icu; o projeto nao o calcula). Verificado contra o export
   local de atividades Bike/Row/Run/Ski: `trimp = moving_time_min * x * 0,64 * exp(1,92 * x)` com
   `x = (average_heartrate - icu_resting_hr) / (athlete_max_hr - icu_resting_hr)`; razao 1,000 e CV 0,000 em
   todas as modalidades. E Banister com coeficientes masculinos, calculado com a **FC media** da atividade
   (nao por amostra). A variante feminina (0,86*exp(1,67*x)) nao bate (razao ~0,85). E a unica usada aqui.
2. `pmc._trimp_e_kj` (usado em dTRIMP/dkJ e eficiencia kJ): **TRIMP sessao-RPE** = duracao_min * RPE * IF.
   Outra variavel (exige kJ, RPE, duracao >= 10 min), so no modelo CTL vs kJ. NAO e usada nem misturada aqui.
Nao existe no projeto nenhuma funcao que calcule TRIMP de Banister a partir de FC; nada foi duplicado.

### Consistencia entre modalidades e limitacoes
- FC max e LTHR sao **por desporto** (podem diferir de uma modalidade para outra): a mesma FC pesa de forma
  diferente entre modalidades. FC de repouso e a do dia.
- FC media: subestima sessoes de intensidade variavel (a funcao e convexa) e nao distingue intervalado de continuo.
- Treinos com `icu_ignore_hr` verdadeiro podem ter `trimp` no export; seguindo `helpers.py`, FC ignorada =
  sem dados de FC, logo TRIMP invalido. Ha atividades elegiveis sem FC.
- Cobertura: o TRIMP > 0 nao cobre todas as atividades elegiveis (sem FC, FC ignorada ou valor invalido); o painel mostra a cobertura real de cada periodo.
- Razao TRIMP/Training Load: varia de modalidade para modalidade. As escalas nao coincidem entre
  modalidades: percentagens de TRIMP e de Training Load nao tem de coincidir, e os valores absolutos nao se comparam.

### Regras do modo TRIMP
- Valido = finito, > 0 e FC nao marcada para ignorar. Motivos de exclusao contados: `sem_valor`,
  `valor_invalido` (0, negativo, nan, texto, bool), `fc_ignorada`.
- Nunca se substitui TRIMP em falta por Training Load, nem o contrario. Cobertura incompleta: a distribuicao
  representa so as atividades com TRIMP valido e o painel di-lo.
- Comparacao: diferenca em pp (TRIMP menos Training Load) por modalidade e janela, valores absolutos com a
  sua unidade, cobertura de cada metodo e um controlo "mesmas atividades" (so atividades com os dois valores
  validos) que separa o efeito da metrica do efeito da cobertura. Nao ha indice nem metodo vencedor.
- A fonte de carga do IR, do PMC, do CTL/ATL/TSB e do FTLM nao muda.

## Decisao metodologica documentada (nao implementada): contribuicao IR em % de P0
A contribuicao liquida do IR (K1E - K2G, em W) pode ser expressa como `100 * (K1E - K2G) / P0`. E uma
reescala por uma constante dentro de UM ajuste (modalidade x duracao), logo matematicamente coerente ali, mas
`P0` difere entre ajustes: percentagens de modalidades/duracoes diferentes teriam bases distintas e nao
seriam comparaveis entre si. Se vier a ser apresentada, deve manter os watts, ser rotulada "% do P0 do
proprio ajuste" e nao alterar o modelo.

## Auditoria de `ev_norm` / observacoes MMP para uma fase posterior (nada implementado)
Estado atual (`pmc.py`): comparacao de duas janelas consecutivas (90/180/365 dias) por mediana, minimo de
`EVOL_N_MIN = 10` observacoes por janela e cobertura semanal >= 70%; sem declive, sem intervalo de incerteza.
Para um modelo de tendencia (ex.: Theil-Sen + bootstrap) seria preciso:
- separar por modalidade e por duracao (60/300/720/1200 s);
- tratar varios esforcos na mesma data (um melhor por data ou ponderacao explicita) e contar datas distintas;
- reportar n de observacoes, n de datas distintas e intervalo (bootstrap por data, nao por observacao);
- manter a regra de nao inferir tendencia abaixo do minimo e distinguir nulo de nao-medido.

## Requisitos para um futuro planeamento (taper) — so documentados
Data e modalidade da competicao; duracao-alvo; estado de carga ate a data; cenarios de carga futura;
incerteza dos parametros; validacao preditiva com dados reais; separacao observacao vs simulacao. Os exemplos
do video nao sao regras fixas. `lambda` do modelo global nao e transferencia fisiologica direcional.
Nao implementados: linhas/bandas de equilibrio, matriz de influencia cruzada, taper.
