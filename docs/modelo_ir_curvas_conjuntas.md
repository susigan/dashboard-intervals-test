# Modelo IR — curvas de influência de todas as modalidades num só gráfico (experimental)

Painel «Modelo IR: curvas de influência por modalidade» da página PMC. **Experimental, ajuste histórico (dentro da
amostra), não validado preditivamente.** Não altera PMC/CTL/ATL/TSB/FTLM/MMP nem o ajuste do modelo IR.

## O que mostra
Um único gráfico, eixo diário comum (data), uma curva por modalidade (Bike, Row, Run, Ski; cores de `config.CORES_MOD`)
e por componente do **último ajuste guardado** de cada modalidade para a duração escolhida (60 / 300 / **1200 s**, por omissão):

| Componente | Definição | Estilo |
|---|---|---|
| Positiva | `K1·E` (W) | fina, tracejada |
| Negativa | `K2·G` (W), desenhada abaixo de zero | fina, ponteada |
| Líquida | positiva − negativa | espessa, contínua |

Resposta completa do modelo = `P0 + positiva − negativa`; a curva «líquida» **não inclui P0**. Por omissão só a líquida
está ligada (12 linhas diárias são ilegíveis); a legenda liga/desliga modalidades e componentes **sem pedir nada ao
servidor**. Tooltip: data, modalidade, componente, valor em % de P0 e watts originais, «fora do ajuste» e «>100 % de P0».

## Escala comum
`% do P0 do próprio ajuste` = `100·componente/P0` (negativa com sinal −). **É só apresentação**: não altera o modelo,
não prova que as modalidades sejam fisiologicamente comparáveis, e um único eixo evita eixos duplos. Se `P0` for inválido
(≤0, ausente ou não finito) **não se calcula percentagem** (a modalidade continua com watts no payload) e o painel diz
«normalização impossível».

## Convenções temporais (as do modelo IR)
- `E_t = E_{t-1}·e^{-1/T1} + carga_t`, `G_t` com T2; `lag_dias` por modalidade (1 por omissão: a carga do dia `t` só
  entra no estado em `t+1`). Lags diferentes ficam assinalados.
- Dias sem treino: carga 0, a curva **decai** exponencialmente.
- Antes da origem da série de cada modalidade os valores são **ausentes** (`null`), nunca 0. Depois do último ajuste a
  curva continua mas esmaecida («fora do ajuste»).
- Só carga cíclica da própria modalidade (Ride→Bike, Rowing→Row, Run, NordicSki→Ski); sem dupla contagem
  (ids duplicados, vários treinos no mesmo dia somam, musculação excluída).

## Estados e avisos
`sem_ajuste`, `dados_insuficientes`, `sem_convergencia`, `sem_atividades` → **sem curva** (sem zeros, motivo visível).
`nao_identificavel` → desenhada com traço distinto («aberto»), ⚠ na legenda, aviso e marca `*` («não confirmado») nos
parâmetros; parâmetros no limite listados. Magnitudes >100 % de P0 são **assinaladas sem corte** (guias a ±100 %).
Não se alteram limites do otimizador nem parâmetros guardados.

## Overall
**Indisponível, deliberadamente.** `/api/modelo/ir/global` ajusta um modelo de uma só duração com K/T partilhados e
transferência entre modalidades (λ), devolve parâmetros/testes mas **não uma série diária**, é recalculado por pedido e
não é persistido; só haveria curva única se λ≈1. Somar/fazer a média de watts de modalidades não tem definição
matemática defensível. O chip fica visível e desativado, com `overall.motivo` e `overall.falta`. Um Overall exige primeiro
(1) definição da série global, (2) λ identificável e ≈1, (3) persistência do ajuste global, (4) critérios de validação
fora da amostra — a apresentar e aprovar antes de implementar.

## API
`GET /api/modelo/ir/conjunto?duracao=<60|300|1200>[&ate=AAAA-MM-DD]` — só leitura (sem escritas, sem recalibração,
sem otimizador). 400 sem/ com `duracao` inválida ou `tipo`; 503 sem base de dados; 405 para outros métodos.
`/api/modelo/ir/influencia` (uma modalidade, atividade, diagnósticos) mantém-se como ferramenta de diagnóstico.

## Testes
`tests/pmc/test_modelo_ir_conjunto.py` (serviço/API: alinhamento, nulos antes da origem, coerência positiva/negativa/
líquida, normalização, decaimento, lag, dupla contagem, estados, plausibilidade, Overall, ausência de escritas),
`tests/pmc/test_modelo_ir_ui.py` + `js/harness_ir.js` (JS real em node). **São testes de implementação (matemática
e interface), não validação preditiva** do modelo.
