"""api_recovery.py — endpoints da tab Recovery.

Registado com:  import api_recovery; api_recovery.registar(app)
"""

import os
import sys
import traceback
from datetime import datetime, timedelta

from flask import jsonify, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'utils'))


def registar(app):

    @app.route('/api/recovery/dados')
    def api_recovery_dados():
        """Todos os modelos de uma vez.  ?dias=180  ?log_hf=0"""
        try:
            import hrv_recovery as hrec

            dias = request.args.get('dias', type=int) or 180
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

            # wellness: a folha é a fonte, com a Intervals.icu como reforço
            registos, origem = [], []
            try:
                import sheets_client
                w, _c, _e = sheets_client.carregar()
                for x in (w or []):
                    d = str(x.get('date') or x.get('Data') or '')[:10]
                    if d >= corte:
                        registos.append({
                            'data': d,
                            'hrv': x.get('hrv'),
                            'hf_power': x.get('hf_power'),
                            # o 'hr' da folha é o de dormir, medido pelo
                            # relógio. O restingHR/avgSleepingHR da
                            # Intervals.icu entra a seguir e tem
                            # prioridade — ver a nota em _juntar_rhr
                            'rhr_folha': x.get('rhr') or x.get('hr'),
                            'sono_h': x.get('sleep') or x.get('sono'),
                            'sono_qualidade': x.get('sleep_quality'),
                            'fadiga': x.get('fatiga') or x.get('fadiga'),
                            'stress': x.get('stress'),
                            'humor': x.get('humor'),
                            'dores': x.get('soreness') or x.get('dores'),
                            'peso': x.get('peso'),
                        })
                if registos:
                    origem.append('folha')
            except Exception as e:
                origem.append(f'folha falhou: {str(e)[:60]}')

            # ── FC de repouso: da Intervals.icu, não da folha ────────
            #
            # A folha tem um campo de FC preenchido à mão, e a
            # Intervals.icu tem restingHR e avgSleepingHR medidos pelo
            # relógio durante a noite. São medições diferentes, e misturar
            # as duas numa série faz saltos que não são fisiologia.
            #
            # A regra que aplicámos ao HRV vale aqui: um valor deve vir
            # da mesma fonte todos os dias.
            registos, nota_rhr = _juntar_rhr(registos, corte)
            if nota_rhr:
                origem.append(nota_rhr)

            prep = hrec.preparar(registos)
            if not prep.get('ok'):
                return jsonify({'status': 'sem_dados',
                                'motivo': prep.get('motivo'),
                                'origem': origem}), 200

            ln = prep['lnrmssd']
            seq = prep['dias']
            res = {'status': 'ok', 'origem': origem,
                   'periodo': {'de': prep['de'], 'ate': prep['ate'],
                               'n_dias': prep['n_dias'],
                               'n_com_hrv': prep['n_com_hrv'],
                               'cobertura_pct': prep['cobertura_pct']}}

            # cada modelo diz por si se tem dados que cheguem
            if prep['n_com_hrv'] >= hrec.MINIMOS['swc']:
                res['swc'] = hrec.baseline_swc(ln)
            else:
                res['swc'] = {'ok': False,
                              'motivo': f"{prep['n_com_hrv']} dias com HRV; "
                                        f"são precisos {hrec.MINIMOS['swc']}"}
            if prep['n_com_hrv'] >= hrec.MINIMOS['javaloyes']:
                res['javaloyes'] = hrec.javaloyes(ln)
            else:
                res['javaloyes'] = {
                    'ok': False,
                    'motivo': f"são precisos {hrec.MINIMOS['javaloyes']} dias"}

            res['kiviniemi'] = hrec.kiviniemi(
                [d.get('hf_power') for d in seq],
                usar_log=request.args.get('log_hf') != '0')
            res['pslope'] = hrec.pslope(ln)
            res['beta'] = hrec.modelo_beta(ln)

            # wellness subjectivo: z-score dos últimos 28 dias
            res['wellness'] = _wellness(seq)
            res['rhr_qualidade'] = {
                'n_com_rhr': sum(1 for d in seq if d.get('rhr') is not None),
                'n_mesma_medicao': sum(1 for d in seq
                                       if d.get('rhr_mesma_medicao')),
                'fontes': sorted({d.get('rhr_origem') for d in seq
                                  if d.get('rhr_origem')}),
                'nota': ('o restingHR e o hrv do mesmo dia saem do mesmo '
                         'registo — mesma janela, mesmos intervalos RR. É '
                         'por isso que se prefere ao avgSleepingHR, que é '
                         'a média da noite inteira'),
            }

            # pesos medidos nos próprios dados, com janela móvel
            votos_hf = None
            if res['kiviniemi'].get('ok'):
                votos_hf = [hrec._VOTO.get(p) for p in
                            res['kiviniemi']['prescricao']]
            votos_wl = _serie_wellness(seq)
            res['qualidade'] = hrec.qualidade_dos_dados(
                ln, [d.get('hf_power') for d in seq])
            res['pesos_ajustados'] = hrec.pesos_ajustados(
                ln, votos_hf, votos_wl,
                janela=request.args.get('janela_pesos', type=int) or 180)

            # síntese
            _b = res['beta'] if res['beta'].get('ok') else {}
            res['sintese'] = hrec.sintetizar(
                swc=(res['swc'].get('estado') or [None])[-1]
                if res['swc'].get('estado') else None,
                jav=(res['javaloyes'].get('prescricao') or [None])[-1]
                if res['javaloyes'].get('prescricao') else None,
                kiv=(res['kiviniemi'].get('prescricao') or [None])[-1]
                if res['kiviniemi'].get('ok') else None,
                ps=res['pslope'].get('zona_actual'),
                beta=({'beta': _b.get('beta_hoje'),
                       'agudo': _b.get('agudo_hoje'),
                       'cronico': _b.get('cronico_hoje')} if _b else None),
                wellness=(res['wellness'] or {}).get('voto'),
                pesos=res['pesos_ajustados']['pesos'],
                qualidade=res['qualidade'])
            res['datas'] = [d['data'] for d in seq]
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app


def _juntar_rhr(registos, corte):
    """FC de repouso da MESMA medição que produziu o HRV.

    Prioridade ao restingHR, não ao avgSleepingHR.

    O restingHR e o hrv saem do MESMO registo: a app calcula o rMSSD e a
    FC dos mesmos intervalos RR, na mesma janela, no mesmo momento. O
    avgSleepingHR é a média da noite inteira — outra janela, outro estado.

    Cruzar o HRV da manhã com a FC da noite compara dois momentos
    diferentes, e a diferença entre eles não é fisiologia: é o intervalo
    entre as duas medições. É a mesma razão pela qual o fórum diz que o
    HRV normalizado tem de ser rMSSD sobre o RR médio DA MEDIÇÃO.

    O avgSleepingHR fica como recurso e vai MARCADO, porque uma série que
    troca de fonte a meio produz degraus que parecem mudanças de forma.
    """
    try:
        from api_client import icu_get
        from config import ATHLETE_ID
        dados, err = icu_get(
            f'/athlete/{ATHLETE_ID}/wellness',
            params={'oldest': corte,
                    'newest': datetime.now().strftime('%Y-%m-%d')})
        if err or not isinstance(dados, list):
            for r in registos:
                r['rhr'] = r.get('rhr_folha')
                r['rhr_origem'] = 'folha'
            return registos, f'RHR da folha (Intervals falhou: {err})'

        por_data = {str(d.get('id'))[:10]: d for d in dados
                    if isinstance(d, dict)}
        conta = {}
        for r in registos:
            w = por_data.get(r['data']) or {}
            v, origem = w.get('restingHR'), 'restingHR'
            if not isinstance(v, (int, float)):
                v, origem = w.get('avgSleepingHR'), 'avgSleepingHR'
            if not isinstance(v, (int, float)):
                v, origem = r.get('rhr_folha'), 'folha'
            r['rhr'] = v if isinstance(v, (int, float)) else None
            r['rhr_origem'] = origem if r['rhr'] is not None else None
            # a FC e o HRV vieram do mesmo registo?
            r['rhr_mesma_medicao'] = (
                origem == 'restingHR'
                and isinstance(w.get('hrv'), (int, float)))
            if r['rhr'] is not None:
                conta[origem] = conta.get(origem, 0) + 1

        n_par = sum(1 for r in registos if r.get('rhr_mesma_medicao'))
        nota = 'RHR: ' + ', '.join(f'{v} dias de {k}'
                                   for k, v in sorted(conta.items()))
        if n_par:
            nota += f'. {n_par} desses vieram do mesmo registo que o HRV'
        if len(conta) > 1:
            nota += ('. ATENÇÃO: mais de uma fonte na mesma série. Os '
                     'degraus que isso produz parecem mudanças de forma e '
                     'não são')
        return registos, nota
    except Exception as e:
        for r in registos:
            r['rhr'] = r.get('rhr_folha')
            r['rhr_origem'] = 'folha'
        return registos, f'RHR da folha ({type(e).__name__})'


def _serie_wellness(seq):
    """Voto de wellness dia a dia, para os pesos poderem ser medidos.

    O _wellness() devolve só o valor de hoje. Para saber quanto o wellness
    antecipa, é preciso a série toda.
    """
    import hrv_recovery as hrec
    campos = {'sono_qualidade': +1, 'sono_h': +1, 'rhr': -1,
              'fadiga': -1, 'stress': -1, 'dores': -1, 'humor': +1}
    fora = []
    for i in range(len(seq)):
        votos = []
        for campo, sentido in campos.items():
            hoje = seq[i].get(campo)
            if not isinstance(hoje, (int, float)):
                continue
            jan = [float(seq[k][campo]) for k in range(max(0, i - 27), i)
                   if isinstance(seq[k].get(campo), (int, float))]
            if len(jan) < 14:
                continue
            m = sum(jan) / len(jan)
            s = hrec._sd(jan)
            if not s:
                continue
            votos.append(max(-1.0, min(1.0, (hoje - m) / s * sentido / 1.5)))
        fora.append(sum(votos) / len(votos) if votos else None)
    return fora


def _wellness(seq):
    """Sono, RHR, fadiga, stress e dores como um voto entre −1 e +1.

    Fica SEPARADO dos modelos de HRV de propósito: é a única fonte que não
    depende do sensor, e por isso é a que pode contradizê-lo de forma
    informativa. Fundi-la no HRV apagaria isso.
    """
    import hrv_recovery as hrec

    campos = {
        'sono_qualidade': +1, 'sono_h': +1,
        'rhr': -1,           # RHR alta é mau
        'fadiga': -1, 'stress': -1, 'dores': -1,
        'humor': +1,
    }
    votos, detalhe = [], {}
    for campo, sentido in campos.items():
        vs = [d.get(campo) for d in seq]
        reais = [float(v) for v in vs if isinstance(v, (int, float))]
        if len(reais) < 14:
            continue
        hoje = next((float(v) for v in reversed(vs)
                     if isinstance(v, (int, float))), None)
        if hoje is None:
            continue
        janela = reais[-28:]
        m = sum(janela) / len(janela)
        s = hrec._sd(janela)
        if not s:
            continue
        z = (hoje - m) / s * sentido
        votos.append(max(-1.0, min(1.0, z / 1.5)))
        detalhe[campo] = {'hoje': round(hoje, 1), 'media28': round(m, 1),
                          'z': round(z, 2), 'n': len(reais)}
    if not votos:
        return {'ok': False, 'motivo': 'sem campos com 14+ dias'}
    return {
        'ok': True,
        'voto': round(sum(votos) / len(votos), 2),
        'n_campos': len(votos),
        'campos': detalhe,
        'nota': ('z-score de cada campo contra os seus 28 dias, com o sinal '
                 'invertido nos que são maus quando sobem (RHR, fadiga, '
                 'stress, dores). É a única fonte independente do sensor'),
    }
