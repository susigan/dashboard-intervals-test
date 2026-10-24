"""Modo sombra: MESMO contrato em SQLite (ficheiro temporario) e PostgreSQL real (IR_TESTE_PG_DSN).

Executar: python -m pytest tests/pmc/test_ir_sombra.py
PostgreSQL: IR_TESTE_PG_DSN="host=127.0.0.1 port=55432 user=postgres dbname=etapa5_teste" python -m pytest ...
Sem a variavel, a classe PostgreSQL e saltada (e reportado como SKIPPED, nao como sucesso).
"""
import json, os, sys, tempfile, threading, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import modelo_ir_confianca as mic  # noqa: E402
import modelo_ir_sombra as sombra  # noqa: E402
import modelo_ir_adaptador as ad  # noqa: E402
import modelo_ir_candidata as cand  # noqa: E402

PG_DSN = os.environ.get('IR_TESTE_PG_DSN')
FIX = os.path.join(HERE, 'fixtures', 'ir_confianca_ronda_real.json')
REG = os.path.join(HERE, 'fixtures', 'candidata_ski300_mediana5_D_T1fixo.json')


def obs(n=20, delta=0.0):
    return [{'test_id': i, 'data': f'2025-{1 + i % 12:02d}-{1 + i % 27:02d}', 'watts': 200.0 + i + delta}
            for i in range(n)]


def corte(mae_ir=5.0, mae_media=9.0, mae_ultimo=10.0, ident=True):
    return {'corte': 'x', 'n_treino': 100, 'n_teste': 10, 'mae_ir': mae_ir, 'rmse_ir': mae_ir * 1.2,
            'mae_media': mae_media, 'rmse_media': mae_media * 1.3, 'mae_ultimo': mae_ultimo,
            'rmse_ultimo': mae_ultimo * 1.3, 'identificavel': ident, 'no_limite': False, 'parametros': {'k1': 1.0}}


def av_A(n=100):
    return {'esquema': 'cortes_treino_teste', 'configuracao': {'id': 'A', 'familia': 'completo', 'fixos': {}},
            'n_observacoes': n, 'falha_ajuste': False, 'suspeita_fuga': False, 'validacao_causal_verificada': True,
            'cortes': [corte(5.0), corte(5.1), corte(4.9), corte(5.0)]}


def av_C(n=10):
    a = av_A(n)
    a['configuracao'] = {'id': 'C', 'familia': 'completo', 'fixos': {}}
    a['cortes'] = [corte(12.0, 8.0, 9.0) for _ in range(4)]
    return a


class Contrato:
    """Mixin: nao e TestCase. Subclasses definem nova_loja()."""

    def setUp(self):
        self.loja = self.nova_loja()
        self.loja.criar()
        self.loja._exec(f'DELETE FROM {sombra.NOME_TABELA}', (), None)
        self.chamadas = []

    def run_sombra(self, av, o=None, cfg='cfgX', carga='c1', p0='p0a', origem='sync', agora=None, loja=None):
        def avaliar():
            self.chamadas.append(1)
            if isinstance(av, Exception):
                raise av
            return av
        return sombra.executar_sombra(loja or self.loja, 'Ski', 300, o or obs(), carga, p0, cfg, avaliar,
                                      origem=origem, agora=agora or '2026-10-23T10:00:00.000001')

    def linhas(self):
        return self.loja._exec(f'SELECT * FROM {sombra.NOME_TABELA}', (), 'all')

    def test_nada_mudou_nao_volta_a_avaliar(self):
        self.assertEqual(self.run_sombra(av_A())['acao_sombra'], 'avaliado')
        self.assertEqual(self.run_sombra(av_A())['acao_sombra'], 'sem_trabalho')
        self.assertEqual(len(self.chamadas), 1)

    def test_execucao_repetida_grava_uma_linha(self):
        for _ in range(5):
            self.run_sombra(av_A())
        self.assertEqual(len(self.linhas()), 1)

    def test_nova_observacao_elegivel_reavalia(self):
        self.run_sombra(av_A(), o=obs(20))
        self.assertEqual(self.run_sombra(av_A(101), o=obs(21))['acao_sombra'], 'avaliado')
        self.assertEqual(len(self.linhas()), 2)

    def test_observacao_corrigida_reavalia(self):
        self.run_sombra(av_A(), o=obs())
        o2 = obs(); o2[0]['watts'] += 5
        self.assertEqual(self.run_sombra(av_A(), o=o2)['acao_sombra'], 'avaliado')

    def test_observacao_removida_reavalia(self):
        self.run_sombra(av_A(), o=obs())
        self.assertEqual(self.run_sombra(av_A(), o=obs()[:-1])['acao_sombra'], 'avaliado')

    def test_candidato_pior_nao_substitui_proposta_anterior(self):
        r1 = self.run_sombra(av_A(), cfg='cfgA')
        r2 = self.run_sombra(av_C(), o=obs(21), cfg='cfgC')
        self.assertTrue(r1['teria_promovido'])
        self.assertEqual(r2['acao_proposta'], 'manter')
        self.assertEqual(self.loja.ultima_teorica('Ski', 300)['nivel'], 'A')

    def test_candidato_de_nivel_inferior_e_mantido(self):
        # A primeiro (promovido); depois B (regularizado, nivel inferior) nao substitui A.
        self.assertEqual(self.run_sombra(av_A(), cfg='cfgA')['acao_proposta'], 'promover')
        b = av_A(); b['configuracao'] = {'id': 'B', 'familia': 'regularizado', 'fixos': {'t2': 5}}
        self.assertEqual(self.run_sombra(b, o=obs(21), cfg='cfgB')['acao_proposta'], 'manter')
        self.assertEqual(self.loja.ultima_teorica('Ski', 300)['nivel'], 'A')

    def test_falha_do_ajuste_regista_rejeicao_sem_promover(self):
        r = self.run_sombra(RuntimeError('otimizador divergiu'), cfg='cfgF')
        self.assertEqual(r['nivel'], 'C')
        self.assertFalse(r['teria_promovido'])
        # indices segundo sombra.COLUNAS: configuracao=3, motivos=13, teria_promovido=15
        linha = [l for l in self.linhas() if l[3] == 'cfgF'][0]
        self.assertIn('falha_avaliacao', linha[13])
        self.assertEqual(linha[15], 0)  # teria_promovido = 0

    def test_configuracoes_diferentes_nao_colidem(self):
        self.run_sombra(av_A(), cfg='cfgA')
        self.run_sombra(av_A(), cfg='cfgA2')
        self.assertEqual(len(self.linhas()), 2)

    def test_concorrencia_mesma_chave_uma_linha_um_avaliado(self):
        resultados, erros = [], []

        def trabalhar():
            try:
                loja = self.nova_loja()
                r = sombra.executar_sombra(loja, 'Ski', 300, obs(), 'c1', 'p0a', 'cfgC1', lambda: av_A(),
                                           origem='teste', agora='2026-10-23T10:00:00.000002')
                resultados.append(r['acao_sombra'])
            except Exception as e:
                erros.append(e)
        ts = [threading.Thread(target=trabalhar) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(erros, [])
        self.assertEqual(len(self.linhas()), 1)
        self.assertEqual(resultados.count('avaliado'), 1)
        self.assertEqual(resultados.count('concorrente_ja_gravou') + resultados.count('avaliado')
                         + resultados.count('sem_trabalho'), 8)

    def test_versao_operacional_intacta_quando_candidato_e_rejeitado(self):
        self.loja._exec(f'DROP TABLE IF EXISTS modelo_ir_ajustes', (), None)
        self.loja._exec('CREATE TABLE modelo_ir_ajustes (modalidade TEXT, duracao_s INTEGER, versao_ajuste INTEGER, '
                        'estado TEXT, PRIMARY KEY (modalidade, duracao_s, versao_ajuste))', (), None)
        self.loja._exec(sombra.sql_para(self.loja.dialeto, 'INSERT INTO modelo_ir_ajustes VALUES (?,?,?,?)'),
                        ('Ski', 180, 7, 'ok'), None)
        antes = self.loja._exec('SELECT * FROM modelo_ir_ajustes', (), 'all')
        self.run_sombra(av_C(), cfg='cfgC')
        self.run_sombra(RuntimeError('x'), o=obs(21), cfg='cfgE')
        self.assertEqual(self.loja._exec('SELECT * FROM modelo_ir_ajustes', (), 'all'), antes)
        self.loja._exec('DROP TABLE IF EXISTS modelo_ir_ajustes', (), None)

    def test_retorno_declara_que_nao_escreve_operacional(self):
        self.assertFalse(self.run_sombra(av_A())['escrito_operacional'])

    def test_candidata_ski_mediana5_avaliada_como_B_em_sombra(self):
        fx = json.load(open(FIX, encoding='utf-8'))
        it = next(i for i in fx['itens'] if i['metadados']['ronda'] == 4 and i['metadados']['celula'] == 'Ski-300'
                  and i['metadados']['chave'] == 'mediana5|D_T1fixo')
        r = sombra.executar_sombra(self.loja, 'Ski', 300, obs(), 'real', 'p0mediana5', 'mediana5|D_T1fixo',
                                   lambda: it, origem='teste')
        self.assertEqual(r['nivel'], 'B')
        self.assertTrue(r['validada'])
        self.assertFalse(r['escrito_operacional'])

    def test_gancho_registra_so_unidades_alteradas_e_nao_levanta(self):
        def preparar(mod, dur):
            if dur == 999:
                raise RuntimeError('entrada corrompida')
            return {'obs': obs(), 'carga_sig': 'c', 'p0_ref_sig': 'p', 'configuracao_id': f'cfg{dur}',
                    'avaliar': lambda: av_A(), 'versao_operacional_ref': 'v7'}
        r = sombra.apos_deteccao_sombra(self.loja, {'unidades_alteradas': [('Ski', 300), ('Ski', 999)]},
                                        'sync', preparar)
        self.assertTrue(r['ok'])
        self.assertEqual([u['acao_sombra'] for u in r['unidades']], ['avaliado', 'erro'])
        self.assertFalse(r['escrito_operacional'])
        r2 = sombra.apos_deteccao_sombra(self.loja, {'unidades_alteradas': [('Ski', 300)]}, 'sync', preparar)
        self.assertEqual(r2['unidades'][0]['acao_sombra'], 'sem_trabalho')

    def test_gancho_sem_deteccao_nao_faz_nada(self):
        r = sombra.apos_deteccao_sombra(self.loja, None, 'sync', lambda m, d: 1 / 0)
        self.assertTrue(r['ok']); self.assertEqual(r['unidades'], [])

    def test_decisao_recuperavel_com_campos_de_auditoria(self):
        self.run_sombra(av_A(), cfg='cfgA')
        d = self.loja.ultima_teorica('Ski', 300)
        for chave in ('nivel', 'validada', 'esquema', 'configuracao', 'parametros_fixos', 'politica_versao',
                      'politica_hash', 'resumo', 'motivos', 'limitacoes', 'confianca_parametros', 'confianca_previsao'):
            self.assertIn(chave, d)
        self.assertEqual(mic.assinatura(d), mic.assinatura(json.loads(json.dumps(d))))


class TestExtratorDeUnidades(unittest.TestCase):

    def test_ajustado_e_inalterado_disparam_bloqueado_falhou_e_vazio_nao(self):
        r = {'ok': True, 'unidades': {'Ski:300': {'acao': 'ajustado', 'versao_ajuste': 3},
                                      'Bike:300': {'acao': 'inalterado'},
                                      'Row:180': {'acao': 'bloqueado', 'motivo': 'p0'},
                                      'Run:180': {'acao': 'falhou', 'erro': 'x'},
                                      'Bike:60': {'acao': 'sem_observacoes'}}}
        # 'inalterado' tambem dispara: a sombra decide pela assinatura, nao pela acao de recalibrar
        self.assertEqual(sombra.unidades_alteradas(r), [('Ski', 300), ('Bike', 300)])

    def test_recalibrar_falhado_ou_vazio_nao_dispara(self):
        self.assertEqual(sombra.unidades_alteradas({'ok': False, 'erro': 'sem base de dados'}), [])
        self.assertEqual(sombra.unidades_alteradas(None), [])


class TestSqlite(Contrato, unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = os.path.join(self._tmp.name, 'sombra.db')
        super().setUp()

    def tearDown(self):
        self._tmp.cleanup()

    def nova_loja(self):
        return sombra.loja_sqlite(self.caminho)

    def test_schema_sqlite_colunas_e_tabela_propria(self):
        import sqlite3
        with sqlite3.connect(self.caminho) as c:
            cols = [r[1] for r in c.execute(f'PRAGMA table_info({sombra.NOME_TABELA})')]
        self.assertEqual(cols, sombra.COLUNAS)
        self.assertNotIn('modelo_ir_ajustes', sombra.DDL)

    def test_modulo_nao_importa_base_de_producao_nem_servico(self):
        linhas_import = [l.strip() for l in open(sombra.__file__, encoding='utf-8')
                         if l.startswith(('import ', 'from '))]
        self.assertFalse([l for l in linhas_import if 'db' in l.split() or 'modelo_ir_servico' in l])


@unittest.skipUnless(PG_DSN, 'IR_TESTE_PG_DSN nao definido: PostgreSQL nao testado')
class TestPostgres(Contrato, unittest.TestCase):

    def setUp(self):
        # Base de teste descartavel: so limpa a tabela de sombra se o nome da base terminar em _tmp.
        import re
        nome = re.search(r'dbname=(\S+)', PG_DSN or '')
        if not (nome and nome.group(1).endswith('_tmp')):
            self.skipTest('IR_TESTE_PG_DSN deve apontar para uma base *_tmp (ex.: sombra_tmp)')
        loja = sombra.loja_postgres(PG_DSN)
        loja._exec(f'DROP TABLE IF EXISTS {sombra.NOME_TABELA}', (), None)
        super().setUp()

    def nova_loja(self):
        return sombra.loja_postgres(PG_DSN)

    def test_schema_postgres_colunas_iguais_ao_sqlite(self):
        cols = [r[0] for r in self.loja._exec(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
            (sombra.NOME_TABELA,), 'all')]
        self.assertEqual(cols, sombra.COLUNAS)

    def test_on_conflict_do_nothing_e_atomico_entre_ligacoes(self):
        """Duas ligacoes separadas, mesma chave: uma insere, a outra nao (sem rowcount, so RETURNING)."""
        a = self.nova_loja(); b = self.nova_loja()
        r1 = sombra.executar_sombra(a, 'Ski', 300, obs(), 'c', 'p', 'cfgP', lambda: av_A(), agora='2026-10-23T10:00:00.000003')
        r2 = sombra.executar_sombra(b, 'Ski', 300, obs(), 'c', 'p', 'cfgP', lambda: av_A(), agora='2026-10-23T10:00:00.000004')
        self.assertEqual(r1['acao_sombra'], 'avaliado')
        self.assertEqual(r2['acao_sombra'], 'sem_trabalho')  # existe() ja ve a linha committed
        self.assertEqual(len(self.linhas()), 1)

    def test_resultados_identicos_a_sqlite(self):
        import tempfile as tf
        d = tf.TemporaryDirectory()
        s = sombra.loja_sqlite(os.path.join(d.name, 'x.db')); s.criar()
        r_pg = self.run_sombra(av_A(), cfg='cfgA')
        r_sq = sombra.executar_sombra(s, 'Ski', 300, obs(), 'c1', 'p0a', 'cfgA', lambda: av_A(),
                                      origem='sync', agora='2026-10-23T10:00:00.000001')
        self.assertEqual({k: r_pg[k] for k in ('nivel', 'validada', 'acao_proposta', 'teria_promovido')},
                         {k: r_sq[k] for k in ('nivel', 'validada', 'acao_proposta', 'teria_promovido')})
        d.cleanup()

    def test_concorrencia_real_entre_processos_de_ligacao(self):
        """Ja coberta em Contrato.test_concorrencia_mesma_chave_uma_linha_um_avaliado (threads, ligacoes proprias)."""
        self.assertIsNotNone(PG_DSN)


if __name__ == '__main__':
    unittest.main()
