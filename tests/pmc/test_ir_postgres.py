"""PR #42 — fluxo transacional, historico de revisoes e migracoes num PostgreSQL TEMPORARIO.

Executar SO com uma base de teste dedicada (nunca producao):

    IR_PG_TEST_URL="postgresql://USER@/ir_tmp?host=/caminho/sock&port=PORTA" \\
        python -m pytest tests/pmc/test_ir_postgres.py

Protecoes: sem IR_PG_TEST_URL os testes sao ignorados; o nome da base tem de terminar em `_tmp` e o host tem de
ser um socket local (caminho absoluto); se o driver nao ficar em PostgreSQL os testes sao ignorados (nunca
se passa por SQLite). As tabelas do IR sao recriadas/limpas SO nessa base de teste.
"""
import os
import sys
import unittest
from datetime import datetime
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'ficticia')

_URL = os.environ.get('IR_PG_TEST_URL', '').strip()
_SEGURO = False
if _URL:
    _p = urlparse(_URL)
    _host = parse_qs(_p.query).get('host', [_p.hostname or ''])[0]
    _SEGURO = (_p.scheme.startswith('postgres') and (_p.path or '').rstrip('/').endswith('_tmp')
               and os.path.isabs(_host) and _host.startswith('/'))
    if _SEGURO:
        os.environ['DATABASE_URL'] = _URL
        os.environ.pop('SQLITE_PATH', None)

import unittest.mock  # noqa: E402,F401
import db  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402
from modelo_testes import ESTADO_CONFIRMADO  # noqa: E402

PULAR = ''
if not _URL:
    PULAR = 'IR_PG_TEST_URL nao definida: sem Postgres temporario, nao se valida o motor PostgreSQL'
elif not _SEGURO:
    PULAR = 'IR_PG_TEST_URL recusada: a base tem de se chamar *_tmp e o host ser um socket local absoluto'
elif db.DRIVER != 'postgres':
    # db.py le DATABASE_URL UMA vez, na importacao, e mantem um so processo de ligacao. Se outro modulo ja importou
    # db (execucao conjunta), o driver fica em SQLite e estes testes NAO correm. Correr isoladamente:
    # tests/pmc/executar_ir_postgres.sh
    PULAR = ('driver %s: db ja importado noutro modulo; PostgreSQL nao validado nesta execucao. '
             'Correr com tests/pmc/executar_ir_postgres.sh' % db.DRIVER)

TABELAS = ('modelo_mmp3_yes_revisoes', 'modelo_mmp3_yes_historico', 'modelo_testes_revisoes')


def rev(aid, n, watts, quando=None):
    return {'test_id': f'Bike:180:{aid}', 'revisao': n, 'evento': 'confirmacao' if n == 1 else 'reconfirmacao',
            'estado': ESTADO_CONFIRMADO, 'estado_anterior': None if n == 1 else ESTADO_CONFIRMADO,
            'modalidade': 'Bike', 'duracao_s': 180, 'activity_id': aid, 'data': '2026-06-10',
            'hora_local': None, 'watts': watts, 'pct_do_melhor': None, 'melhor_recente': None, 'deteccao': None,
            'elegibilidade': 'elegivel', 'motivo_elegibilidade': None, 'icu_ignore_power': 0,
            'aceita_elegibilidade_desconhecida': False, 'metodo_confirmacao': 'manual',
            'motivo': 'teste padronizado', 'responsavel': None, 'origem_registo': 'api', 'versao_contrato': 1,
            'registado_em': quando or datetime(2026, 6, 10 + n, 9, 0, 0),
            'revisado_em': quando or datetime(2026, 6, 10 + n, 9, 0, 0), 'confirmado_em': None}


def linha_yes(aid, n, watts):
    return {'test_id': f'Bike:180:{aid}', 'regra_versao': mm.VERSAO_REGRA_P0, 'activity_id': aid,
            'modalidade': 'Bike', 'data': '2026-06-10', 'inicio': None, 'watts': watts, 'season': '2026',
            'origem_classificacao': mm.ORIGEM_CONFIRMACAO, 'classe_intervalo': None,
            'motivo_decisao': f'confirmado manualmente (revisao {n})', 'referencia_watts': 300.0,
            'revisao': n, 'registado_em': datetime(2026, 6, 10 + n, 9, 0, 0)}


@unittest.skipIf(PULAR, PULAR)
class PostgresTemporario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_schema()

    def setUp(self):
        # limpeza SO da base de teste *_tmp (verificada acima)
        for t in TABELAS:
            db._exec(f'DELETE FROM {t}')

    def n(self, sql, *params):
        return db._exec(sql, params, fetch='one')[0]

    def yes_efetivo(self, aid):
        for r in db.yes_historico():
            if r['test_id'] == f'Bike:180:{aid}':
                return r
        return None

    # 1. revisao + YES na mesma transacao
    def test_revisao_e_yes_na_mesma_transacao(self):
        db.confirmar_revisao_e_yes(rev('a1', 1, 330.0), [linha_yes('a1', 1, 330.0)])
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_testes_revisoes WHERE test_id = ?', 'Bike:180:a1'), 1)
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_mmp3_yes_revisoes WHERE test_id = ?', 'Bike:180:a1'), 1)
        self.assertEqual(self.yes_efetivo('a1')['watts'], 330.0)

    # 2. rollback se uma escrita falhar
    def test_rollback_se_a_linha_yes_falhar(self):
        falha = linha_yes('a2', 1, None)                       # watts NOT NULL: a 2a escrita falha
        with self.assertRaises(Exception):
            db.confirmar_revisao_e_yes(rev('a2', 1, 330.0), [falha])
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_testes_revisoes WHERE test_id = ?', 'Bike:180:a2'), 0)
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_mmp3_yes_revisoes WHERE test_id = ?', 'Bike:180:a2'), 0)
        self.assertIsNone(self.yes_efetivo('a2'))

    # 3. repeticao idempotente
    def test_repeticao_e_idempotente(self):
        db.confirmar_revisao_e_yes(rev('a3', 1, 330.0), [linha_yes('a3', 1, 330.0)])
        with self.assertRaises(db.ConflitoRevisao):
            db.confirmar_revisao_e_yes(rev('a3', 1, 330.0), [linha_yes('a3', 1, 330.0)])
        self.assertEqual(db.gravar_yes_revisao([linha_yes('a3', 1, 330.0)], '2026-06-11T09:00:00'), 1)
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_testes_revisoes WHERE test_id = ?', 'Bike:180:a3'), 1)
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_mmp3_yes_revisoes WHERE test_id = ?', 'Bike:180:a3'), 1)

    # 4. reconfirmacao com novo valor
    def test_reconfirmacao_com_novo_valor_preserva_o_original(self):
        db.confirmar_revisao_e_yes(rev('a4', 1, 330.0), [linha_yes('a4', 1, 330.0)])
        db.confirmar_revisao_e_yes(rev('a4', 2, 335.0), [linha_yes('a4', 2, 335.0)])
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_testes_revisoes WHERE test_id = ?', 'Bike:180:a4'), 2)
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_mmp3_yes_revisoes WHERE test_id = ?', 'Bike:180:a4'), 2)
        original = db._exec('SELECT watts FROM modelo_mmp3_yes_revisoes WHERE test_id = ? AND revisao = 1',
                            ('Bike:180:a4',), fetch='one')[0]
        self.assertEqual(original, 330.0)                    # o YES original nao e reescrito

    # 5. P0 usa a revisao efetiva mais recente
    def test_p0_usa_a_revisao_efetiva_mais_recente(self):
        db.confirmar_revisao_e_yes(rev('a5', 1, 330.0), [linha_yes('a5', 1, 330.0)])
        db.confirmar_revisao_e_yes(rev('a5', 2, 335.0), [linha_yes('a5', 2, 335.0)])
        self.assertEqual(self.yes_efetivo('a5')['watts'], 335.0)
        p0 = mm.p0_global({'Bike': []}, historico_yes=db.yes_historico())
        self.assertEqual(p0['estado'], 'valida')
        self.assertEqual(p0['registo']['watts'], 335.0)

    # 6. inicializacao repetida e migracao, preservando o historico
    def test_inicializacao_repetida_preserva_historico(self):
        db.confirmar_revisao_e_yes(rev('a6', 1, 330.0), [linha_yes('a6', 1, 330.0)])
        db.init_schema()
        db.init_schema()
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_testes_revisoes WHERE test_id = ?', 'Bike:180:a6'), 1)
        self.assertEqual(self.yes_efetivo('a6')['watts'], 330.0)

    def test_migracao_recria_tabela_nova_e_mantem_yes_legado(self):
        db.gravar_yes_historico([{'test_id': 'Bike:180:legado', 'regra_versao': mm.VERSAO_REGRA_P0,
                                  'activity_id': 'legado', 'modalidade': 'Bike', 'data': '2025-06-10',
                                  'watts': 280.0}], '2025-06-10T08:00:00')
        db._exec('DROP TABLE modelo_mmp3_yes_revisoes')      # base anterior ao PR
        db.init_schema()
        self.assertEqual(self.n('SELECT COUNT(*) FROM modelo_mmp3_yes_revisoes'), 0)
        legado = self.yes_efetivo('legado')
        self.assertEqual((legado['watts'], legado['revisao']), (280.0, 0))

    # 7. sincronizacao: upsert sem duplicar, tags alteradas chegam a base
    def test_upsert_atividade_nao_duplica_e_atualiza_tags(self):
        base = {'id': 'pg-act-1', 'date': '2026-06-10', 'start_local': '2026-06-10T07:30:00', 'type': 'Bike',
                'raw': {'id': 'pg-act-1', 'tags': []}}
        db.upsert_activities([base])
        db.upsert_activities([dict(base, raw={'id': 'pg-act-1', 'tags': ['3min']})])
        self.assertEqual(self.n('SELECT COUNT(*) FROM activities WHERE id = ?', 'pg-act-1'), 1)
        raw = [a for a in db.load_activities() if a.get('id') == 'pg-act-1'][0]
        self.assertEqual(raw['tags'], ['3min'])


if __name__ == '__main__':
    unittest.main()
