"""Gera, a partir do codigo REAL (Flask test client + SQLite temporaria + dados SINTETICOS), as
respostas do servidor que o harness da UI consome. Assim a UI e testada contra o contrato real
de /api/protocolo/observacoes-auto e nao contra um payload escrito a mao.

Cenario (Bike 1200 s salvo indicacao; ver test_mmp_deteccao_automatica.BIKE/ROW):
  a1 .......... 1.o ponto da serie            -> sem_referencia_anterior
  a4 .......... com tags '20min', '5km' e um texto HTML  -> tags (tempo coincide / distancia / texto)
  a5 .......... ignore_parts presente         -> elegibilidade desconhecida (fora do estimador)
  a6 .......... icu_ignore_power=true         -> inelegivel
  b2 .......... novo maximo da season (112%)  -> incluida, referencia season
  b3 .......... exclusao manual + valor mudou -> versao 2, exclusao_manual.valor_diferente
  b4 .......... 92,6% do all-time             -> referencia all_time
  Row r6 ...... so proximo do melhor recente  -> referencia recente
"""
import os
import sys
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

XSS = '<img src=x onerror=alert(1)>'


def gerar():
    import test_mmp_deteccao_automatica as T

    class Cenario(T.BaseDB):
        def runTest(self):
            pass

    c = Cenario()
    c.setUp()
    try:
        # dois esforcos no MESMO dia sem hora que prove a ordem (a hora das atividades do fixture e de
        # outro dia, logo invalida): nenhum serve de referencia ao outro
        for aid, w in (('s1', 335), ('s2', 336)):
            c.curvs.append(T.cv(aid, 'Bike', '2026-07-01', w))
            c.ativ.append({'id': aid, 'start_date_local': '2026-03-01T07:30:00', 'icu_ignore_power': False})
        for cv_ in c.curvs:                       # so 300 e 1200 s: a tabela da UI mostra no maximo 40 linhas
            keep = [i for i, sc in enumerate(cv_['secs']) if sc != 60]
            cv_['secs'] = [cv_['secs'][i] for i in keep]
            cv_['watts'] = [cv_['watts'][i] for i in keep]
        c.sync()
        for a in c.ativ:
            if a['id'] == 'a6':
                a['icu_ignore_power'] = True
            elif a['id'] == 'a5':
                a['ignore_parts'] = [{'start_time': 0, 'end_time': 60}]
            elif a['id'] == 'a4':
                a['tags'] = ['20min', '5km', 'zona 2']
        c.atualiza()
        r = c.decide('b3', motivo=XSS)
        assert r.status_code == 200, r.get_json()
        c.muda('b3', 265)
        c.atualiza(agora=datetime(2026, 11, 1))
        out = {'get': c.c.get('/api/protocolo/observacoes-auto').get_json()}
        out['get_filtrado'] = c.c.get('/api/protocolo/observacoes-auto?tipo=Row').get_json()
        out['validacao_epoca'] = c.c.get('/api/modelo/validacao?modo=conhecido_na_epoca&tipo=Bike&duracao=1200').get_json()
        out['validacao_retro'] = c.c.get('/api/modelo/validacao?modo=retrospectivo&tipo=Bike&duracao=1200').get_json()
        assert out['get']['status'] == 'OK', out['get']
        w = next(cv['watts'][cv['secs'].index(1200)] for cv in c.curvs if cv['activity_id'] == 'b2')
        ok = c.decide('b2', motivo='excluir de teste')
        out['post_decisao_ok'] = ok.get_json()
        out['post_decisao_ok_status'] = ok.status_code
        base = {'modalidade': 'Bike', 'duracao_s': 1200, 'activity_id': 'b4', 'decisao': 'rejeitar',
                'motivo': 'x', 'confirmacao_explicita': True}
        r409 = c.c.post('/api/protocolo/testes/decisao', json={**base, 'watts_vistos': 1.0})
        out['post_409'] = r409.get_json()
        out['post_409_status'] = r409.status_code
        r400 = c.c.post('/api/protocolo/testes/decisao', json={**base, 'watts_vistos': 315.0,
                                                               'confirmacao_explicita': False})
        out['post_erro'] = r400.get_json()
        out['post_erro_status'] = r400.status_code
        import observacoes_auto as oa
        with mock.patch.object(oa, 'obter_marcos', return_value=(T.MARCOS, 'teste')):
            ra = c.c.post('/api/protocolo/observacoes-auto/atualizar')
        out['post_atualizar'] = ra.get_json()
        out['post_atualizar_status'] = ra.status_code
        # uma 2.a execucao com um esforco detetado NO DIA em que aconteceu: historico temporal parcial
        c.curvs.append(T.cv('n1', 'Bike', '2026-10-20', 345))
        c.ativ.append({'id': 'n1', 'start_date_local': '2026-10-20T07:30:00', 'icu_ignore_power': False})
        c.sync()
        c.atualiza(agora=datetime(2026, 10, 20, 20))
        out['get_parcial'] = c.c.get('/api/protocolo/observacoes-auto').get_json()
        return out
    finally:
        c.doCleanups()
