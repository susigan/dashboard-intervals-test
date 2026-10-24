"""Etapa 11: walk-forward ponta a ponta com o historico temporal (modelo_ir_temporal) ligado ao avaliador.

TODOS os dados sao SINTETICOS (cenario do assento de teste; YES, marcos, valores y e revisoes construidos aqui).
Nao e validacao historica real do atleta. Demonstra: (a) cada historico temporal e usado no instante certo;
(b) rejeicao explicita quando falta historico; (c) ausencia de fuga temporal: alterar YES, marcos, valores y e
revisoes posteriores a um corte nao muda previsoes nem P0 de alvos anteriores.

SQLite: observacoes e registo temporal numa base temporaria. PostgreSQL: o registo temporal em IR_TESTE_PG_DSN
(base *_tmp, driver psycopg v3); as observacoes continuam em SQLite.
"""
import copy
import os
import re
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import test_modelo_ir_servico as TS  # noqa: E402
import test_ir_validacao_p0 as V0  # noqa: E402  (yes() e P0_REAL, capturado antes do assento)
import db  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_ir_temporal as tmp  # noqa: E402
import modelo_ir_temporal_migracao as mig  # noqa: E402
import modelo_ir_curvas as mcurv  # noqa: E402
import modelo_ir_curvas_migracao as mcmig  # noqa: E402
import modelo_testes as mt  # noqa: E402
from test_ir_migracao import _executor_psycopg3  # noqa: E402

PG_DSN = os.environ.get('IR_TESTE_PG_DSN')
MARCOS_1 = [('2025-01-01', 'S25')]
MARCOS_2 = [('2025-01-01', 'S25'), ('2025-06-01', 'S25b')]


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


def dia_mais(d, n):
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def revisao_rejeicao(test_id, revisado_em):
    """Revisao manual SINTETICA (rejeicao) no formato que estado_atual_por_teste valida."""
    aid = test_id.split(':')[-1]
    return {'test_id': test_id, 'revisao': 1, 'evento': mt.EVENTO_REJEICAO, 'estado': mt.ESTADO_REJEITADO,
            'estado_anterior': None, 'modalidade': 'Bike', 'duracao_s': 1200, 'activity_id': aid,
            'revisado_em': revisado_em}


class CenarioTemporal(TS._Base):
    """Cenario sintetico: YES, marcos e transicoes conhecidos em instantes controlados."""

    LEDGER_PG = False

    def setUp(self):
        super().setUp()
        self._efeitos = [V0.yes(f'Bike:1200:{c["activity_id"]}', c['date'], float(c['watts'][-1]))
                         for c in self.CURVAS]
        p = mock.patch.object(mis, 'entradas_mmp3', side_effect=self._entradas)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(mis, 'p0_global_das_entradas', new=V0.P0_REAL)
        p.start(); self.addCleanup(p.stop)
        self._preparar_ledger()

    def _preparar_ledger(self):
        if self.LEDGER_PG:
            loja = _executor_psycopg3(PG_DSN)
            for t in (tmp.TAB_MARCOS, tmp.TAB_YES, mig.REGISTO):
                loja._exec(f'DROP TABLE IF EXISTS {t}', (), None)
            self.ledger = tmp.RegistoTemporal(loja._exec, loja.dialeto)
        else:
            self.ledger = tmp.RegistoTemporal(db._exec, db.DRIVER)
        self.assertEqual(mig.aplicar(self.ledger._exec, self.ledger.dialeto, '2026-10-23T10:00:00'), 'aplicada')

    def _entradas(self, curvas, atividades, ate, marcos=None, revisoes=None):
        # Sem marcos conhecidos nao ha esforcos elegiveis: a season depende do calendario.
        vazio = {'Row': ([], []), 'Ski': ([], []), 'Run': ([], [])}
        if marcos is None:
            return {'Bike': ([], []), **vazio}
        ativos = [copy.deepcopy(e) for e in self._efeitos if e['data'] <= ate]
        return {'Bike': ([], ativos), **vazio}

    # ── preparacao ─────────────────────────────────────────────────────────────

    def preparar_historico(self):
        """Deteccao conhecida no dia do esforco (T12:00). Nao se reconstroem datas passadas.
        Curvas: o historico verificavel (ir_curvas_versoes) recebe a versao de cada curva no instante de TESTE
        2024-12-31, anterior a todos os alvos. Isto e um relogio injetado no teste (cenario sintetico), nao um
        registo de producao. Nao se usa updated_at: nao prova nada."""
        self.prepara()
        db._exec("UPDATE modelo_observacoes_auto SET detetado_em = data || 'T12:00:00' "
                 "WHERE modalidade = 'Bike' AND duracao_s = 1200", (), None)
        self.assertEqual(mcmig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00'), 'aplicada')
        self.hc = mcurv.HistoricoCurvas(db._exec, db.DRIVER)
        self.hc.registar(db.load_power_curves() or [], '2024-12-31T00:00:00')

    def registar_marcos(self, marco_1_em, marco_2_em=None):
        self.ledger.registar_marcos(MARCOS_1, 'teste', marco_1_em + 'T10:00:00')
        if marco_2_em:
            self.ledger.registar_marcos(MARCOS_2, 'teste', marco_2_em + 'T10:00:00')

    def registar_yes(self, ate_data, instante):
        """Transicoes de YES: estado dos esforcos com data <= ate_data, gravado no instante dado."""
        efet = {e['test_id']: {'modalidade': 'Bike', 'activity_id': e['activity_id'], 'watts': e['watts'],
                               'regra_versao': 1, 'origem': 'automatico', 'revisao': None}
                for e in self._efeitos if e['data'] <= ate_data}
        return self.ledger.registar_transicoes_yes(efet, instante)

    def registar_yes_dia_a_dia(self):
        for d in sorted({e['data'] for e in self._efeitos}):
            self.registar_yes(d, d + 'T12:00:00')

    def val(self, revisoes=None):
        revs = db.revisoes_testes_modelo() if revisoes is None else revisoes
        return mis.validar_preditivo(db.observacoes_auto_historico(), revs, self.ativ,
                                     db.load_power_curves() or [], modos=('conhecido_na_epoca',), tipo='Bike',
                                     duracao=1200, temporal=self.ledger,
                                     historico_curvas=getattr(self, 'hc', None))


class WalkForwardTemporal(CenarioTemporal):

    def test_sem_marcos_registados_todos_os_alvos_sao_rejeitados_com_motivo(self):
        self.preparar_historico()
        res = self.val()
        it = res['itens'][0]
        self.assertEqual(it['n_alvos_com_previsao_ir'], 0)
        # o unico alvo sem motivo de historico temporal e o primeiro da serie: sem nenhum dia anterior (sem_historico)
        self.assertEqual(sum(it['alvos_sem_previsao_ir'].values()), it['n_alvos'])
        self.assertEqual(it['alvos_sem_previsao_ir'].get('marcos_sem_registo_antes_do_alvo'), it['n_alvos'] - 1)
        self.assertEqual(it['alvos_sem_previsao_ir'].get('sem_historico'), 1)
        self.assertEqual(res['historico_temporal'], 'registo_prospetivo')

    def test_alvos_antes_do_marco_rejeitados_e_depois_previstos_com_yes_conhecido(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        marco = dia_mais(primeiro, 60)
        self.registar_marcos(marco)
        self.registar_yes_dia_a_dia()
        it = self.val()['itens'][0]
        self.assertGreater(it['n_alvos_com_previsao_ir'], 0, str(it['alvos_sem_previsao_ir']))
        self.assertTrue(all(p['data'] >= marco for p in it['previsoes']))
        self.assertGreater(it['alvos_sem_previsao_ir'].get('marcos_sem_registo_antes_do_alvo', 0), 0)

    def test_sem_yes_conhecido_antes_do_alvo_e_rejeicao_explicita(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 1))            # marcos existem, mas nenhum YES registado
        it = self.val()['itens'][0]
        self.assertEqual(it['n_alvos_com_previsao_ir'], 0)
        self.assertEqual(sum(it['alvos_sem_previsao_ir'].values()), it['n_alvos'])
        self.assertEqual(it['alvos_sem_previsao_ir'].get('yes_sem_registo_antes_do_alvo'), it['n_alvos'] - 1)
        self.assertEqual(set(it['alvos_sem_previsao_ir']) - {'yes_sem_registo_antes_do_alvo', 'sem_historico'}, set())

    def test_marcos_indisponiveis_nao_sao_substituidos_por_presuncao(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.ledger.registar_marcos(None, 'mes', dia_mais(primeiro, 30) + 'T10:00:00')
        self.registar_yes_dia_a_dia()
        it = self.val()['itens'][0]
        self.assertEqual(it['n_alvos_com_previsao_ir'], 0)
        self.assertIn('marcos_indisponiveis_antes_do_alvo', it['alvos_sem_previsao_ir'])

    def test_retrospectivo_omitido_com_nota_quando_ha_registo(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 1))
        res = mis.validar_preditivo(db.observacoes_auto_historico(), db.revisoes_testes_modelo(), self.ativ,
                                    db.load_power_curves() or [], modos=('retrospectivo', 'conhecido_na_epoca'),
                                    tipo='Bike', duracao=1200, temporal=self.ledger)
        self.assertEqual({i['modo'] for i in res['itens']}, {'conhecido_na_epoca'})
        self.assertEqual(res['modos_omitidos'],
                         {'omitidos': ['retrospectivo'], 'motivo': 'retrospectivo_sem_marcos_nem_yes_conhecidos'})

    def test_sem_registo_temporal_comportamento_legado_inalterado(self):
        self.preparar_historico()
        res = mis.validar_preditivo(db.observacoes_auto_historico(), db.revisoes_testes_modelo(), self.ativ,
                                    db.load_power_curves() or [], modos=('conhecido_na_epoca',), tipo='Bike',
                                    duracao=1200, marcos=None)
        self.assertEqual(res['historico_temporal'], 'reconstrucao_por_deteccao')
        self.assertIsNone(res['modos_omitidos'])

    def test_alterar_futuro_nao_muda_previsoes_nem_p0_passados(self):
        """Ausencia de fuga temporal: YES, marcos, valores y e revisoes posteriores ao corte."""
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60), marco_2_em=dia_mais(primeiro, 200))
        self.registar_yes_dia_a_dia()
        antes = self.val()
        it_a = antes['itens'][0]
        datas = sorted(p['data'] for p in it_a['previsoes'])
        self.assertGreater(len(datas), 4, 'cenario sintetico sem alvos suficientes')
        corte = datas[len(datas) // 2]
        # a previsao IR depende do P0 e dos dados passados: IR identico nos alvos anteriores implica P0 identico
        prev_antes = {p['test_id']: (p['data'], p['ir'], None) for p in it_a['previsoes']}
        rej_antes = {(r['test_id'], r['data']): r['motivo'] for r in it_a['alvos_rejeitados'] if r['data'] < corte}

        # 1. YES: esforcos posteriores ao corte mudam de valor; a transicao fica com instante posterior ao corte
        for e in self._efeitos:
            if e['data'] >= corte:
                e['watts'] = e['watts'] * 2 + 500
        self.assertGreater(self.registar_yes(max(e['data'] for e in self._efeitos), dia_mais(corte, 3) + 'T18:00:00'), 0)
        # 2. marcos: nova versao conhecida depois do corte
        self.ledger.registar_marcos(MARCOS_2 + [('2026-01-01', 'S26')], 'teste', dia_mais(corte, 5) + 'T10:00:00')
        # 3. valores y: nova versao de observacoes posteriores ao corte
        futuras = [h for h in db.observacoes_auto_historico()
                   if h['modalidade'] == 'Bike' and h['duracao_s'] == 1200 and str(h['data']) >= corte][:3]
        for h in futuras:
            db.inserir_observacao_auto(dict(h, versao=h['versao'] + 1, watts=float(h['watts']) + 40.0,
                                            detetado_em=str(h['data']) + 'T23:00:00',
                                            substitui_versao=h['versao']))
        # 4. revisoes: rejeicao manual de um YES posterior ao corte
        futuro_tid = next(e['test_id'] for e in self._efeitos if e['data'] >= corte)
        revs = db.revisoes_testes_modelo() + [revisao_rejeicao(futuro_tid, dia_mais(corte, 2) + 'T09:00:00')]

        depois = self.val(revisoes=revs)
        it_d = depois['itens'][0]
        prev_depois = {p['test_id']: (p['data'], p['ir'], None) for p in it_d['previsoes']}
        passados = [t for t, (d, _, _) in prev_antes.items() if d < corte]
        self.assertTrue(passados, 'nenhum alvo anterior ao corte para comparar')
        for t in passados:
            self.assertEqual(prev_depois.get(t), prev_antes[t], t)
        rej_depois = {(r['test_id'], r['data']): r['motivo'] for r in it_d['alvos_rejeitados'] if r['data'] < corte}
        self.assertEqual(rej_depois, rej_antes)
        # o teste nao e vazio: a alteracao futura mudou alvos posteriores ao corte (previsao ou motivo)
        mudaram = [t for t, (d, ir, _) in prev_antes.items() if d >= corte and prev_depois.get(t) != (d, ir, _)]
        mudaram += [k for k in {(r['test_id'], r['data']) for r in it_d['alvos_rejeitados']}
                    if k[1] >= corte and k not in {(r['test_id'], r['data']) for r in it_a['alvos_rejeitados']}]
        self.assertTrue(mudaram, 'a alteracao futura nao afetou nenhum alvo posterior: teste vazio')


@unittest.skipUnless(PG_DSN and re.search(r'dbname=\S*_tmp\b', PG_DSN),
                     'IR_TESTE_PG_DSN com base *_tmp nao definido: PostgreSQL nao testado')
class WalkForwardTemporalPostgres(WalkForwardTemporal):
    """Mesmo cenario com o registo temporal em PostgreSQL (driver psycopg v3). Observacoes continuam em SQLite."""
    LEDGER_PG = True


if __name__ == '__main__':
    unittest.main()
