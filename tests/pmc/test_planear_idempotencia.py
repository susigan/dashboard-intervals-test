"""Idempotencia semantica de modelo_observacoes_auto.planear.

Uma observacao so' se regrava quando a DECISAO ou a PROVA da referencia mudam, ou quando o
contrato gravado e' anterior ao atual. Sem mudanca semantica, nada e' gravado (idempotencia).
Dados sinteticos: o caso Ski-300 e' so' a fixture; nao afirma valores da observacao real.

Modulo puro: sem app, sem base de dados.
"""
import copy
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import config  # noqa: E402
import modelo_observacoes_auto as moa  # noqa: E402
import protocolo  # noqa: E402

TESTES = {300: protocolo.TESTES[300]}
AGORA = datetime(2026, 10, 22, tzinfo=timezone.utc)
TID = 'Ski:300:i126571067'


def season(d):
    return config.season_de(d, None)


def cv(aid, data, w, tipo='Ski'):
    """Curva com um unico ponto de 300 s (o suficiente para a serie)."""
    return {'activity_id': aid, 'type': tipo, 'date': data, 'weight': 70.0,
            'secs': [300], 'watts': [w]}


def base():
    # all-time 286 (2022, i2977223); season 2026: b1 250, d1 200 (ambas anteriores a C);
    # C = 262 em 2026-02-20. Serie com >= 5 pontos validos.
    return [cv('i2977223', '2022-01-03', 286), cv('a2', '2022-02-01', 100),
            cv('a3', '2022-02-02', 100), cv('a4', '2022-02-03', 100), cv('a5', '2022-04-01', 100),
            cv('b1', '2026-01-10', 250), cv('d1', '2026-01-15', 200),
            cv('i126571067', '2026-02-20', 262)]


def planear(curvas, historico, ignorar=(), agora=AGORA):
    cands = moa.detetar(copy.deepcopy(curvas), set(ignorar), season, testes=TESTES)
    novas = moa.planear(cands, historico, agora, moa.limpar_curvas(copy.deepcopy(curvas), TESTES),
                        criterio={})
    return novas


def aplicar(historico, novas):
    return list(historico) + [dict(n) for n in novas]


def do_teste(historico, tid=TID):
    return [h for h in historico if h['test_id'] == tid]


def atual(historico, tid=TID):
    return moa.atuais(historico)[0] if tid is None else next(
        (r for r in moa.atuais(historico) if r['test_id'] == tid), None)


class IdempotenciaSemantica(unittest.TestCase):

    def setUp(self):
        self.curvas = base()
        self.v1 = planear(self.curvas, [])
        self.h1 = aplicar([], self.v1)
        self.linha1 = next(l for l in self.h1 if l['test_id'] == TID)

    # 1 ── sem mudanca semantica: idempotente
    def test_01_sem_mudanca_nao_cria_versao(self):
        self.assertEqual(planear(self.curvas, self.h1), [])
        self.assertEqual(planear(self.curvas, self.h1), [])        # e de novo, sem acumular

    # 1b ── variacao de n_pontos (nao semantica) nao gera versao
    def test_01b_n_pontos_nao_e_semantico(self):
        h = copy.deepcopy(self.h1)
        for r in h:
            if r['test_id'] == TID and r.get('referencias', {}).get('season'):
                r['referencias']['season']['n_pontos'] += 7
        self.assertEqual(planear(self.curvas, h), [])

    # 2 ── mudanca de referencia sazonal (passo 2 da Etapa 5): b1 deixa de contar
    def test_02_referencia_sazonal_alterada_gera_nova_versao(self):
        novas = planear(self.curvas, self.h1, ignorar={'b1'})
        n = [x for x in novas if x['test_id'] == TID]
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]['evento'], moa.EVENTO_DETETADA)
        self.assertEqual(n[0]['versao'], 2)
        self.assertEqual(n[0]['referencia_tipo'], 'season')
        self.assertEqual(n[0]['referencia_watts'], 200.0)
        self.assertAlmostEqual(n[0]['pct_da_referencia'], 131.0)

    # 3 ── mudanca de tipo de referencia: season -> all_time (i2977223 ignorada)
    def test_03_mudanca_de_tipo_gera_nova_versao(self):
        novas = planear(self.curvas, self.h1, ignorar={'i2977223'})
        n = [x for x in novas if x['test_id'] == TID]
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]['referencia_tipo'], 'all_time')
        self.assertEqual(n[0]['referencia_watts'], 250.0)

    # 4 ── mudanca da atividade de origem com o MESMO valor gera versao (proveniencia)
    def test_04_atividade_de_origem_diferente_com_mesmo_valor_gera_versao(self):
        curvas = [c for c in self.curvas if c['activity_id'] != 'b1'] + [cv('b1x', '2026-01-12', 250)]
        # b1 (250) ignorada; b1x com o mesmo valor, depois da mesma data. A referencia season
        # passa a ser b1x: mesmo valor, outra atividade de origem.
        novas = planear(curvas, self.h1, ignorar={'b1'})
        n = [x for x in novas if x['test_id'] == TID]
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]['referencia_watts'], 250.0)
        self.assertEqual(n[0]['referencias']['season']['referencia_activity_id'], 'b1x')

    # 5 ── contrato: anterior ao atual regrava; mesmo contrato nao; superior nao regrava para tras
    def test_05_contrato_anterior_regrava(self):
        h = copy.deepcopy(self.h1)
        for r in h:
            if r['test_id'] == TID:
                r['versao_contrato'] = moa.VERSAO_CRITERIO - 1
        n = [x for x in planear(self.curvas, h) if x['test_id'] == TID]
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]['versao_contrato'], moa.VERSAO_CRITERIO)

    def test_05b_mesmo_contrato_nao_regrava(self):
        self.assertEqual(self.linha1['versao_contrato'], moa.VERSAO_CRITERIO)
        self.assertEqual([x for x in planear(self.curvas, self.h1) if x['test_id'] == TID], [])

    def test_05c_contrato_superior_nao_regrava_para_tras(self):
        h = copy.deepcopy(self.h1)
        for r in h:
            if r['test_id'] == TID:
                r['versao_contrato'] = moa.VERSAO_CRITERIO + 1
        self.assertEqual([x for x in planear(self.curvas, h) if x['test_id'] == TID], [])

    # 6 ── limiar de 92%: referencia que nao qualifica nao e' escolhida nem gravada como detetada
    def test_06_limiar_92_respeitado_em_todas_as_versoes(self):
        # b1/d1 ignoradas: sem season; all-time 286 -> C = 262/286 = 91,6% (nao qualifica);
        # sem pontos na janela 'recente'. C nao e' detetada: a versao anterior sai como retirada.
        novas = planear(self.curvas, self.h1, ignorar={'b1', 'd1'})
        n = [x for x in novas if x['test_id'] == TID]
        self.assertTrue(all(x['evento'] == moa.EVENTO_RETIRADA for x in n),
                        f'nao deveria haver detetada abaixo do limiar: {n}')
        # e nenhuma versao detetada grava pct abaixo de 92 na referencia escolhida
        h = aplicar(self.h1, novas)
        for r in h:
            if r['evento'] == moa.EVENTO_DETETADA and r['referencia_tipo'] is not None:
                self.assertGreaterEqual(r['pct_da_referencia'], 92.0, r['test_id'])

    # 7 ── icu_ignore_power que altera a referencia nao deixa a observacao atual obsoleta
    def test_07_icu_ignore_altera_referencia_e_visivel_na_atual(self):
        h = aplicar(self.h1, planear(self.curvas, self.h1, ignorar={'b1'}))
        a = atual(h)
        self.assertEqual(a['referencia_watts'], 200.0)
        self.assertEqual(a['versao'], 2)

    # 8 ── historico intacto, substitui_versao correto, sem versoes duplicadas
    def test_08_historico_intacto_e_cadeia_coerente(self):
        antes = copy.deepcopy(self.h1)
        novas = planear(self.curvas, self.h1, ignorar={'b1'})
        h = aplicar(self.h1, novas)
        self.assertEqual(moa.verificar_cadeia(h), [])
        v1 = do_teste(h)[0]
        self.assertEqual(v1, antes[[r['test_id'] for r in antes].index(TID)])   # v1 nao mudou
        v2 = do_teste(h)[1]
        self.assertEqual(v2['versao'], 2)
        self.assertEqual(v2['substitui_versao'], 1)
        self.assertEqual(moa.estado_versao(v1, h), moa.ESTADO_SUBSTITUIDA)
        self.assertEqual(moa.estado_versao(v2, h), moa.ESTADO_ATUAL)
        # terceira chamada identica: nada novo
        self.assertEqual([x for x in planear(self.curvas, h, ignorar={'b1'}) if x['test_id'] == TID], [])


class MMP3Isolado(unittest.TestCase):
    """O caminho MMP3 (180 s) nao depende de planear nem da tabela de observacoes."""

    def test_mmp3_nao_chama_planear_nem_persiste(self):
        import modelo_mmp3
        with open(modelo_mmp3.__file__, encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('planear(', src)
        self.assertNotIn('observacoes_auto_historico', src)
        self.assertNotIn('inserir_observacao_auto', src)


if __name__ == '__main__':
    unittest.main()
