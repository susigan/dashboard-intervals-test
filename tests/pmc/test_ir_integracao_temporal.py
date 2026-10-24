"""Etapa 12: historico temporal ligado a SOMBRA, ao endpoint legado e a contagem de rejeicoes por categoria.

TODOS os dados sao SINTETICOS (cenario do walk-forward temporal, test_ir_walkforward_temporal). Nao e validacao
historica do atleta. Cobre: sombra com registo temporal; migracao ausente sem fallback; endpoint a rotular a natureza
de cada avaliacao; curvas sem prova temporal; discordancia P0/YES real; versao do alvo; rejeicoes por categoria;
falhas da sombra sem afetar a sincronizacao; sem escrita no operacional; sem fuga temporal.

SQLite: tudo. PostgreSQL (psycopg v3, IR_TESTE_PG_DSN): so os testes que dependem do registo temporal.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import test_modelo_ir_servico as TS  # noqa: E402
import test_ir_walkforward_temporal as W  # noqa: E402  (CenarioTemporal, dia_mais)
from test_ir_walkforward_temporal import dia_mais  # noqa: E402
import db  # noqa: E402
import modelo_ir_confianca_migracao as mig  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_ir_sombra as sombra  # noqa: E402
import modelo_ir_sombra_integracao as msi  # noqa: E402
import modelo_ir_temporal as tmp  # noqa: E402
import modelo_ir_temporal_migracao as tmig  # noqa: E402

PG_DSN = os.environ.get('IR_TESTE_PG_DSN')
HORA_CURVAS_ANTIGAS = '2024-12-31T00:00:00'     # curvas gravadas antes de qualquer alvo do cenario
HORA_FUTURA = '2026-10-22T10:00:00'            # regravacao posterior a todos os alvos do cenario
RES_RECAL = {'ok': True, 'p0_global': {'Bike': {'p0': 290.0}},
             'unidades': {'Bike:1200': {'acao': 'ajustado', 'versao_ajuste': 1}}}


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


def curvas_gravadas_em(caso, instante):
    """Cenario de TESTE: o historico de curvas passa a ter a PRIMEIRA versao de cada curva em `instante` (relogio
    injetado). Apaga so o historico desta base de teste (nunca producao) e regista de novo."""
    db._exec('DELETE FROM ir_curvas_versoes', (), None)
    caso.hc.registar(db.load_power_curves() or [], instante)


def _loja_temporaria(tmpdir):
    loja = sombra.loja_sqlite(os.path.join(tmpdir, 'sombra.db'))
    loja.criar()
    return loja


# ── unidades puras (sem base de dados) ─────────────────────────────────────────

class NormalizacaoDeInstantes(unittest.TestCase):

    def test_texto_iso_e_datetime_ficam_iguais(self):
        self.assertEqual(tmp.como_datetime('2025-01-02T03:04:05.000006'),
                         tmp.como_datetime(datetime(2025, 1, 2, 3, 4, 5, 6)))

    def test_tz_aware_converte_para_utc_naive(self):
        d = datetime(2025, 1, 2, 5, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(tmp.como_datetime(d), datetime(2025, 1, 2, 3, 0, 0))

    def test_valor_ausente_ou_ilegivel_nao_e_provado(self):
        self.assertIsNone(tmp.como_datetime(None))
        self.assertIsNone(tmp.como_datetime(''))
        self.assertIsNone(tmp.como_datetime('nao-e-data'))


class CategoriasDeRejeicao(unittest.TestCase):

    def test_cada_motivo_conhecido_tem_categoria(self):
        for m in (mis.M_VERSAO_ALVO, mis.M_MARCOS_SEM_REGISTO, mis.M_MARCOS_INDISPONIVEIS, mis.M_CURVAS_HISTORICAS,
                  mis.M_YES_SEM_REGISTO, mis.M_DISCORDANCIA, 'sem_yes_conhecido_antes_do_alvo',
                  'sem_referencia_p0_conhecida', 'sem_historico', 'estimador_sem_previsao'):
            self.assertNotEqual(mis.categoria_motivo(m), 'outros', m)

    def test_contagem_soma_os_rejeitados_e_nao_repete(self):
        rej = [{'motivo': mis.M_CURVAS_HISTORICAS}, {'motivo': mis.M_CURVAS_HISTORICAS},
               {'motivo': mis.M_DISCORDANCIA}, {'motivo': 'sem_historico'}]
        c = mis.contar_categorias(rej)
        self.assertEqual(c, {'curvas': 2, 'discordancia': 1, 'amostra': 1})
        self.assertEqual(sum(c.values()), len(rej))

    def test_motivos_de_amostra_com_parametro_sao_amostra(self):
        for m in ('menos_de_15_observacoes', 'menos_de_5_datas_distintas', 'cobertura_inferior_a_180_dias',
                  'sem_carga_no_periodo', 'alvo_sem_variacao', 'sem_solucao_numerica'):
            self.assertEqual(mis.categoria_motivo(m), 'amostra', m)
        self.assertEqual(mis.categoria_motivo('motivo_desconhecido'), 'outros')

    def test_natureza_rotula_cada_modo(self):
        self.assertEqual(mis.natureza_avaliacao({'modo': 'retrospectivo'}, True), 'retrospectiva_valores_atuais')
        self.assertEqual(mis.natureza_avaliacao({'modo': 'conhecido_na_epoca', 'n_alvos_com_previsao_ir': 0,
                                                 'rejeicoes_por_categoria': {'marcos': 3}}, True),
                         'bloqueada_sem_historico')
        self.assertEqual(mis.natureza_avaliacao({'modo': 'conhecido_na_epoca', 'n_alvos_com_previsao_ir': 0,
                                                 'rejeicoes_por_categoria': {'marcos': 3}}, False),
                         'reconstruida_sem_prova_temporal')
        self.assertEqual(mis.natureza_avaliacao({'modo': 'conhecido_na_epoca', 'n_alvos_com_previsao_ir': 5,
                                                 'rejeicoes_por_categoria': {}}, True), 'temporal_com_historico')
        self.assertEqual(mis.natureza_avaliacao({'modo': 'conhecido_na_epoca', 'n_alvos_com_previsao_ir': 5,
                                                 'rejeicoes_por_categoria': {}}, False),
                         'reconstruida_sem_prova_temporal')


# ── cenario com registo temporal (base comum) ───────────────────────────────────

class _TestesComRegisto:
    """Mixin (nao e TestCase): testes com o registo temporal e o avaliador real. As classes concretas escolhem o motor."""

    def _marcos_e_yes(self):
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60))
        self.registar_yes_dia_a_dia()
        return primeiro

    def _curvas_gravadas(self, instante):
        curvas_gravadas_em(self, instante)

    def _item(self, res):
        return res['itens'][0]

    # ── 1. curvas sem prova temporal ─────────────────────────────────────────

    def test_curvas_regravadas_depois_do_alvo_bloqueiam_o_p0_com_motivo_explicito(self):
        self.preparar_historico()
        self._marcos_e_yes()
        self._curvas_gravadas(HORA_FUTURA)
        it = self._item(self.val())
        self.assertEqual(it['n_alvos_com_previsao_ir'], 0)
        self.assertGreater(it['rejeicoes_por_categoria'].get('curvas', 0), 0)
        self.assertEqual(it['rejeicoes_por_categoria'].get('yes', 0), 0)
        self.assertEqual(it['rejeicoes_por_categoria'].get('discordancia', 0), 0)

    # ── 2. divergencia P0/YES real (curvas provadas) ─────────────────────────

    def test_yes_registado_de_valor_diferente_com_curvas_provadas_e_discordancia(self):
        self.preparar_historico()
        self._curvas_gravadas(HORA_CURVAS_ANTIGAS)
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60))
        for d in sorted({e['data'] for e in self._efeitos}):
            efet = {e['test_id']: {'modalidade': 'Bike', 'activity_id': e['activity_id'],
                                   'watts': e['watts'] + 7.0, 'regra_versao': 1, 'origem': 'automatico',
                                   'revisao': None}
                    for e in self._efeitos if e['data'] <= d}
            self.ledger.registar_transicoes_yes(efet, d + 'T12:00:00')
        it = self._item(self.val())
        self.assertEqual(it['n_alvos_com_previsao_ir'], 0)
        self.assertGreater(it['rejeicoes_por_categoria'].get('discordancia', 0), 0)
        self.assertEqual(it['rejeicoes_por_categoria'].get('curvas', 0), 0)

    def test_sem_yes_registado_nao_e_discordancia(self):
        self.preparar_historico()
        self._curvas_gravadas(HORA_CURVAS_ANTIGAS)
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60))           # marcos, sem YES
        it = self._item(self.val())
        self.assertEqual(it['rejeicoes_por_categoria'].get('discordancia', 0), 0)
        self.assertGreater(it['rejeicoes_por_categoria'].get('yes', 0), 0)

    # ── 3. contagem por categoria: uma vez por alvo ──────────────────────────

    def test_contagem_por_categoria_bate_certo_com_os_alvos(self):
        self.preparar_historico()
        self._marcos_e_yes()
        self._curvas_gravadas(HORA_FUTURA)
        it = self._item(self.val())
        self.assertEqual(sum(it['rejeicoes_por_categoria'].values()), it['n_alvos_rejeitados'])
        self.assertEqual(sum(it['alvos_sem_previsao_ir'].values()), it['n_alvos'])
        self.assertNotIn('outros', it['rejeicoes_por_categoria'])
        self.assertEqual(sum(it['rejeicoes_por_categoria'].values()), it['n_alvos_rejeitados'])

    # ── 4. versao do alvo ───────────────────────────────────────────────────

    def test_alvo_sem_data_de_conhecimento_e_rejeitado_com_motivo_explicito(self):
        self.preparar_historico()
        self._marcos_e_yes()
        self._curvas_gravadas(HORA_CURVAS_ANTIGAS)
        alvo = self._efeitos[-1]['test_id']
        # detetado_em e NOT NULL na base: "sem data verificavel" e a string vazia
        db._exec("UPDATE modelo_observacoes_auto SET detetado_em = '' WHERE test_id = ?", (alvo,), None)
        it = self._item(self.val())
        self.assertEqual(it['alvos_sem_previsao_ir'].get(mis.M_VERSAO_ALVO), 1)
        self.assertEqual(it['rejeicoes_por_categoria'].get('versao_alvo'), 1)

    def test_separar_alvos_sem_versao_usa_a_linha_atual_do_historico(self):
        alvos = [{'test_id': 'a', 'data': '2025-01-01'}, {'test_id': 'b', 'data': '2025-01-02'}]
        linhas = {'a': {'detetado_em': '2025-01-01T12:00:00'}, 'b': {'detetado_em': ''}}
        com, sem = mis.separar_alvos_sem_versao(alvos, [], linhas)
        self.assertEqual([x['test_id'] for x in com], ['a'])
        self.assertEqual([x['test_id'] for x in sem], ['b'])

    # ── 5. ausencia de fuga temporal ────────────────────────────────────────

    def test_alterar_curvas_posteriores_ao_corte_nao_muda_previsoes_passadas(self):
        self.preparar_historico()
        self._marcos_e_yes()
        self._curvas_gravadas(HORA_CURVAS_ANTIGAS)
        antes = self._item(self.val())
        datas = sorted(p['data'] for p in antes['previsoes'])
        self.assertGreater(len(datas), 4, 'cenario sem alvos suficientes para comparar')
        # corte a meio da serie. Uma curva com data >= corte nunca entra no P0 de um alvo anterior ao corte
        # (P0 usa so datas <= dia anterior ao alvo): alterar essas curvas nao pode mudar o passado.
        corte = datas[len(datas) // 2]
        # regravacao com CONTEUDO alterado, conhecida so em HORA_FUTURA (nova versao; as antigas ficam intactas)
        futuras = [dict(c, watts=[w + 5.0 for w in c['watts']]) for c in db.load_power_curves()
                   if str(c['date'])[:10] >= corte]
        self.assertTrue(futuras, 'cenario sem curvas posteriores ao corte')
        self.hc.registar(futuras, HORA_FUTURA)
        depois = self._item(self.val())
        self.assertEqual(depois['previsoes'], antes['previsoes'])

    def test_curva_usada_por_alvos_passados_vira_rejeicao_explicita_nunca_valor_diferente(self):
        self.preparar_historico()
        self._marcos_e_yes()
        self._curvas_gravadas(HORA_CURVAS_ANTIGAS)
        antes = self._item(self.val())
        self.assertGreater(len(antes['previsoes']), 0)
        self._curvas_gravadas(HORA_FUTURA)                 # todas as curvas regravadas depois dos alvos
        depois = self._item(self.val())
        self.assertEqual(depois['n_alvos_com_previsao_ir'], 0)   # nenhuma previsao com valor possivelmente errado
        self.assertGreaterEqual(depois['rejeicoes_por_categoria'].get('curvas', 0), len(antes['previsoes']))

    # ── 6. sombra com o registo temporal ───────────────────────────────────

    def test_sombra_avalia_com_o_registo_temporal_e_grava_rejeicoes(self):
        self.preparar_historico()
        self._marcos_e_yes()
        self._curvas_gravadas(HORA_CURVAS_ANTIGAS)
        with tempfile.TemporaryDirectory() as d:
            loja = _loja_temporaria(d)
            out = msi.apos_recalibrar(RES_RECAL, 'sync', dados={'loja': loja, 'temporal': self.ledger,
                                                                 'historico_curvas': self.hc})
            self.assertTrue(out['ok'], out)
            self.assertFalse(out['escrito_operacional'])
            (u,) = out['unidades']
            self.assertEqual(u['acao_sombra'], 'avaliado')
            self.assertEqual(u['historico_temporal'], 'registo_prospetivo')
            self.assertGreaterEqual(sum(u['rejeicoes_por_categoria'].values()), 0)
            linha = loja.linhas()
            self.assertEqual(len(linha), 1)

    def test_sombra_com_registo_sem_marcos_regista_rejeicao_marcos(self):
        self.preparar_historico()
        self._curvas_gravadas(HORA_CURVAS_ANTIGAS)
        with tempfile.TemporaryDirectory() as d:
            loja = _loja_temporaria(d)
            out = msi.apos_recalibrar(RES_RECAL, 'sync', dados={'loja': loja, 'temporal': self.ledger,
                                                                 'historico_curvas': self.hc})
            (u,) = out['unidades']
            self.assertGreater(u['rejeicoes_por_categoria'].get('marcos', 0), 0)
            self.assertEqual(u['nivel'], 'C')

    # ── 7. falhas da sombra e escrita operacional ──────────────────────────

    def test_falha_ao_obter_o_registo_nao_levanta_nem_altera_a_recalibracao(self):
        self.preparar_historico()
        with mock.patch.object(msi, '_leitor_temporal', side_effect=RuntimeError('registo partiu')):
            out = msi.apos_recalibrar(RES_RECAL, 'sync', dados={'loja': mock.Mock()})
        self.assertFalse(out['ok'])
        self.assertIn('registo partiu', out['erro'])
        self.assertFalse(out['escrito_operacional'])

    def test_sombra_nunca_escreve_nas_tabelas_operacionais(self):
        self.preparar_historico()
        self._marcos_e_yes()
        self._curvas_gravadas(HORA_CURVAS_ANTIGAS)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(db, '_exec', wraps=db._exec) as spy:
            loja = _loja_temporaria(d)
            msi.apos_recalibrar(RES_RECAL, 'sync', dados={'loja': loja, 'temporal': self.ledger,
                                                           'historico_curvas': self.hc})
        escritas = [c.args[0] for c in spy.call_args_list
                    if c.args and str(c.args[0]).lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        for sql in escritas:
            self.assertNotIn('modelo_ir_ajustes', sql)
            self.assertNotIn('ultimo_ajuste_ir', sql)
            self.assertNotIn('modelo_ir_p0_referencias', sql)

    # ── 8. registo alterado invalida a reutilizacao ─────────────────────────

    def test_assinatura_do_registo_muda_quando_entra_um_marco(self):
        self.preparar_historico()
        a = self.ledger.assinatura_estado()
        self.registar_marcos(dia_mais(min(e['data'] for e in self._efeitos), 60))
        self.assertNotEqual(a, self.ledger.assinatura_estado())


class EtapaSqlite(_TestesComRegisto, W.CenarioTemporal):
    """SQLite: todos os testes da base comum, com o registo temporal em SQLite."""


@unittest.skipUnless(PG_DSN, 'IR_TESTE_PG_DSN nao definida: sem PostgreSQL temporario')
class EtapaPostgres(_TestesComRegisto, W.CenarioTemporal):
    """PostgreSQL (psycopg v3): o mesmo registo temporal e avaliador, com o registo em PostgreSQL."""
    LEDGER_PG = True


# ── sem migracao temporal: sem avaliacao, sem fallback ───────────────────────

class SemMigracaoTemporal(TS._Base):

    def test_leitor_de_producao_reporta_tabelas_ausentes(self):
        leitor, estado = tmp.leitor_producao()
        self.assertIsNone(leitor)
        self.assertEqual(estado, 'tabelas_ausentes')

    def test_sombra_sem_migracao_temporal_nao_avalia_nem_escreve(self):
        mig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00')   # so a sombra; temporal NAO aplicada
        out = msi.apos_recalibrar(RES_RECAL, 'sync')
        self.assertEqual(out['ignorado'], 'historico_temporal_indisponivel')
        self.assertEqual(out['estado_temporal'], 'tabelas_ausentes')
        self.assertEqual(out['migracao_pendente'], tmig.MIGRACAO_ID)
        self.assertFalse(out['escrito_operacional'])
        self.assertEqual(db._exec(f'SELECT COUNT(*) FROM {sombra.NOME_TABELA}', (), 'one')[0], 0)

    def test_avaliacao_sem_registo_nunca_e_chamada(self):
        mig.aplicar(db._exec, db.DRIVER, '2026-10-23T10:00:00')
        with mock.patch.object(msi, 'avaliacao_walk_forward') as av:
            msi.apos_recalibrar(RES_RECAL, 'sync')
        av.assert_not_called()


# ── endpoint legado ─────────────────────────────────────────────────────────────

class EndpointLegadoNatureza(W.CenarioTemporal):

    def _preparar(self):
        self.preparar_historico()
        primeiro = min(e['data'] for e in self._efeitos)
        self.registar_marcos(dia_mais(primeiro, 60))
        self.registar_yes_dia_a_dia()
        curvas_gravadas_em(self, HORA_CURVAS_ANTIGAS)

    def test_com_registo_rotula_cada_modo_e_declara_historico_temporal(self):
        self._preparar()
        r = self.c.get('/api/modelo/ir/validacao?tipo=Bike&duracao=1200&modo=ambos')
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j['historico_temporal'], 'registo_prospetivo')
        # estado canonico: 'incompleto' (ha alvos anteriores ao marco, rejeitados); o valor legado 'ok' fica separado
        self.assertEqual(j['historico_temporal_estado'], 'incompleto')
        self.assertEqual(j['historico_temporal_estado_legado'], 'ok')
        self.assertEqual(j['natureza_avaliacao']['conhecido_na_epoca'], 'temporal_incompleta')
        self.assertEqual(j['natureza_por_modo']['conhecido_na_epoca'], 'temporal_com_historico')
        modos = {it['modo']: it['natureza'] for it in j['itens']}
        self.assertEqual(modos['retrospectivo'], 'retrospectiva_valores_atuais')
        self.assertEqual(modos['conhecido_na_epoca'], 'temporal_com_historico')
        self.assertTrue(j['legado'])

    def test_sem_registo_bloqueia_a_avaliacao_temporal_sem_reconstruir(self):
        """Etapa 14: sem o historico temporal o endpoint NAO avalia conhecido_na_epoca (nao reconstroi)."""
        self._preparar()
        db._exec(f'DROP TABLE IF EXISTS {tmp.TAB_YES}', (), None)
        db._exec(f'DROP TABLE IF EXISTS {tmp.TAB_MARCOS}', (), None)
        j = self.c.get('/api/modelo/ir/validacao?tipo=Bike&duracao=1200&modo=conhecido_na_epoca').get_json()
        self.assertEqual(j['historico_temporal_estado'], 'indisponivel')
        self.assertEqual(j['historico_temporal_estado_legado'], 'tabelas_ausentes')
        self.assertEqual(j['itens'], [])
        r = j['resultado_por_modo']['conhecido_na_epoca']
        self.assertTrue(r['bloqueado'])
        self.assertEqual(r['motivo_bloqueio'], 'historico_temporal_indisponivel')
        self.assertEqual(j['natureza_avaliacao']['conhecido_na_epoca'], 'bloqueada_por_historico_insuficiente')
        self.assertFalse(r['validacao_temporal'])

if __name__ == '__main__':
    unittest.main()
