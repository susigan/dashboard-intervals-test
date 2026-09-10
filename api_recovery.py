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
            res['altini'] = hrec.altini(ln)
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
                altini=res['altini'].get('estado_hoje')
                if res['altini'].get('ok') else None,
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
            # ── correlações com fontes independentes ─────────────────
            try:
                series = {'lnrmssd': ln}
                rhr = [d.get('rhr') for d in seq]
                if sum(1 for v in rhr if v is not None) >= 30:
                    series['rhr'] = [float(v) if v is not None else None
                                     for v in rhr]
                wl_serie = _serie_wellness(seq)
                if sum(1 for v in wl_serie if v is not None) >= 30:
                    series['wellness'] = wl_serie
                hfp = [d.get('hf_power') for d in seq]
                if sum(1 for v in hfp if v is not None) >= 30:
                    series['hf_power'] = [hrec._ln(v) for v in hfp]
                for campo in ('sono_h', 'sono_qualidade', 'fadiga',
                              'stress', 'dores', 'peso'):
                    vs = [d.get(campo) for d in seq]
                    if sum(1 for v in vs if isinstance(v, (int, float))) >= 30:
                        series[campo] = [float(v) if isinstance(
                            v, (int, float)) else None for v in vs]
                # carga e PMC, se existirem
                diag_carga = {}
                series.update(_carga_e_pmc(seq, diag_carga))
                res['diagnostico_series_treino'] = diag_carga
                if diag_carga.get('carga') or diag_carga.get('pmc') \
                        or diag_carga.get('ftlm'):
                    res['erros_series_treino'] = {
                        k: v for k, v in diag_carga.items()
                        if k in ('carga', 'pmc', 'ftlm')}
                res['correlacoes'] = hrec.correlacoes(series)

                # ── cada MODELO como alvo, não só o HRV em bruto ─────
                #
                # "a carga mexe no HRV?" e "a carga mexe no que o modelo
                # DIZ?" são perguntas diferentes. Os modelos são
                # transformações não-lineares do mesmo sinal, e um deles
                # pode acompanhar a carga melhor do que o valor contínuo.
                alvos = {'lnrmssd': ln}
                if res['swc'].get('estado'):
                    alvos['plews'] = hrec.ordinal(res['swc']['estado'])
                if res['altini'].get('ok'):
                    alvos['altini'] = hrec.ordinal(res['altini']['estado'])
                if res['javaloyes'].get('prescricao'):
                    alvos['javaloyes'] = hrec.ordinal(
                        res['javaloyes']['prescricao'])
                if res['kiviniemi'].get('ok'):
                    alvos['kiviniemi'] = hrec.ordinal(
                        res['kiviniemi']['prescricao'])
                if res['pslope'].get('ok'):
                    alvos['pslope'] = hrec.ordinal(res['pslope']['zonas'])
                if res['beta'].get('ok'):
                    alvos['beta'] = res['beta']['beta']

                # AS PREDITORAS SÃO SÓ TREINO.
                #
                # O filtro anterior tirava apenas o lnrmssd e o hf_power, e
                # deixava passar o rhr, o wellness, o sono, a fadiga. Nada
                # disso é treino — e o RHR ganhava sempre, porque sai do
                # MESMO registo que o HRV. Um rho de −0.72 entre eles não
                # diz nada sobre a carga: diz que a FC e o HRV da mesma
                # medição estão relacionados, o que já se sabia.
                #
                # Lista explícita, por prefixo, em vez de exclusão: o que
                # não for reconhecido como treino fica de fora.
                def _e_treino(k):
                    return k.startswith((
                        'kj', 'tss', 'horas', 'distancia', 'n_sessoes',
                        'ctl', 'atl', 'tsb', 'ftlm'))
                pred = {k: v for k, v in series.items() if _e_treino(k)}
                res['preditoras_usadas'] = sorted(pred)
                res['preditoras_ignoradas'] = sorted(
                    k for k in series if not _e_treino(k))
                res['correlacoes_modelos'] = hrec.correlacoes_multi_alvo(
                    alvos, pred)
            except Exception as e:
                res['correlacoes'] = {'ok': False,
                                      'erro': f'{type(e).__name__}: {e}'}

            res['datas'] = [d['data'] for d in seq]
            # o LnRMSSD DIÁRIO: o gráfico do SWC precisa dele para se ver
            # a dispersão de que a banda foi feita. Só a média de 7 dias
            # esconde os dias que a produziram
            res['lnrmssd'] = [round(v, 4) if v is not None else None
                              for v in ln]
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


# Nomes de coluna candidatos para cada campo. Os literais 'icu_joules' e
# 'icu_training_load' rebentaram em produção com UndefinedColumn — a
# tabela real usa outros nomes, que eu não tinha visto (o db.py nunca
# passou por esta conversa). Em vez de adivinhar de novo, descobre-se o
# que existe e usa-se isso.
_CAND_COLUNAS = {
    'joules': ['icu_joules', 'joules', 'work', 'kilojoules', 'kj'],
    'load':   ['icu_training_load', 'training_load', 'load', 'tss',
               'icu_hrss', 'hrss'],
    'tempo':  ['moving_time', 'elapsed_time', 'duration'],
    'dist':   ['distance', 'icu_distance'],
}


def _colunas_existentes(nomes_tabela='activities'):
    """Que colunas de facto existem na tabela, para escolher os nomes certos."""
    try:
        import db as _db
        r = _db._exec(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s" if _db.MODO == 'postgres' else
            f"PRAGMA table_info({nomes_tabela})",
            (nomes_tabela,) if _db.MODO == 'postgres' else (),
            fetch='all') or []
        return {str(row[0]).lower() for row in r}
    except Exception:
        return set()


def _escolher_coluna(disponiveis, candidatos):
    for c in candidatos:
        if c.lower() in disponiveis:
            return c
    return None


def _carga_e_pmc(seq, diag=None):
    """kJ, TSS, volume e CTL/ATL/TSB/FTLM alinhados com os dias.

    Estes são a única fonte com DIRECÇÃO clara: a carga vem antes da
    resposta. Por isso é neles que o desfasamento tem mais a dizer.

    As colunas são descobertas em runtime — ver _colunas_existentes — em
    vez de assumidas por nome. 'icu_joules' e 'icu_training_load' não
    existem nesta base; os nomes reais aparecem em res['colunas_activities']
    para se poder confirmar.

    O `diag` recolhe o que falhou. Antes, qualquer excepção era engolida
    por um `except: pass` e as séries desapareciam sem explicação.
    """
    fora = {}
    diag = diag if diag is not None else {}
    disp = _colunas_existentes()
    diag['colunas_encontradas'] = sorted(disp)[:40] if disp else None
    col_j = _escolher_coluna(disp, _CAND_COLUNAS['joules'])
    col_l = _escolher_coluna(disp, _CAND_COLUNAS['load'])
    col_t = _escolher_coluna(disp, _CAND_COLUNAS['tempo'])
    col_d = _escolher_coluna(disp, _CAND_COLUNAS['dist'])
    if not disp:
        diag['carga'] = ('não foi possível listar as colunas de '
                         '"activities" — ver erro em baixo')
        return fora
    if not (col_j or col_l):
        diag['carga'] = (
            f'nenhuma coluna de carga reconhecida entre as candidatas '
            f"{_CAND_COLUNAS['joules'] + _CAND_COLUNAS['load']}. "
            f'Colunas disponíveis: {sorted(disp)[:20]}')
        return fora
    try:
        import db as _db
        d0, d1 = seq[0]['data'], seq[-1]['data']
        # A query usa as colunas DESCOBERTAS acima, não nomes fixos.
        # kJ = joules/1000 quando há coluna de joules; senão fica None e
        # só o 'load' (TSS/HRSS/o que existir) entra.
        sel_j = f'SUM(COALESCE({col_j},0))/1000.0' if col_j else 'NULL'
        sel_l = f'SUM(COALESCE({col_l},0))' if col_l else 'NULL'
        sel_t = f'SUM(COALESCE({col_t},0))/3600.0' if col_t else 'NULL'
        sel_d = f'SUM(COALESCE({col_d},0))/1000.0' if col_d else 'NULL'
        linhas = _db._exec(
            f"""SELECT date, {sel_j}, {sel_l}, {sel_t}, {sel_d}, COUNT(*)
                 FROM activities WHERE date BETWEEN ? AND ?
                GROUP BY date""", (d0, d1), fetch='all') or []
        diag['colunas_usadas'] = {'joules': col_j, 'load': col_l,
                                  'tempo': col_t, 'distancia': col_d}
        por_data = {str(r[0])[:10]: r[1:] for r in linhas}
        vazio = (None, None, None, None, None)
        kj = [por_data.get(d['data'], vazio)[0] for d in seq]
        tss = [por_data.get(d['data'], vazio)[1] for d in seq]

        # VOLUME: horas, distância e número de sessões.
        #
        # A carga (kJ, TSS) e o volume não são a mesma coisa — três horas
        # fáceis e uma hora dura podem dar o mesmo TSS e afectar a
        # recuperação de formas diferentes. Testá-los em separado é a
        # única maneira de ver qual dos dois se relaciona com o HRV.
        horas = [por_data.get(d['data'], vazio)[2] or 0.0 for d in seq]
        dist = [por_data.get(d['data'], vazio)[3] or 0.0 for d in seq]
        n_ses = [por_data.get(d['data'], vazio)[4] or 0 for d in seq]
        for nome, vs in (('horas', horas), ('distancia_km', dist),
                         ('n_sessoes', n_ses)):
            if sum(1 for v in vs if v) >= 30:
                fora[nome] = [float(v) for v in vs]
                # acumulados: o volume de uma semana pesa mais do que o
                # de um dia, e é o que a periodização manipula
                for jan in (7, 21):
                    ac = []
                    for i in range(len(vs)):
                        j = vs[max(0, i - jan + 1):i + 1]
                        ac.append(float(sum(j)) / len(j) if j else None)
                    fora[f'{nome}_media_{jan}d'] = ac
        # dias sem treino são 0, não são dados em falta
        kj = [v if v is not None else 0.0 for v in kj]
        tss = [v if v is not None else 0.0 for v in tss]
        if sum(1 for v in kj if v) >= 30:
            fora['kj'] = kj
            # Carga ACUMULADA, não a de um dia isolado.
            #
            # Testar o kJ de há 21 dias contra o HRV de hoje procura o
            # efeito de UM treino três semanas depois — que não existe. O
            # que persiste é a carga acumulada dessas semanas, e é isso
            # que tem de ser a série.
            for jan in (7, 21):
                acum = []
                for i in range(len(kj)):
                    j = kj[max(0, i - jan + 1):i + 1]
                    acum.append(sum(j) / len(j) if j else None)
                fora[f'kj_media_{jan}d'] = acum
        if sum(1 for v in tss if v) >= 30:
            fora['tss'] = tss
            # CTL/ATL/TSB dos próprios dados, com as constantes usuais
            ctl, atl = [], []
            c = a = 0.0
            for v in tss:
                c += (v - c) / 42.0
                a += (v - a) / 7.0
                ctl.append(round(c, 1))
                atl.append(round(a, 1))
            fora['ctl'] = ctl
            fora['atl'] = atl
            fora['tsb'] = [round(x - y, 1) for x, y in zip(ctl, atl)]
    except Exception as e:
        diag['carga'] = f'{type(e).__name__}: {e}'

    # ── séries do PMC: reserva homeostática, FTLM ────────────────────
    #
    # Estas são a razão pela qual as correlações com lags longos valem a
    # pena. O CTL clássico satura em poucas semanas; o FTLM tem memória
    # longa por construção, e a reserva homeostática é ajustada aos
    # dados do atleta em vez de usar τ=42/7 fixos.
    #
    # Se o HRV se relacionar com a reserva a 21 dias e não com o CTL,
    # isso diz que o modelo ajustado descreve melhor a resposta — e é o
    # tipo de coisa que só se vê testando as duas.
    try:
        import pmc as _pmc
        import db as _db2
        # o pmc.calcular espera as sessões, não vai buscá-las sozinho
        ses = _db2._exec(
            "SELECT date, type, icu_training_load, icu_joules "
            "FROM activities WHERE date BETWEEN ? AND ? ORDER BY date",
            (seq[0]['data'], seq[-1]['data']), fetch='all') or []
        sessoes = [{'date': str(r[0])[:10], 'type': r[1],
                    'tl': r[2] or 0, 'icu_joules': r[3] or 0}
                   for r in ses]
        serie = _pmc.calcular(sessoes) if sessoes else None
        if serie:
            por_data = {r['date']: r for r in serie}
            for campo, nome in (('ctl', 'ctl_pmc'), ('atl', 'atl_pmc'),
                                ('tsb', 'tsb_pmc')):
                vs = [por_data.get(d['data'], {}).get(campo) for d in seq]
                if sum(1 for v in vs if v is not None) >= 30:
                    fora[nome] = vs
    except Exception as e:
        diag['pmc'] = f'{type(e).__name__}: {e}'
    try:
        import ftlm as _ftlm
        kj_lim = [v or 0.0 for v in (fora.get('kj') or [])]
        if len(kj_lim) >= 60:
            # dois gamas: memória curta e longa, para se ver a que escala
            # a carga ainda pesa
            for g in (0.3, 0.7):
                v = _ftlm.ftlm_fractional(kj_lim, g)
                fora[f'ftlm_g{g}'] = [float(x) for x in v]
    except Exception as e:
        diag['ftlm'] = f'{type(e).__name__}: {e}'
    return fora


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
