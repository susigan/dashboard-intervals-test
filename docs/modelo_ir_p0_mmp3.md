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
