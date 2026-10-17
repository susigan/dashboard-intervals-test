"""Fase 1 — contrato puro (modelo_testes.py): sem Flask, sem base de dados, sem rede."""
import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fase1_util as U  # noqa: E402
import modelo_testes as M  # noqa: E402
import protocolo as P  # noqa: E402

AGORA = datetime(2026, 10, 17, 12, 0, 0)
CURVAS = U.curvas('Bike', 12) + U.curvas('Row', 8)


def anotada(estados=None):
    det = P.detectar_testes(CURVAS)
    if estados is None:
        return P.anotar_elegibilidade(det, P.mapa_ignore_power(U.todas(CURVAS, False)))
    return P.anotar_elegibilidade(det, P.mapa_ignore_power(estados))


def pedido(**kw):
    d = {'modalidade': 'Bike', 'duracao_s': 1200, 'activity_id': 'Bike11', 'decisao': 'confirmar',
         'motivo': 'ok', 'confirmacao_explicita': True}
    d.update(kw)
    return M.validar_pedido(d)


class Identidade(unittest.TestCase):
    def test_id_deterministico_e_distinto(self):
        self.assertEqual(M.id_teste('Bike', 1200, 'i1'), 'Bike:1200:i1')
        self.assertNotEqual(M.id_teste('Bike', 1200, 'i1'), M.id_teste('Row', 1200, 'i1'))
        self.assertNotEqual(M.id_teste('Bike', 1200, 'i1'), M.id_teste('Bike', 300, 'i1'))

    def test_duracoes_vem_do_protocolo(self):
        self.assertEqual(M.DURACOES, (60, 300, 1200))
        self.assertEqual(M.MODALIDADES, ('Bike', 'Row', 'Ski', 'Run'))


class Candidatos(unittest.TestCase):
    def test_sem_anotacao_a_elegibilidade_e_desconhecida_nunca_elegivel(self):
        cs = M.candidatos(P.detectar_testes(CURVAS))
        self.assertTrue(cs)
        self.assertTrue(all(c['elegibilidade'] == 'desconhecida' and
                            c['motivo_elegibilidade'] == 'sem_anotacao' for c in cs))

    def test_vazio(self):
        self.assertEqual(M.candidatos({}), [])
        self.assertEqual(M.candidatos(None), [])
        self.assertEqual(M.candidatos_com_estado({}, {}), [])

    def test_estado_pendente_por_omissao(self):
        cs = M.candidatos_com_estado(anotada(), {})
        self.assertTrue(all(c['estado_registo'] == 'pendente' and c['revisao'] is None for c in cs))

    def test_confirmabilidade_por_elegibilidade(self):
        ativ = U.atividades(Bike11=False, Bike10=True, Bike9='ausente')
        cs = {c['test_id']: c for c in M.candidatos_com_estado(anotada(ativ), {})
              if c['duracao_s'] == 1200 and c['modalidade'] == 'Bike'}
        self.assertEqual((cs['Bike:1200:Bike11']['confirmavel'],
                          cs['Bike:1200:Bike11']['requer_aceitar_elegibilidade_desconhecida']), (True, False))
        self.assertEqual(cs['Bike:1200:Bike10']['confirmavel'], False)
        self.assertEqual((cs['Bike:1200:Bike9']['confirmavel'],
                          cs['Bike:1200:Bike9']['requer_aceitar_elegibilidade_desconhecida']), (True, True))
        self.assertIsNotNone(cs['Bike:1200:Bike9']['aviso_elegibilidade'])

    def test_nao_altera_a_deteccao_recebida(self):
        det = anotada()
        copia = repr(det)
        M.candidatos_com_estado(det, {})
        self.assertEqual(repr(det), copia)


class ValidarPedido(unittest.TestCase):
    def test_valores_por_omissao(self):
        p = pedido()
        self.assertEqual((p['metodo_confirmacao'], p['origem_registo'],
                          p['aceita_elegibilidade_desconhecida'], p['responsavel']),
                         ('manual', 'api', False, None))

    def test_motivo_e_aparado(self):
        self.assertEqual(pedido(motivo='  ok  ')['motivo'], 'ok')

    def test_erros_tem_codigo_e_campo(self):
        with self.assertRaises(M.ErroContrato) as e:
            pedido(modalidade='X')
        self.assertEqual((e.exception.status, e.exception.campo), (400, 'modalidade'))
        with self.assertRaises(M.ErroContrato) as e:
            M.validar_pedido(None)
        self.assertEqual(e.exception.status, 400)

    def test_nao_aceita_watts_do_cliente(self):
        p = pedido(watts=999, elegibilidade='elegivel')
        self.assertNotIn('watts', p)
        self.assertNotIn('elegibilidade', p)


class Decidir(unittest.TestCase):
    def test_confirmar_copia_os_dados_do_servidor(self):
        rev = M.decidir(pedido(), anotada(), None, AGORA, '2026-03-01T07:30:00')
        self.assertEqual((rev['test_id'], rev['revisao'], rev['evento'], rev['estado']),
                         ('Bike:1200:Bike11', 1, 'confirmacao', 'confirmado'))
        self.assertEqual(rev['watts'], 311.0)
        self.assertEqual((rev['registado_em'], rev['revisado_em'], rev['confirmado_em']), (AGORA,) * 3)
        self.assertEqual(rev['versao_contrato'], M.VERSAO_CONTRATO)

    def test_rejeitar_nao_tem_data_de_confirmacao(self):
        rev = M.decidir(pedido(decisao='rejeitar'), anotada(), None, AGORA)
        self.assertEqual((rev['evento'], rev['estado'], rev['confirmado_em']), ('rejeicao', 'rejeitado', None))
        self.assertIsNone(rev['hora_local'])                  # nada inventado

    def test_transicoes_e_eventos(self):
        r1 = M.decidir(pedido(), anotada(), None, AGORA)
        r2 = M.decidir(pedido(decisao='rejeitar'), anotada(), r1, AGORA)
        r3 = M.decidir(pedido(), anotada(), r2, AGORA)
        self.assertEqual([r['evento'] for r in (r1, r2, r3)],
                         ['confirmacao', 'invalidacao', 'reconfirmacao'])
        self.assertEqual([r['revisao'] for r in (r1, r2, r3)], [1, 2, 3])
        self.assertEqual(r2['registado_em'], r1['registado_em'])
        self.assertIsNone(M.decidir(pedido(), anotada(), r1, AGORA))     # mesmo estado: nada

    def test_data_de_registo_preservada_entre_revisoes(self):
        r1 = M.decidir(pedido(), anotada(), None, datetime(2026, 1, 1))
        r2 = M.decidir(pedido(decisao='rejeitar'), anotada(), r1, datetime(2026, 2, 2))
        self.assertEqual((r2['registado_em'], r2['revisado_em']), (datetime(2026, 1, 1), datetime(2026, 2, 2)))

    def test_regras_de_elegibilidade(self):
        ativ = U.atividades(Bike11=False, Bike10=True, Bike9='ausente')
        det = anotada(ativ)
        with self.assertRaises(M.ErroContrato) as e:
            M.decidir(pedido(activity_id='Bike10'), det, None, AGORA)
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(M.ErroContrato) as e:
            M.decidir(pedido(activity_id='Bike9'), det, None, AGORA)
        self.assertEqual(e.exception.status, 409)
        rev = M.decidir(pedido(activity_id='Bike9', aceita_elegibilidade_desconhecida=True), det, None, AGORA)
        self.assertEqual((rev['elegibilidade'], rev['icu_ignore_power']), ('desconhecida', None))
        self.assertEqual(M.decidir(pedido(activity_id='Bike10', decisao='rejeitar'), det, None,
                                   AGORA)['icu_ignore_power'], True)

    def test_candidato_inexistente(self):
        with self.assertRaises(M.ErroContrato) as e:
            M.decidir(pedido(activity_id='nope'), anotada(), None, AGORA)
        self.assertEqual(e.exception.status, 404)
        with self.assertRaises(M.ErroContrato) as e:
            M.decidir(pedido(), {}, None, AGORA)
        self.assertEqual(e.exception.status, 404)

    def test_hora_local_so_com_dado_real(self):
        self.assertEqual(M.hora_local_da_atividade(U.atividades(a=False), 'a'), '2026-03-01T07:30:00')
        self.assertIsNone(M.hora_local_da_atividade(U.atividades(a=False), 'b'))
        self.assertIsNone(M.hora_local_da_atividade(None, 'a'))
        self.assertIsNone(M.hora_local_da_atividade([{'id': 'a', 'start_date_local': 5}], 'a'))
        self.assertIsNone(M.hora_local_da_atividade([{'id': 'a'}], 'a'))


class SituacaoDados(unittest.TestCase):
    REV = {'activity_id': 'a', 'duracao_s': 1200, 'watts': 300.0}

    def test_estados(self):
        curva = {'a': {'secs': [60, 1200], 'watts': [500.0, 300.0]}}
        self.assertEqual(M.situacao_dados(self.REV, curva), 'conforme')
        self.assertEqual(M.situacao_dados(self.REV, {'a': {'secs': [1200], 'watts': [301.0]}}), 'divergente')
        self.assertEqual(M.situacao_dados(self.REV, {}), 'sem_curva')
        self.assertEqual(M.situacao_dados(self.REV, {'a': {'secs': [60], 'watts': [1.0]}}), 'sem_curva')
        self.assertEqual(M.situacao_dados(self.REV, None), 'sem_curva')


if __name__ == '__main__':
    unittest.main()
