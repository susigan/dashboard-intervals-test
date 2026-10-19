# P0 global do IR = MMP3 do último registo YES

**Regra.** `P0 = MMP3 (180 s)` do esforço `YES` mais recente (data/hora efetiva do início da atividade; desempate pelo
`activity_id`), entre as quatro modalidades. O mesmo valor é o P0 de Bike, Row, Ski e Run. Não é o melhor valor
histórico, nem o PR, nem o CP do CP Model, nem FTP/eFTP, nem outra duração. Sem nenhum `YES` não há P0 (estado
`ausente` ou `dados_insuficientes`): não se cria ajuste novo e a UI diz que não existe referência MMP3 válida.

**YES (regra versão 2, decisão do utilizador 2026-10-19).** Atualizar a Power Curve **não** é um teste. Um esforço de
180 s é `YES` (`modelo_mmp3.avaliar_esforcos`) quando, cumulativamente:

1. a atividade tem a **tag de tempo de 3 min** (`3min`, `3 min`, `180s`…; `modelo_observacoes_auto.tag_coincide_duracao`);
   sem tag => `NO` com motivo `sem_tag_3min` (testado antes da suficiência de dados);
2. a potência não é ignorada (`icu_ignore_power` => `NO`) e a elegibilidade é conhecida (desconhecida => `dados_insuficientes`);
3. existe referência válida **anterior** e a amostra mínima existente (>= 5 pontos válidos de 180 s da modalidade);
4. `watts_180s / melhor_MMP3_valido_anterior >= 0,92` (inclusivo; esforço sobre referência).

A referência é **só o melhor MMP3 válido anterior** (all-time causal da modalidade, sem o próprio esforço — que fica fora
da série —, sem atividades ignoradas, sem dados posteriores). As referências `recente` e `season` do detetor deixam de
qualificar. Superar o recorde (razão >= 1) é YES; ficar abaixo, até 0,92 do recorde, também. Não exige PR. Uma rejeição
manual no registo de testes retira o esforço de YES. O 0,92 (`protocolo.LIMIAR_ESFORCO`) **não foi alterado** e não está
calibrado para 3 min (decisão de 2026-10-18).

*Porque mudou.* A regra anterior era um OR sobre as referências recente / season / all-time. Row 18/10/2026: 318 W (treino
da season, season anterior 253 W, all-time 401 W) era YES porque 318 / 300 (season, ela própria feita de treinos
253 -> 268 -> 290 -> 300) = 106 %, embora 318 / 401 = 79,3 %. A atividade não era a sua própria referência (o esforço
avaliado é excluído), mas os treinos validavam-se em cadeia. Na regra nova: sem tag => NO (`sem_tag_3min`); com tag,
318 / 401 = 79,3 % < 0,92 => NO (`abaixo_do_limiar`).

*Efeito no histórico.* O YES é derivado em tempo de execução de `power_curves` + tags das atividades; nada persistido é
reescrito. As referências P0 e os ajustes são append-only: com a regra nova a assinatura da referência muda
(`VERSAO_P0 = 4`, `VERSAO_REGRA_P0 = 2`) e, se o último YES mudar, o próximo `recalibrar` grava uma referência nova e um
ajuste novo nas quatro modalidades; as linhas antigas ficam preservadas (auditáveis) e o ajuste antigo aparece
desatualizado. Não há reclassificação em massa nem reprocessamento automático do histórico.

**Recalibração e sync (fluxo).** `POST /api/modelo/ir/recalibrar` (corpo JSON opcional `forcar`, `tipo`, `duracao_s`) é
**só POST**: um GET devolve 405 (foi o que o navegador mostrou; a rota existe em `main`). Respostas: 200 ok, 409 erro de
validação/entrada, 503 sem base, 500 exceção. A UI atual não tem botão que a chame; se o tiver, deve usar
`fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})` e tratar 200 / 409 / 503 / 500.
Ordem: (1) `GET /api/sync` só se houver dados novos/desatualizados (`sync_activities` + `sync_power_curves`; este corre a
deteção e o gancho `apos_deteccao`); (2) elegibilidade MMP3 e referência global; (3) recalibração do IR com o P0
global válido (`recalibrar`; não chama o sync nem a API externa; sem YES não recalibra); (4) confirmar os quatro ajustes
(`unidades` na resposta / `/api/modelo/ir/conjunto`). `POST /api/protocolo/observacoes-auto/atualizar` corre a deteção
sobre as curvas guardadas e o gancho, sem sync.

**Referência persistida.** `modelo_ir_p0_referencias` (só INSERT, sem alteração de esquema), uma linha por modalidade
com o mesmo registo de origem e a mesma assinatura. Colunas herdadas: `cp_w` = valor MMP3, `modelo_cp` =
`mmp3_ultimo_yes`, `cp_data_referencia`/`cp_data_gravacao` = data e hora do registo, `cp_resultado_ref` e
`parametros.registo` = `test_id`, `activity_id`, modalidade, data/hora, watts. `formulacao = p0_mmp3_ultimo_yes`;
as referências antigas (`p0_igual_cp`) ficam intactas.

**Solver.** P0 fixo; só K1, K2, T1, T2 são estimados. Registo YES novo => nova versão de referência nas quatro
modalidades e novo ajuste em cada uma; sem mudança => nada é gravado. Uma falha do Solver numa modalidade grava a
tentativa falhada dessa modalidade; o último ajuste válido fica preservado e é apresentado como desatualizado
(`erro_solver`), sem afetar as outras.

**Legado.** `validar_preditivo` e o painel de validação (60/300/1200 s) ficam marcados como LEGADO: não alimentam nem
validam o IR de MMP3. O CP Model (M1/M2/M3 e restantes) não foi alterado e não influencia o IR.

**Limitações.** O P0 é um valor único para quatro modalidades; os ajustes são em amostra (histórico), com P0 de hoje
aplicado a todo o histórico; sem validação preditiva do IR de MMP3; identificabilidade não demonstrada.

## Regra v3 — deteção de testes a partir dos intervalos das atividades

**Fonte dos intervalos.** `activities.raw.interval_summary` (lista, ou texto JSON de lista, de itens como `"1x 2m56s 189w"`:
repetições × duração, valor médio, unidade `w`/`bpm`; itens de ritmo como `"3x 4m9s 5:10"` não são potência e são descartados).
Não há início/fim, tipo nem id de intervalo na cópia local; `/activity/{id}/intervals` e `icu_intervals_json` só existem via API
(não usados). Os intervalos são **evidência anexada ao esforço** (máximo móvel de 180 s da `power_curves`): o valor do P0 é sempre
o MMP3 do esforço, nunca a média do intervalo.

**Sinais de teste.** `tag_manual` (tag de 3 min, continua válida e tem prioridade) ou `intervalo_detectado`.
Um intervalo só promove a YES se: é único (n = 1), dura 175–185 s, tem potência média ≥ 0,92 × melhor MMP3 anterior (causal,
sem a própria atividade) **e** ≥ 0,92 × o máximo móvel de 180 s da atividade; e o esforço passa a regra de proximidade
(≥ 0,92), referência com amostra ≥ 5, potência não ignorada e elegibilidade conhecida.

**Janela ±5 s.** Justificada pelo histograma real: 151 grupos estruturados concentram-se em 179–182 s; fora disso aparecem
durações de outro tipo de treino.

**Classes (`classe_intervalo`)**: `intervalo_3min_automatico` (YES); `a_verificar_repeticoes` (n ≥ 2);
`a_verificar_pico_fora` (o pico de 180 s está noutro trecho); `a_verificar_intervalo_longo` (186–360 s próximo do MMP3:
média de 5 min **não** equivale a máximo móvel de 180 s); `intervalo_3min_abaixo_limiar`; `sem_intervalo_3min`;
`sem_dados_de_intervalos`. Tudo o que não é provado fica `a_verificar`, nunca YES.

**Mantém-se**: P0 global = MMP3 do YES mais recente de todo o histórico, igual para Bike/Row/Run/Ski; comparação causal;
limiar 0,92; sem fallback para CP/FTP/eFTP/outras durações; treino forte comum nunca vira teste; um esforço por atividade
(sem dupla contagem entre intervalos e máximo móvel).

**Diagnóstico** (`p0_global.diagnostico`, por modalidade e total): intervalos analisados, candidatos por duração e por
potência, longos, `a_verificar`, confirmados por tag, elegíveis automáticos, motivos de exclusão, último YES (atividade,
data, modalidade, potência) e estado do Solver (`estado_solver`): `nao_executado` (sem P0 / sem observações),
`dados_insuficientes` (faltam N observações), `executado_nao_identificavel`, `executado_ok`, `falha`.

**Limitações fisiológicas não validadas**: o limiar 0,92 não foi calibrado para 3 min; a média de um intervalo de ~3 min
pode ser inferior ao máximo móvel real; a janela ±5 s foi derivada de um único atleta/histórico; intervalos sem início/fim
não permitem confirmar que o esforço de 180 s coincide com o intervalo.
