"""P0 GLOBAL do modelo IR = MMP3 do esforco `YES` mais recente (modulo PURO: sem base nem rede).

Regra (decisao final, substitui "P0 = CP confirmado")
-----------------------------------------------------
    P0 = MMP3 (180 s) do registo elegivel (`YES`) MAIS RECENTE, o MESMO valor para Bike, Row, Run e Ski

  * "YES" e o estado `elegivel` de modelo_mmp3: tag de 3 min na atividade, potencia nao ignorada e
    watts / melhor MMP3 valido ANTERIOR >= 0,92 (ver o docstring de modelo_mmp3). Um YES nao exige PR.
  * "Mais recente" = data/hora efetiva do inicio da atividade (a mesma ordem que a deteccao existente usa dentro do
    dia; sem hora, desempata o activity_id). NAO e o melhor valor historico nem o PR atual.
  * Um registo `NO` posterior nao substitui o ultimo `YES`.
  * Sem nenhum `YES` NAO ha P0: o chamador nao cria ajuste e diz o motivo. Nunca ha fallback para o CP Model, para
    FTP/eFTP, para outra duracao nem para o melhor historico.
  * O CP Model (M1/M2/M3 e os restantes) NAO e consultado nem alterado por este modulo.

A referencia guarda a proveniencia (origem, test_id, activity_id, modalidade do registo, data/hora, valor, versao da
regra) e uma assinatura. A tabela `modelo_ir_p0_referencias` (so INSERT) e reutilizada sem alteracao de esquema:
`cp_w` guarda o valor MMP3 (coluna NOT NULL herdada), `modelo_cp` = 'mmp3_ultimo_yes' e `formulacao` identifica a regra;
as referencias antigas (formulacao 'p0_igual_cp') ficam intactas e nunca se confundem com as novas.
"""
import math

import modelo_ir as ir
import modelo_mmp3 as mm

VERSAO_P0 = 4                    # 4: YES = tag 3 min + razao ao melhor anterior (modelo_mmp3.VERSAO_REGRA_P0 = 2)
FORMULA_ID = 'p0_mmp3_ultimo_yes'
ORIGEM = mm.ORIGEM_P0
EQUACAO = 'P0 = MMP3 (180 s) do esforco YES mais recente, partilhado por Bike, Row, Run e Ski  [W]'

M_SEM_YES = 'sem_referencia_mmp3_yes'
M_P0 = 'p0_invalido'

TEXTOS = {
    M_SEM_YES: 'nao existe nenhum registo MMP3 (180 s) com estado YES: nao ha referencia MMP3 valida; nao se usa o CP, '
               'FTP/eFTP, outra duracao nem o melhor valor historico',
    M_P0: 'o MMP3 do registo YES mais recente nao e um numero finito positivo',
}


def _num(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0)


def assinatura_referencia(registo):
    """Assinatura da REFERENCIA global: regra, duracao e o registo de origem (id, data/hora, valor). A modalidade da
    unidade NAO entra (a referencia e global); a mesma assinatura nas quatro unidades."""
    return ir.assinatura({'v': VERSAO_P0, 'f': FORMULA_ID, 'dur': mm.DURACAO_S, 'test_id': registo['test_id'],
                          'inicio': registo.get('inicio') or registo['data'], 'watts': float(registo['watts'])})


def resolver_p0(modalidade, duracao_s, global_):
    """P0 (global) da unidade ou o motivo do bloqueio. Puro e deterministico.

    global_: saida de modelo_mmp3.p0_global. `modalidade`/`duracao_s` so identificam a unidade do IR: o valor e o
    mesmo para todas."""
    base = {'modalidade': modalidade, 'duracao_s': duracao_s, 'origem': ORIGEM, 'estado_referencia': global_['estado'],
            'n_yes': global_.get('n_yes'), 'valor_p0': None}
    reg = global_.get('registo')
    if not reg:
        return {**base, 'valida': False, 'motivo': M_SEM_YES, 'detalhe': TEXTOS[M_SEM_YES]}
    p0 = reg['watts']
    if not _num(p0):
        return {**base, 'valida': False, 'motivo': M_P0, 'detalhe': TEXTOS[M_P0], 'registo': reg}
    p0 = float(p0)
    return {**base, 'valida': True, 'motivo': None, 'registo': reg, 'valor_p0': p0, 'formulacao': FORMULA_ID,
            'equacao': EQUACAO, 'versao_regra': mm.VERSAO_REGRA_P0,
            'assinatura': assinatura_referencia(reg)}
