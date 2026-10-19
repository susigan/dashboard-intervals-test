# Modelo IR — diagnóstico de identificabilidade e arquitetura (fase 1, só diagnóstico)

Estado: **diagnóstico**. Nada neste documento altera a equação, os limites, os pesos, o objetivo, os dados,
`utils/cp_model.py`, `tabs/tab_cp_model.py` ou a UI. Ferramenta reproduzível: `tools/ir_diagnostico.py`
(offline, só leitura, usa uma cópia local da base). Testes: `tests/pmc/test_modelo_ir_excel_solver.py`.

## 1. Conclusões

1. **A equação do IR é a da folha.** Com lag 0 e os parâmetros da folha o SSE reproduzido coincide com L10
   (diferença ~1e‑11). O lag 1 do IR é uma decisão de modelação (efeito do treino do dia só no dia seguinte), não um bug.
2. **O Solver da folha está nos limites** (T1 = 25, T2 = 10) e tem ótimo único sob as suas caixas
   (P0 = 274 e K1 = 1 fixos, K2 ∈ [2,3], T1 ∈ [25,50], T2 ∈ [4,10]): SSE 4268,03 = SSE da folha. O teste do dia 113
   representa 61,7 % do SSE. A folha, portanto, também não é "identificável" no sentido forte.
3. **A não identificabilidade dos ajustes reais é explicada por falta de informação nos dados**, não por erro de
   fórmula: SNR real (amplitude do efeito / ruído) ≈ 0,4–0,8; R² dos ajustes 0,02–0,42; ótimos nos limites em quase
   todos os casos (T1 = 20 ou 120, T2 = 1 ou 15); 8 soluções equivalentes (≤ +2 % de SSE) em todos os 15 ajustes;
   correlação K1~T1 ≈ 0,98–0,99 no Jacobiano; IC de perfil toca os limites.
4. **Estender os limites não cria mínimo interior** (grelha estendida: melhoria do SSE ≤ 5,4 %). Não é mínimo local:
   o ótimo do Solver coincide com o mínimo da grelha 48×48 e com o multi-arranque.
5. **Simulações** (carga real, 2784 ajustes): com SNR < 1,5 o selo "identificável" é 0 %–3 %; só com n ≥ 60 e
   SNR ≥ 3 a precisão do selo passa de ~75 %; no melhor cenário (n ≥ 60, A ≥ 15 W, σ = 5 W, uniforme) 44–91 % dos
   ajustes saem identificáveis. Isto mostra **o que seria preciso**, não que os dados reais lá cheguem.
6. **Lag 0 vs 1** (mesmos dados, mesmo objetivo): SSE e R² quase iguais (ex. Bike 1200: 47 480 vs 48 818; Row 300:
   31 576 vs 31 579). Lag 0 manda K2 → 0 em vários casos. Não há evidência para preferir um lag pelos dados.
7. **Holdout temporal 70/30**: o IR não bate de forma consistente as linhas de base simples (último valor, mediana
   móvel de 5): ex. Bike 60 s RMSE IR 95,7 vs mediana móvel 51,1. **Não há skill preditivo demonstrado**; o ajuste é descritivo.
8. **P0 fixo** (S3/S4): P0 = P0 estimado reproduz o ajuste livre; P0 = 0,85× ou 1,0× a mediana dos alvos piora
   o SSE (Bike 60 s: 89 183 → 94 403 → 115 857) e desloca T1/T2 para os limites. O P0 imposto (=CP) é plausível
   só se o alvo for compatível em escala com o CP (ver `modelo_ir_p0_cp.md`, `compatibilidade_p0_alvo`).

Hipóteses **não** resolvidas: dados de produção (a cópia local pode diferir); o critério de seleção ≥ 92 % da
referência recente; o desajuste alvo (melhor de 60/300/1200 s numa atividade) vs P0 (regra anterior: CP).

## 2. Auditoria da equação e do objetivo

| Ponto | Verificado |
|---|---|
| E, G | `E_t = E_{t‑1}·e^{‑1/T1} + c_t`, `G_t` com T2 — igual à folha |
| Resposta | `P_t = P0 + K1·E − K2·G` |
| Condições iniciais | E = G = 0 no primeiro dia da série de carga (início = primeiro dia com carga ou teste) |
| Dias sem treino | carga 0, com decaimento |
| Alinhamento | observação indexada por data; carga por modalidade; 0 desencontros de data |
| Lag | IR 1 dia; folha 0 dias (diferença conhecida, comparada acima) |
| Erro/objetivo | previsto − observado; SSE nos dias de teste (sem pesos) |
| Limites IR | k [0,6], t1 [20,120], t2 [1,15]; o Solver da folha usa caixas mais estreitas e R ≥ 0,75 |
| Duplicados | não auditados de forma exaustiva nesta fase (ver limitações) |

## 3. Arquiteturas comparadas

| | **A** calibração por modalidade × duração (atual) | **B** calibração única por modalidade | **C** modelo conjunto entre durações |
|---|---|---|---|
| Coerência | cada alvo tem o seu P0‑escala mal acoplado ao CP | um conjunto K/T por modalidade; exige alvo único | K/T partilhados + escala por duração |
| Compat. Excel | alta (a folha é uma duração) | média | baixa |
| Dados | poucos por combinação (15–62) | mais dados, mas alvo tem de ser definido | mais dados, mais parâmetros |
| Risco | selo de identificabilidade correto mas quase nunca verde | alvo único rolante testado: **não resolve** | complexidade, risco de sobreajuste |
| Impacto em ajustes/UI | nenhum | seletor de duração perderia função | UI nova |
| Complexidade | nenhuma | média | alta |
| Evidência | diagnósticos acima | experimento B (alvo CP‑like rolante) sem ganho | não testado |

## 4. Recomendação provisória

Manter **A** como vigente e **descritiva**: continuar a mostrar o estado "não identificável" e não interpretar
K/T fisiologicamente. Só avançar para B ou C com (i) uma série‑alvo bem definida e validada, (ii) mais observações
com SNR ≥ 3, e (iii) decisão explícita do utilizador. Não alterar limites nem pesos para esconder parâmetros nos limites.

## 5. Limitações

Cópia local da base (não produção); simulações sintéticas com cargas reais mas efeito assumido; Jacobiano numérico
local; jackknife e holdout com n pequeno; nenhuma validação fisiológica.
