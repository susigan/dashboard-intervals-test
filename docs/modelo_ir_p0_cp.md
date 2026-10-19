> **SUBSTITUÍDO** por [modelo_ir_p0_mmp3.md](modelo_ir_p0_mmp3.md): o P0 do IR já não é o CP do CP Model. Este documento fica como histórico da regra anterior.

# Modelo IR — P0 = CP e recalibração automática

Estado: **experimental**. Decisão final: **P0 = CP**, exatamente o valor de CP (`cp_w`, em W) do resultado
selecionado e guardado no CP Model para a modalidade. A interpretação `P0 = CP + W′/d` foi rejeitada e não é usada.

## Regra

| | |
|---|---|
| P0 | `cp_w` do resultado em vigor da modalidade em `cp_resultados` (o mesmo "instantâneo gravado" que `/api/cp/actual/<modalidade>` devolve), sem arredondar nem somar nada |
| Não entra no P0 | W′, duração do IR (60/300/1200 s), SEE%, pontos do CP, MMP, mediana das observações, outro modelo escolhido automaticamente, parâmetro livre do Solver |
| Modelo | o `modelo_escolhido` gravado. O de menor SEE% nunca é escolhido pelo IR. O CP Model não distingue "escolha explícita" de "default gravado"; essa limitação mantém-se e o IR não inventa uma confirmação |
| Duração | identifica o alvo e o ajuste, nunca o P0. As três durações da modalidade partilham o mesmo P0 |
| Solver | estima **K1, K2, T1, T2** com P0 fixo (4 parâmetros livres, graus de liberdade = n − 4). Nunca estima P0 |

Código: `modelo_ir_p0.py` (resolvedor puro), `modelo_ir_servico.py` (referência, recalibração, curvas),
`modelo_ir.py` (`ajustar(..., p0_fixo=)`). `utils/cp_model.py` e `tabs/tab_cp_model.py` não foram alterados.

## Fluxo automático

1. O utilizador grava o resultado no CP Model (`POST /api/perfil/guardar` com `cp`), ou o sync/deteção corre.
2. O IR lê o resultado em vigor e resolve P0 = CP (`resolver_p0`).
3. Se a **assinatura da referência** mudou, grava uma **nova referência de P0** (`modelo_ir_p0_referencias`, só INSERT)
   com `conhecida_desde = agora`.
4. Corre o Solver com P0 fixo e grava uma **nova versão de ajuste** (`modelo_ir_ajustes`, só INSERT) ligada a essa
   referência (`entradas.p0_ref`).
5. Referências e ajustes anteriores nunca são alterados nem apagados. Repetir sem mudanças é idempotente.

Assinatura da referência = (modalidade, modelo selecionado, CP, **data de referência** do resultado, versão da regra).
Consequências:

* **CP mudou** → referência nova + recalibração (motivo `cp_alterado`).
* **Só W′ mudou** (mesma data de referência) → nada: sem referência nova, sem Solver. W′ não faz parte do P0.
* **Revisão anual / nova gravação com data de referência nova e o mesmo CP** → referência nova + recalibração
  (motivo `referencia_p0_alterada`): a nova referência de CP é um novo ponto de ancoragem no tempo.
* Regravar o mesmo resultado (mesma chave modalidade/season/data) → idempotente.
* Gravar com data de referência **mais antiga** que a vigente não substitui a vigente → nada acontece.

### Revisão anual do CP

Não há agendamento artificial. O utilizador faz a revisão no CP Model como hoje: calcula, escolhe o modelo e grava
com a data de referência da revisão. Isso aciona o fluxo acima. Se o resultado for inválido (ver Bloqueios), o IR não
cria referência nem ajuste, regista o motivo e mantém o último ajuste válido, indicado como não atualizado.

Gatilhos: `POST /api/perfil/guardar` (com `cp`), sync/deteção de testes, `POST /api/modelo/ir/recalibrar`.
Não disparam: `/api/perfil/apagar`, gravar só perfil/limiares, "calcular agora" sem gravar.

## Bloqueios (apenas o que a matemática do resultado justifica)

`sem_resultado_cp_confirmado`, `modelo_nao_registado`, `cp_invalido` (ausente, não finito ou ≤ 0),
`cp_inconsistente_com_modelos` (o CP da linha difere do CP guardado para o mesmo modelo),
`graus_de_liberdade_cp_insuficientes` (n_pts ≤ k_params: o CP seria uma interpolação exata, sem SEE%).

Avisos que **não** bloqueiam e ficam na referência (`avisos_cp`): modelo de 3 parâmetros, W′ fora do intervalo
plausível do CP Model, modelo ausente em `modelos_json`, graus de liberdade não verificáveis.

**Não há limiar de SEE%, de W′ nem de número de pontos além dos graus de liberdade.** O CP Model mostra o SEE% e não
define corte; escolher um aqui seria arbitrário e não é justificável por teste. É uma limitação assumida: um CP
mal ajustado mas matematicamente válido passa, com o SEE% registado.

## Falhas do Solver e atomicidade

* Não há transação única referência + ajuste. A referência é gravada antes do Solver.
* Se o Solver lança exceção, grava-se uma versão de ajuste `sem_convergencia` (sem parâmetros) com
  `metricas.excecao`; o **último ajuste válido fica intacto** no histórico, mas deixa de ser o "atual".
  Ação devolvida: `solver_falhou`; a próxima passagem repete a tentativa.
* Se a gravação do ajuste falha (ex. disco), a referência fica e o ajuste não; a assinatura do ajuste não coincide,
  logo a próxima passagem refaz o ajuste sem duplicar a referência.
* A UI e as curvas marcam `p0_desatualizado` e mostram o ajuste válido anterior como histórico. Nunca desenham
  parâmetros antigos com o P0 novo como se estivessem recalibrados.
* Falha do IR nunca estraga a gravação do CP (a resposta traz `modelo_ir.erro`).

## Qualidade e identificabilidade

Mantêm-se os estados do IR (`identificavel`, `nao_identificavel`, `insuficiente`, `sem_convergencia`) e os mínimos
existentes (15 observações, 180 dias de cobertura, 12 datas distintas). Com P0 fixo há 4 parâmetros livres; os que
ficam nos limites (`k:[0,6]`, `t1:[20,120]`, `t2:[1,15]`) são sinalizados e o ajuste não é interpretável
fisiologicamente. Ajuste válido pode ser guardado; não identificável é guardado mas rotulado; matemática inválida
bloqueia.

## Limitação fisiológica que não se esconde

O **alvo** do IR é o melhor valor de potência (60/300/1200 s) numa atividade, não um teste máximo; o **P0 = CP** é a
potência sustentável. Alvos submáximos abaixo do CP, ou de duração curta (60 s, muito acima do CP), são
incompatíveis em escala com P0 = CP. `compatibilidade_p0_alvo` (mediana dos alvos sobre P0, % de alvos acima de P0) é
registada em cada ajuste e mostrada na UI. Os parâmetros K1/K2/T1/T2 são **ajustes de um modelo sob um P0 imposto**,
não constantes fisiológicas validadas; as curvas não estão validadas fisiologicamente.

## Causalidade

Cada ajuste é recuperável com modalidade, P0, referência de origem, CP, modelo, data de referência,
`conhecida_desde`, K1/K2/T1/T2, estado e diagnósticos. No walk-forward só entra a referência com
`conhecida_desde` estritamente anterior à data do alvo; sem ela o alvo não tem previsão IR
(`sem_referencia_p0_conhecida`). Nada histórico é reescrito.

## Curvas

Uma curva por modalidade (sem Overall e sem somar potências entre modalidades). Cada curva mostra a versão do P0
(`P0 = CP (valor W · modelo) · ref. data · conhecida data · vN`), a versão do ajuste, o estado de qualidade, o aviso
de não identificabilidade, períodos sem observações e trechos de extrapolação.
