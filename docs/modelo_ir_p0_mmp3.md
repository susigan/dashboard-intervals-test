# P0 global do IR = MMP3 do último registo YES

**Regra.** `P0 = MMP3 (180 s)` do esforço `YES` mais recente (data/hora efetiva do início da atividade; desempate pelo
`activity_id`), entre as quatro modalidades. O mesmo valor é o P0 de Bike, Row, Ski e Run. Não é o melhor valor
histórico, nem o PR, nem o CP do CP Model, nem FTP/eFTP, nem outra duração. Sem nenhum `YES` não há P0 (estado
`ausente` ou `dados_insuficientes`): não se cria ajuste novo e a UI diz que não existe referência MMP3 válida.

**YES.** Estado `elegivel` de `modelo_mmp3.avaliar_esforcos`: a deteção existente (`modelo_observacoes_auto.detetar`, com
as referências causais recente / season / all-time) aplicada à potência de 180 s de `power_curves`, com a proximidade
existente `protocolo.LIMIAR_ESFORCO` (0,92, inclusivo), a amostra mínima existente (>= 5 pontos válidos) e a
elegibilidade da potência do projeto (`icu_ignore_power`). Não exige PR. Uma rejeição manual no registo de testes retira
o esforço de YES. **Decisão do utilizador (2026-10-18):** manter o 0,92 do detetor existente também para 180 s (não
existe tolerância específica de 180 s no código, testes ou docs; o 0,92 estava documentado para 60/300/1200 s).

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
