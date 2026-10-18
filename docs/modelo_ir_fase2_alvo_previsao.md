# Modelo IR — fase 2: compatibilidade alvo × P0, identificabilidade e previsão temporal

Estado: **diagnóstico** (arquitetura A vigente). Nada aqui altera a equação, a função objetivo, os limites, os pesos, o lag,
os dados, o CP Model ou a interface. Reproduzível com `tools/ir_fase2.py` (usa `tools/ir_diagnostico.py`,
`modelo_ir.avaliar_preditivo`, `modelo_validacao`; não reimplementa a matemática). Testes: `tests/pmc/test_modelo_ir_fase2.py`,
`tests/pmc/test_modelo_ir_teste_novo.py`.

**Dados.** Cópia local da base (12 unidades modalidade × duração; 13–62 alvos). **Não existe nenhum CP confirmado na cópia local**
(`cp_resultados` vive na base de perfil, ausente aqui). Por isso **nenhum CP foi substituído por aproximação**: a compatibilidade
é medida como "para que valores de P0 o alvo é compatível", e a ferramenta aceita o teu CP real (`--cp Bike=W`).
Os resultados "reais" abaixo são ajustes a esses alvos (melhor valor da duração numa atividade perto do melhor recente), com
**P0 livre**, salvo onde se diz P0 fixo (proxy rotulado, nunca CP).

## 1. Alvo × P0 (real)

Tabela por unidade (P0* = P0 do ajuste livre; intervalo = P0 fixo com F ≤ F95 contra P0 livre; "grelha": 0,2–1,3 × mediana do alvo):

| Unidade | n | mediana alvo (W) | P0* (W) | P0*/mediana | P0 compatível 95 % (W) | F com P0 = mediana |
|---|---|---|---|---|---|---|
| Bike 60 | 43 | 265 | 188 | 0,71 | 148–233 | 11,4 |
| Bike 300 | 52 | 216 | 191 | 0,88 | 173–207 | 7,7 |
| Bike 1200 | 62 | 168 | 146 | 0,87 | 128–165 | 5,5 |
| Row 60 | 27 | 315 | 135 | 0,43 | 63–252 | 10,8 |
| Row 300 | 47 | 182 | 142 | 0,78 | 113–167 | 8,2 |
| Row 1200 | 58 | 158 | 134 | 0,84 | 120–146 | 13,5 |
| Run 60 / 300 | 13 / 14 | 505 / 388 | — | — | n < 15: sem ajuste | — |
| Run 1200 | 16 | 325 | 280 | 0,86 | 221–318 | 5,9 |
| Ski 60 | 17 | 265 | 66 | 0,25 | 53–154 | 14,8 |
| Ski 300 | 34 | 189 | 132 | 0,70 | 79–185 | 4,7 |
| Ski 1200 | 37 | 147 | 154 | 1,05 | 106–171 | 0,2 |

* Em 9 de 10 unidades ajustáveis, **fixar P0 na mediana do próprio alvo é rejeitado** (F crítico ≈ 4,0–4,8); o P0 compatível fica
  tipicamente em 0,7–0,9 × a mediana do alvo, i.e. **abaixo** do nível dos esforços observados. O alvo (melhor esforço do dia) vive
  acima do P0; o nível é então absorvido por K1·E e deixa de ser separável da dinâmica (K1 e T1 correlacionam |ρ| 0,97–0,99).
* **Uma única P0 por modalidade** (a regra do projeto: as 3 durações partilham o CP): o Bike não tem P0 compatível com as três
  durações (60 s: 148–233, 300 s: 173–207, 1200 s: 128–165 → interseção vazia, 173 > 165). Row: 120–146 W; Ski: 106–154 W.
  (Resolução de grelha, 95 % por unidade sem correção de comparações múltiplas.)
* Isto é uma afirmação sobre o **ajuste do modelo**, não sobre fisiologia. Se o teu CP confirmado estiver fora destes intervalos, o IR
  com P0 = CP não descreve aquele alvo: `python tools/ir_fase2.py alvo --db ... --cp Bike=W`.
* Distribuição: o CV dos alvos é 0,08–0,41 (60 s o maior); gap mediano entre observações 7,5–29,5 dias, máximo 108–296 dias;
  só ~10–20 % dos dias da série têm carga na modalidade; maior bloco sem carga 27–146 dias. Resultados completos: `alvo_real.json` da ferramenta.

## 2. Identificabilidade (real, P0 livre, lag 1)

| Aspeto | Evidência |
|---|---|
| Informação | SNR (desvio do efeito ÷ desvio residual) 0,37–0,81; R² 0,14–0,60; 10 de 10 ajustes possíveis `nao_identificavel` (Run 60/300: n < 15) |
| Correlação | K1~T1 ≈ 0,97–0,99; K1~T2, K2~T2 até −1,0 (Row); número de condição normalizado 14–305 (moderado: o problema é colinearidade, não singularidade) |
| Soluções equivalentes | 79–200 de 200 arranques ficam a ≤ +2 % do SSE em todas as unidades; em 4 unidades diferem 20–40 W **nas previsões** (Bike 300: 40 W; Row 60: 31 W; Ski 1200: 30 W; Row 300: 21 W): nem a previsão é determinada pelos dados |
| Mínimo local | não: o SSE mínimo da grelha 48×48 coincide com o do Solver (razão 1,0000) |
| Dados/outliers | um único teste explica 7–41 % do SSE (Run 1200: 41 % com n = 16); duplicados não auditados exaustivamente |
| Jackknife (n ≥ 15) | T1 ou T2 no limite em ≥ 86 % das remoções em 9 de 10 unidades (Ski 60 é a exceção); Bike 60: T1 de 23 a 120; Ski 1200: T1 20↔120 |
| P0 fixo | com P0 = 0,85 ou 1,0 × mediana (proxy) o SSE sobe e T1/T2 vão aos limites (ver fase 1); sem CP confirmado não se mede o efeito de "P0 = CP" |

Classificação: **informação insuficiente** (todas) + **colinearidade estrutural K~T** (todas) + **incompatibilidade alvo–P0**
(9/10 com P0 = mediana; Bike sem P0 comum). **Não** é mínimo local; **erro de dados** só pontual (outliers dominantes). Run 60/300: n < 15.

## 3. Validação temporal (real, walk-forward um passo, sem fuga)

Treino = observações com data estritamente anterior; cargas até ao dia anterior; refit em cada alvo; mesmas datas para todos os métodos
(M0 média expansiva, M1 último valor, M2 filtro q = 0,01, MM5 mediana móvel das 5 últimas). Modo retrospetivo (viés de seleção:
a decisão de confirmar foi posterior). Avaliado só a partir de 15 observações de treino (12–47 alvos por unidade).

RMSE (W), P0 livre:

| Unidade | n | IR | M0 | M1 | M2 | MM5 | IR dentro da amostra |
|---|---|---|---|---|---|---|---|
| Bike 60 | 28 | 59,1 | 70,1 | 60,2 | 51,1 | 47,5 | 48,4 |
| Bike 300 | 37 | 34,8 | 34,9 | 40,3 | 34,4 | 33,5 | 30,5 |
| Bike 1200 | 47 | 34,4 | 32,8 | 27,4 | 27,6 | 30,6 | 28,5 |
| Row 60 | 12 | 132,4 | 115,9 | 83,9 | 92,4 | 106,7 | 95,8 |
| Row 300 | 32 | 30,1 | 34,0 | 37,3 | 31,1 | 33,2 | 26,8 |
| Row 1200 | 40 | 19,5 | 21,1 | 16,0 | 15,8 | 17,4 | 17,3 |
| Run 1200 | 1 | — | — | — | — | — | (n = 1) |
| Ski 60 / 300 / 1200 | 2 / 19 / 22 | 17,1 / 34,3 / 23,9 | 21,8 / 37,5 / 24,0 | 1,4 / 33,8 / 13,4 | 19,7 / 30,4 / 14,9 | 22,0 / 32,5 / 20,3 | 13,1 / 25,5 / 20,2 |

* **Nenhuma** unidade tem o IR melhor que ambos M0 e M1 (veredicto do projeto). Bike 1200, Row 1200 e Ski 1200: IR pior que pelo menos um baseline.
  O erro relativo agregado (descritivo, erros correlacionados, n = 240): IR 0,180; M0 0,174; M1 0,177; M2 0,153; MM5 0,163.
* Fora da amostra o IR piora vs dentro (ex. Bike 60 48→59 W; Ski 300 26→34 W): o ajuste dentro da amostra sobrestima a capacidade.
* Com P0 fixo-proxy (0,85 ou 1,0 × mediana dos alvos anteriores) o quadro é o mesmo (pooled 0,175 / 0,172); não é CP.
* Limitações: amostras pequenas (Row 60: 12; Run, Ski 60: 1–2); o alvo é o próximo esforço **detetado** (perto do melhor recente), não a
  capacidade; M1/M2 beneficiam dessa persistência; o modo `conhecido_na_epoca` **não é avaliável localmente** (as 448 observações têm todas o mesmo
  `detetado_em`, 2026‑10‑18: uma única deteção retroativa).

## 4. Testes novos e datas reais (auditoria + teste isolado)

Mecanismo existente (sem alterar): o teste entra como observação em `modelo_observacoes_auto` (append‑only, `data` = data da atividade),
`apos_deteccao` → `recalibrar` reutiliza a referência de P0 em vigor; o CP só muda por `POST /api/perfil/guardar`. Provado por
`test_modelo_ir_teste_novo.py` (base temporária; `calcular_cp_completo` proibido durante a chegada do teste): CP gravado inalterado,
nenhuma referência de P0 nova, nova versão de ajuste com `n_obs`+1, `data_fim` = data real, P0 = CP; o ajuste anterior não usa o teste futuro.
Não existe ponte teste→CP e nada foi criado. **Não há dados locais de testes novos prospetivos** (nem CP datado): o experimento necessário é registar
testes padronizados (protocolo) com data e CP confirmado/gravado antes, e avaliar as previsões prospetivas; nenhum resultado foi inventado.

## 5. Simulações (SINTÉTICAS: cargas reais, efeito e ruído assumidos; não provam nada sobre os dados reais)

Verdade A (T1 45, T2 7, K2/K1 2), 24 repetições por célula, Bike/Row. "Falso selo" = estado `identificável` com T1 ou T2 a > 25 % da verdade.

* **n × espaçamento** (amplitude 15 W, σ 5 W, P0 livre): n=15: selo 0–4 %, T1&T2 < 25 % em 11–25 %; n=30: selo 8–21 %, falso selo 50–88 %;
  n=60: selo 35–56 %, falso 25–42 %; n=120: selo 73–88 %, falso 10–26 %. Layout (uniforme/regular/agrupado/lacuna 12 m) muda pouco
  nesta amostra (diferenças dentro do ruído de 48 execuções); **o selo "identificável" não é fiável para n ≤ 60**.
* **Amplitude/ruído** (n = 60): só depende da razão A/σ: A/σ ≤ 1 → recuperação (T1 e T2 < 25 %) 2–6 %, selo ≈ 0; A/σ = 1,5 → 17 %; A/σ = 3 → 54 %/selo 50 %; A/σ ≥ 6 → ≥ 85 %.
  O SNR real (0,37–0,81, definição análoga) está na zona sem recuperação.
* **P0 imposto errado** (n = 60, A 15, σ 5): P0 certo → recuperação 56 % (P0 livre: 54 %); P0 ±5 % → 35 %/15 %; ±15 % → ≤ 4 %; ±30 % → ~0 %. Com P0 −5 %, 31 % dos ajustes saem
  `identificável` e 60 % desses estão errados: **um P0 mal especificado produz parâmetros confiantes e errados**.
* Nenhum limiar (n, SNR) é proposto como regra de produção.

## 6. Conclusões

**Comprovado (neste conjunto local):**
1. A equação e o objetivo são os da folha; a calibração independente por unidade é matematicamente coerente com a equação.
2. Os dados reais não identificam K1, K2, T1, T2 (SNR < 1, colinearidade K~T, soluções equivalentes que divergem até 40 W na previsão).
3. O IR não acrescenta valor preditivo sobre baselines simples em nenhuma unidade avaliada; em 3 é pior.
4. Com P0 = mediana do alvo o ajuste é rejeitado em 9/10; no Bike nenhuma P0 única serve as 3 durações.
5. A chegada de um teste novo não toca no CP nem no P0 (teste isolado).

**Hipóteses (não resolvidas):** CP confirmado real dentro/fora dos intervalos; efeito do critério de seleção ≥ 92 % no alvo; dados de produção;
se alvos de testes padronizados (maximais) mudam a conclusão. **Limitações:** cópia local; sem CP; amostras pequenas; retrospetivo; sintéticos assumidos.

**Recomendação (A continua vigente):** (a) tratar K1/K2/T1/T2 como **descrição matemática do ajuste, sem interpretação fisiológica**
(proposta de texto na interface, a decidir por ti); (b) o selo "identificável" não deve ser lido como prova (falso selo 25–40 % a n=60 sintético) — rever o critério só com validação própria;
(c) dados mais informativos: testes padronizados (60/300/1200 s) com espaçamento regular e SNR conhecido, CP datado e confirmado antes de cada teste, e série prospetiva para validar;
(d) executar `ir_fase2.py alvo --cp` com os teus CP confirmados. Se estruturalmente se quiser P0 comum às 3 durações com alvos "melhor do dia", a evidência acima
sugere que A com P0 = CP é inadequada para esse alvo: seria uma proposta **separada** (não implementada).
