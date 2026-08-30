"""api_moxy.py — sessoes com sensor NIRS (Moxy).

Encontra as sessoes marcadas como Moxy e devolve os streams de SmO2 e THb
ja' limpos pelo pipeline do utils/mnirs.py.

A marca e' procurada no nome, na descricao e nos campos de texto do JSON da
actividade, aceitando 'moxy', '#moxy', 'Moxy' e variantes. Nao se assume
um campo de tags: a Intervals.icu nao expoe um consistentemente, e ja'
custou caro neste projecto assumir nomes de campos.

Registado com:  import api_moxy; api_moxy.registar(app)
"""

import json
import re
import traceback
from datetime import datetime, timedelta

from flask import jsonify, request

# So' a tag conta. Antes procurava-se tambem no nome e na descricao, e
# aceitavam-se sessoes com Smo2 no sumario mesmo sem tag -- isso trazia
# actividades que nada tinham a ver, porque qualquer sessao com o sensor
# ligado por acaso entrava. A tag e' uma decisao explicita do atleta; o
# nome nao e'.
MX_SESSOES_CACHE = {}

PADRAO_MOXY = re.compile(r'^\s*#?\s*moxy\s*$', re.IGNORECASE)

# Nomes possiveis dos streams NIRS. A Intervals.icu expoe smo2/thb, mas
# ficheiros com dois sensores acrescentam sufixos.
CANAIS = {
    'smo2': ['smo2', 'SmO2', 'smo2_1', 'Smo2'],
    'thb': ['thb', 'THb', 'thb_1'],
    'o2hb': ['O2Hb', 'o2hb'],
    'hhb': ['HHb', 'hhb', 'DiffHb'],
}


def _tags(j):
    """Lista de tags da actividade. Vem null quando nao ha nenhuma."""
    t = (j or {}).get('tags')
    if isinstance(t, str):
        return [x.strip() for x in t.split(',') if x.strip()]
    return [str(x).strip() for x in (t or []) if x]


def _tem_tag_moxy(j):
    return any(PADRAO_MOXY.match(t) for t in _tags(j))


def _remover_orfa(aid):
    """Apaga das tabelas locais uma actividade que a API ja' nao tem."""
    import db as _db
    fora = {}
    for tabela, coluna in (('activities', 'id'),
                           ('power_curves', 'activity_id'),
                           ('zone_times', 'activity_id')):
        try:
            _db._exec(f"DELETE FROM {tabela} WHERE {coluna} = ?", (str(aid),))
            fora[tabela] = 'apagada'
        except Exception as e:
            fora[tabela] = f'{type(e).__name__}: {e}'
    return fora


def registar(app):

    @app.route('/api/moxy/sessoes')
    def api_moxy_sessoes():
        """Sessoes marcadas como Moxy.  ?modalidade=Bike&dias=3650&n=300"""
        try:
            import db as _db
            from config import TYPE_MAP
            # todo o historico por omissao. O limite de 365 dias
            # escondia as sessoes de 2024 e anteriores.
            dias = request.args.get('dias', type=int) or 3650
            n = min(request.args.get('n', type=int) or 300, 500)
            modalidade = request.args.get('modalidade')
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

            cond, args = ["raw IS NOT NULL", "date >= ?"], [corte]
            if modalidade:
                variantes = [k for k, v in TYPE_MAP.items() if v == modalidade]
                if variantes:
                    cond.append(f"type IN ({','.join('?' * len(variantes))})")
                    args += variantes
            linhas = _db._exec(
                f"""SELECT id, type, date, name, raw FROM activities
                     WHERE {' AND '.join(cond)} ORDER BY date DESC""",
                tuple(args), fetch='all') or []

            fora, sem_tag = [], 0
            tags_vistas = {}
            for aid, tipo, data, nome, raw in linhas:
                try:
                    j = raw if isinstance(raw, dict) else json.loads(raw)
                except Exception:
                    continue
                tt = _tags(j)
                for x in tt:
                    tags_vistas[x] = tags_vistas.get(x, 0) + 1
                if not _tem_tag_moxy(j):
                    sem_tag += 1
                    continue
                fora.append({
                    'id': aid, 'tipo': tipo, 'modalidade': TYPE_MAP.get(tipo),
                    'data': str(data)[:10], 'nome': (nome or '')[:70],
                    'tags': tt,
                    'smo2_no_sumario': (j or {}).get('Smo2'),
                    'duracao_min': (round((j or {}).get('moving_time', 0) / 60)
                                    if (j or {}).get('moving_time') else None),
                    'streams': f'/api/moxy/dados/{aid}',
                })
                MX_SESSOES_CACHE[str(aid)] = fora[-1]
                if len(fora) >= n:
                    break
            return jsonify({
                'status': 'ok', 'n': len(fora), 'dias': dias,
                'modalidade': modalidade,
                'sessoes_sem_tag': sem_tag,
                'sessoes': fora,
                'ultima': fora[0] if fora else None,
                'tags_existentes': dict(sorted(tags_vistas.items(),
                                               key=lambda kv: -kv[1])),
                'nota': ('so entram actividades com a tag "Moxy". O nome da '
                         'sessao e ignorado, e ter Smo2 no sumario tambem nao '
                         'chega -- a tag e uma decisao explicita, o resto e '
                         'coincidencia. tags_existentes lista o que ha na '
                         'base, para se confirmar a grafia')})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    def _dados_sessao(activity_id, args):
        """Streams NIRS limpos de uma sessao.

        ?fc=0.02  frequencia de corte   ?outlier=3   ?acima=95
        ?normalizar=deslocar|reescalar
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import mnirs as mn
            import api_client as api

            aid = str(activity_id).strip().strip('/').split('/')[-1]
            bruto, err = api.icu_get(f'/activity/{aid}/streams')
            if err:
                # 404 = a actividade ja' nao existe na Intervals.icu, mas
                # continua na base local. Acontece sempre que se apaga e
                # volta a carregar uma sessao: o id muda e o sync
                # incremental so' acrescenta, nunca remove. Limpa-se aqui,
                # para o registo morto nao voltar a aparecer na lista.
                if '404' in str(err):
                    removidas = _remover_orfa(aid)
                    return ({
                        'status': 'removida',
                        'mensagem': ('esta actividade ja nao existe na '
                                     'Intervals.icu e foi removida da base '
                                     'local. Provavelmente foi apagada e '
                                     'recarregada com outro id -- carrega em '
                                     '"Actualizar sessões" para apanhar a nova'),
                        'id_usado': aid, 'linhas_removidas': removidas})
                return ({'status': 'erro', 'mensagem': f'API: {err}',
                                'id_usado': aid})
            lista = bruto
            if isinstance(lista, dict):
                lista = lista.get('streams') or lista.get('content') or []

            streams = {}
            for st in (lista or []):
                if isinstance(st, dict) and (st.get('type') or st.get('name')):
                    streams[st.get('type') or st.get('name')] = st.get('data') or []

            def _norm(x):
                return ''.join(c for c in str(x).lower() if c.isalnum())

            def _achar(nomes):
                """Nome do stream, tolerante a variantes.

                O ficheiro FIT declara o campo como "SmO2 (%)" com id
                dev_field_0_34, e a Intervals.icu pode expo-lo com o nome
                do dev field, com a unidade colada, ou com sufixo de
                sensor. Comparacao exacta falhava nesses casos e a sessao
                aparecia como "sem streams de SmO2" tendo-os.
                """
                alvos = [_norm(a) for a in nomes]
                for k in streams:                      # exacto
                    if _norm(k) in alvos:
                        return k
                for k in streams:                      # comeca por
                    nk = _norm(k)
                    if any(nk.startswith(a) or a.startswith(nk)
                           for a in alvos if a):
                        return k
                for k in streams:                      # contem
                    nk = _norm(k)
                    if any(a and a in nk for a in alvos):
                        return k
                return None

            canais, mapa = {}, {}
            for alvo, nomes in CANAIS.items():
                k = _achar(nomes)
                if k and streams[k]:
                    canais[alvo] = streams[k]
                    mapa[alvo] = k
            if not canais:
                # Sem adivinhar: devolve-se tudo o que a sessao tem, com o
                # numero de pontos, para se ver qual e' o canal do sensor.
                # Um dev field pode chegar como 'dev_field_0_34' sem nome
                # legivel, e nesse caso so' o utilizador sabe qual e'.
                detalhe = sorted(
                    ({'stream': k, 'n_pontos': len(v),
                      'amostra': [x for x in (v or [])[:5]]}
                     for k, v in streams.items()),
                    key=lambda x: -x['n_pontos'])
                return ({
                    'status': 'sem_dados', 'id': aid,
                    'mensagem': ('sem streams de SmO2 ou THb reconhecidos. '
                                 'Se algum dos streams abaixo for o sensor '
                                 '(pode chegar como dev_field_N sem nome), '
                                 'acrescenta o nome em CANAIS no api_moxy.py'),
                    'streams_na_actividade': sorted(streams),
                    'detalhe_dos_streams': detalhe})

            n = len(next(iter(canais.values())))
            tempo = streams.get('time') or list(range(n))

            res = mn.processar(
                tempo, canais, hz=1.0,
                acima=args.get('acima', type=float) or 95.0,
                corte_outlier=args.get('outlier', type=float) or 3.0,
                fc=args.get('fc', type=float) or 0.02,
                normalizar=args.get('normalizar'))

            # todos os outros canais que a sessao tenha: entram sem
            # filtragem NIRS, so' reamostrados, para se poder ver o SmO2
            # contra a intensidade, a respiracao ou a cadencia
            extras = {
                'watts': ['watts', 'power'],
                'heartrate': ['heartrate', 'hr'],
                'cadence': ['cadence'],
                'respiration': ['respiration', 'RespirationRateAlphaHRV'],
                'dfa_a1': ['dfa_a1', 'dfaa1'],
                'velocity_smooth': ['velocity_smooth', 'Speed'],
                'torque': ['torque'],
            }
            # Canais que dependem da cinta peitoral levam o mesmo
            # tratamento que os NIRS. A serie de RR e' o que produz o
            # DFA-a1 e a frequencia respiratoria: se a cinta falha, os tres
            # herdam os buracos, e nas sessoes deste atleta o stream de
            # artefactos chegou a marcar 97% dos pontos. Deixa-los brutos ao
            # lado de um SmO2 filtrado dava a impressao errada de que o
            # ruido era do musculo.
            DA_CINTA = {'heartrate', 'dfa_a1', 'respiration'}
            art_k = _achar(['artifacts', 'artifact'])
            artefactos = streams.get(art_k) if art_k else None
            art_max = args.get('artefacto_max', type=float) or 5.0
            n_art = 0

            for alvo, nomes in extras.items():
                k = _achar(nomes)
                if not k:
                    continue
                serie = list(streams[k])
                if alvo in DA_CINTA and artefactos:
                    for i in range(min(len(serie), len(artefactos))):
                        a = artefactos[i]
                        if a is not None and a > art_max:
                            serie[i] = None
                            n_art += 1
                t2, v2 = mn.resample(tempo, serie, hz=1.0)
                if alvo in DA_CINTA:
                    v2, d_rep = mn.replace(
                        v2, invalidos=(0,),
                        corte_outlier=args.get('outlier', type=float) or 3.0,
                        largura=15)
                    v2 = mn.media_movel(v2, 15)
                    res['diagnostico'][alvo] = {
                        **d_rep, 'n_pontos': len(v2),
                        'filtro': {'metodo': 'media movel', 'largura': 15},
                        'fonte': 'cinta peitoral'}
                res['canais'][alvo] = [
                    round(x, 2) if x is not None else None for x in v2]
                mapa[alvo] = k
                if alvo not in res['diagnostico']:
                    vv = [x for x in v2 if x is not None]
                    res['diagnostico'][alvo] = {
                        'n_pontos': len(v2),
                        'invalidos': sum(1 for x in v2 if x is None),
                        'outliers': 0, 'pct_substituido': None,
                        'minimo': round(min(vv), 1) if vv else None,
                        'maximo': round(max(vv), 1) if vv else None,
                        'filtro': {'metodo': 'nenhum',
                                   'motivo': 'canal de contexto, so reamostrado'},
                        'fonte': 'stream original'}

            if artefactos:
                validos = [a for a in artefactos if a is not None]
                res['artefactos'] = {
                    'stream': art_k, 'limiar_usado': art_max,
                    'pontos_descartados': n_art,
                    'pct_acima_do_limiar': (
                        round(sum(1 for a in validos if a > art_max)
                              / len(validos) * 100, 1) if validos else None),
                    'nota': ('aplicado so aos canais que vem da cinta '
                             '(FC, DFA-a1, respiracao). O SmO2 e o THb vem '
                             'do Moxy e nao sao afectados')}

            # Blocos: primeiro pelos LAPS, que sao a estrutura marcada pelo
            # atleta e trazem o tipo WORK/RECOVERY. So' na falta deles se
            # deduz da potencia -- deduzir e' sempre pior do que ler.
            laps, err_l = api.icu_get(f'/activity/{aid}/intervals')
            if isinstance(laps, dict):
                laps = laps.get('icu_intervals') or laps.get('intervals') or []
            bl = mn.blocos_de_laps(laps or [])
            if bl.get('ok'):
                res['blocos'] = bl
                res['corte_proposto'] = mn.propor_corte_laps(bl)
            else:
                wt = res['canais'].get('watts')
                if wt:
                    bl = mn.detectar_blocos(res['tempo'], wt, hz=1.0)
                    bl['fonte'] = 'potencia (sem laps)'
                    res['blocos'] = bl
                    res['corte_proposto'] = mn.propor_corte(bl)
                else:
                    res['blocos'] = {'ok': False,
                                     'motivo': 'sem laps nem potencia'}
                    res['corte_proposto'] = {'ok': False,
                                             'motivo': 'sem laps nem potencia'}
            if err_l:
                res['erro_laps'] = err_l

            # corte gravado pelo utilizador tem prioridade
            try:
                import drive_db_perfil as ddp
                cn = ddp.get_conn()
                r = cn.execute(
                    """SELECT inicio_s, fim_s, origem, nota, data_gravacao
                         FROM moxy_cortes WHERE activity_id = ?""",
                    (aid,)).fetchone()
                cn.close()
                if r:
                    res['corte_guardado'] = {
                        'inicio_s': r[0], 'fim_s': r[1], 'origem': r[2],
                        'nota': r[3], 'data_gravacao': r[4]}
            except Exception as e:
                res['corte_guardado'] = None
                res['erro_corte'] = f'{type(e).__name__}: {e}'

            res['status'] = 'ok'
            res['activity_id'] = aid
            res['streams_usados'] = mapa
            res['canais_nirs'] = sorted(canais)
            res['canais_contexto'] = sorted(
                k for k in res['canais'] if k not in canais)
            res['streams_na_actividade'] = sorted(streams)
            return (res)
        except Exception as e:
            return ({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()})


    @app.route('/api/moxy/dados/<path:activity_id>')
    def api_moxy_dados(activity_id):
        """Streams NIRS limpos de uma sessao.

        ?fc=0.02  ?outlier=3  ?acima=95  ?normalizar=deslocar|reescalar
        """
        return jsonify(_dados_sessao(activity_id, request.args))

    @app.route('/api/moxy/actualizar')
    def api_moxy_actualizar():
        """Reconcilia a base local com a Intervals.icu.

        Tres coisas que o sync incremental nao faz:

          1. Remove o que ja' nao existe la'. Uma sessao apagada fica para
             sempre na base local.
          2. Volta atras no tempo. O incremental arranca da ultima data
             sincronizada e so' avanca, portanto uma sessao recarregada com
             data de 2025 nunca entra.
          3. Actualiza o que mudou. Uma sessao reprocessada mantem o id mas
             muda de conteudo, e o incremental ignora-a por ja' existir.

        A API e' consultada em blocos de 180 dias em vez de um pedido
        unico, porque um intervalo de tres anos pode ser truncado sem
        aviso. O numero devolvido por bloco vai na resposta, para se ver se
        isso acontece.

        ?dias=1095   ?bloco=180   ?so_diagnostico=1
        """
        try:
            import db as _db
            import api_client as api
            import sync as _sync

            dias = request.args.get('dias', type=int) or 1095
            bloco = request.args.get('bloco', type=int) or 180
            so_diag = request.args.get('so_diagnostico') in ('1', 'true')
            hoje = datetime.now()
            oldest_geral = (hoje - timedelta(days=dias)).strftime('%Y-%m-%d')

            todas, chunks, erros = {}, [], []
            fim = hoje
            while True:
                ini = fim - timedelta(days=bloco)
                if ini < hoje - timedelta(days=dias):
                    ini = hoje - timedelta(days=dias)
                bruto, err = api.icu_get(
                    f'/athlete/{api.ATHLETE_ID}/activities',
                    {'oldest': ini.strftime('%Y-%m-%d'),
                     'newest': fim.strftime('%Y-%m-%d')})
                if err:
                    erros.append({'de': ini.strftime('%Y-%m-%d'), 'erro': err})
                else:
                    acts = bruto or []
                    if isinstance(acts, dict):
                        acts = acts.get('content') or []
                    planas = []
                    for x in acts:
                        if isinstance(x, dict):
                            planas.append(x)
                        elif isinstance(x, list):
                            planas.extend(y for y in x if isinstance(y, dict))
                    for a in planas:
                        if a.get('id'):
                            todas[str(a['id'])] = a
                    chunks.append({'de': ini.strftime('%Y-%m-%d'),
                                   'ate': fim.strftime('%Y-%m-%d'),
                                   'n': len(planas)})
                if ini <= hoje - timedelta(days=dias):
                    break
                fim = ini

            ids_api = set(todas)
            locais = _db._exec(
                "SELECT id FROM activities WHERE date >= ?",
                (oldest_geral,), fetch='all') or []
            ids_locais = {str(r[0]) for r in locais}
            orfas = sorted(ids_locais - ids_api)
            novas = sorted(ids_api - ids_locais)

            resumo = {
                'status': 'ok', 'dias': dias, 'bloco_dias': bloco,
                'janela': [oldest_geral, hoje.strftime('%Y-%m-%d')],
                'blocos_pedidos': chunks, 'erros_api': erros,
                'na_api': len(ids_api), 'na_base_local': len(ids_locais),
                'n_orfas': len(orfas), 'orfas': orfas[:50],
                'n_novas': len(novas), 'novas': novas[:50],
            }
            if so_diag:
                resumo['nota'] = ('so_diagnostico=1: nada foi alterado. '
                                  'Retira o parametro para aplicar')
                return jsonify(resumo)

            for aid in orfas:
                _remover_orfa(aid)

            # Grava TODAS as que a API tem, nao so' as novas: uma sessao
            # reprocessada mantem o id e muda de conteudo, e ficaria com o
            # JSON antigo se so' se tratassem as novas.
            gravadas = 0
            try:
                rows = [r for r in (_sync.to_row(a) for a in todas.values())
                        if r]
                sem_data = len(todas) - len(rows)
                if rows:
                    ins, upd = _db.upsert_activities(rows)
                    gravadas = ins + upd
                resumo['gravadas'] = gravadas
                resumo['inseridas'] = ins if rows else 0
                resumo['actualizadas'] = upd if rows else 0
                resumo['descartadas_sem_data'] = sem_data
            except Exception as e:
                resumo['erro_gravar'] = f'{type(e).__name__}: {e}'

            try:
                # a cache vive no app.py; importado aqui para nao criar
                # dependencia circular no topo do modulo
                from app import invalidar_cache
                invalidar_cache()
            except Exception as e:
                resumo['aviso_cache'] = f'{type(e).__name__}: {e}'

            resumo['nota'] = (
                'orfas = existiam localmente e ja nao estao na API (apagadas '
                'la). novas = estao na API e nao estavam ca. Todas as da API '
                'sao regravadas, para apanhar as que mudaram de conteudo sem '
                'mudar de id')
            return jsonify(resumo)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/moxy/comparar')
    def api_moxy_comparar():
        """Compara varias sessoes, alinhadas pela potencia de cada degrau.

        ?ids=i1,i2,i3   ?tolerancia=15   ?aparar=30
        Alem dos parametros de /api/moxy/dados.

        Os canais vem sufixados com o indice da sessao -- smo2_1, smo2_2 --
        e os degraus vem emparelhados por potencia, nao por tempo: assim
        nao e preciso sincronizar sessoes com aquecimentos diferentes.
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import mnirs as mn

            ids = [x.strip() for x in (request.args.get('ids') or '').split(',')
                   if x.strip()]
            if not ids:
                return jsonify({'status': 'erro',
                                'mensagem': 'passa ?ids=i1,i2'}), 400
            if len(ids) > 4:
                ids = ids[:4]

            sessoes, canais_comb, degraus_por_sessao = [], {}, []
            for n, aid in enumerate(ids, start=1):
                d = _dados_sessao(aid, request.args)
                if d.get('status') != 'ok':
                    sessoes.append({'indice': n, 'id': aid,
                                    'status': d.get('status'),
                                    'mensagem': d.get('mensagem')})
                    degraus_por_sessao.append([])
                    continue

                # corte guardado ou proposto: comparar aquecimentos nao
                # tem sentido nenhum
                corte = d.get('corte_guardado') or (
                    d.get('corte_proposto') if (d.get('corte_proposto') or {})
                    .get('ok') else None)
                t = d.get('tempo') or []
                i0, i1 = 0, len(t) - 1
                if corte and t:
                    for i, x in enumerate(t):
                        if x >= corte['inicio_s']:
                            i0 = i
                            break
                    for i in range(len(t) - 1, -1, -1):
                        if t[i] <= corte['fim_s']:
                            i1 = i
                            break

                canais_cortados = {k: v[i0:i1 + 1]
                                   for k, v in (d.get('canais') or {}).items()}
                tempo_cortado = t[i0:i1 + 1]
                for k, v in canais_cortados.items():
                    canais_comb[f'{k}_{n}'] = v

                bl = mn.detectar_blocos(tempo_cortado,
                                        canais_cortados.get('watts') or [],
                                        hz=1.0)
                dg = mn.resumir_degraus(
                    tempo_cortado, canais_cortados, bl,
                    aparar=request.args.get('aparar', type=int) or 30)
                degraus_por_sessao.append(dg)

                sessoes.append({
                    'indice': n, 'id': aid, 'status': 'ok',
                    'tempo': tempo_cortado,
                    'canais_nirs': d.get('canais_nirs'),
                    'canais_contexto': d.get('canais_contexto'),
                    'corte_usado': corte,
                    'origem_do_corte': ('guardado' if d.get('corte_guardado')
                                        else 'proposto' if corte else 'nenhum'),
                    'n_degraus': len(dg),
                    'degraus': dg,
                    'diagnostico': d.get('diagnostico'),
                    'artefactos': d.get('artefactos')})

            pares = mn.emparelhar_degraus(
                degraus_por_sessao,
                tolerancia=request.args.get('tolerancia', type=float) or 15.0)

            return jsonify({
                'status': 'ok', 'n_sessoes': len(ids), 'ids': ids,
                'sessoes': sessoes,
                'canais': canais_comb,
                'degraus_emparelhados': pares,
                'n_degraus_comuns': sum(1 for p in pares
                                        if p['n_sessoes'] == len(ids)),
                'nota': ('degraus emparelhados por potencia, com tolerancia. '
                         'Alinhar por tempo exigiria sincronizar sessoes com '
                         'aquecimentos diferentes; por potencia, o degrau de '
                         '200 W compara-se com o de 200 W seja quando for. '
                         'Cada sessao entra ja cortada pelo intervalo '
                         'guardado ou proposto')})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/moxy/rede/<path:activity_id>')
    def api_moxy_rede(activity_id):
        """Rede causal entre os canais desta sessao.

        ?lag=5  ?corr=0.30  ?alfa=0.05  ?controlo=watts
        ?diferenciar=0  ?condicionar=0   (para comparar com o metodo cru)
        ?derivados=1    incluir O2Hb e HHb (componentes do SmO2)
        ?inicio=900&fim=2100   restringir ao intervalo analisado
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import rede_causal as rc

            aid = str(activity_id).strip().strip('/').split('/')[-1]
            with app.test_request_context(f'/api/moxy/dados/{aid}'):
                pass
            corpo = api_moxy_dados(aid)
            dados = corpo[0].get_json() if isinstance(corpo, tuple) \
                else corpo.get_json()
            if not dados or dados.get('status') != 'ok':
                return jsonify({'status': 'sem_dados',
                                'mensagem': (dados or {}).get('mensagem')}), 200

            t = dados.get('tempo') or []
            canais = dict(dados.get('canais') or {})
            ini = request.args.get('inicio', type=float)
            fim = request.args.get('fim', type=float)
            if ini is not None or fim is not None:
                a = 0 if ini is None else next(
                    (i for i, x in enumerate(t) if x >= ini), 0)
                b = len(t) - 1 if fim is None else next(
                    (i for i in range(len(t) - 1, -1, -1) if t[i] <= fim),
                    len(t) - 1)
                canais = {k: v[a:b + 1] for k, v in canais.items()}

            res = rc.rede(
                canais,
                controlo=request.args.get('controlo') or 'watts',
                max_lag=request.args.get('lag', type=int) or rc.MAX_LAG,
                corr_minima=request.args.get('corr', type=float) or rc.CORR_MINIMA,
                alfa=request.args.get('alfa', type=float) or rc.P_MAXIMO,
                diferenciar=request.args.get('diferenciar') != '0',
                condicionar=request.args.get('condicionar') != '0',
                incluir_derivados=request.args.get('derivados') == '1')
            res['status'] = 'ok' if res.get('ok') else 'sem_dados'
            res['activity_id'] = aid
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/moxy/interpretacao/<path:activity_id>')
    def api_moxy_interpretacao(activity_id):
        """5-1-5 Interpretation Tool automatizado.

        ?inicio=&fim=  intervalo   ?claro=10&ligeiro=3  cortes em % da amplitude
        ?fraccao=0.5   fraccao final da sessao usada
        ?resp_2A=...   sobrepor a resposta medida de qualquer pergunta
        ?repetida=0    excluir as perguntas de carga repetida (8A, 13)
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import interpretacao_515 as it

            aid = str(activity_id).strip().strip('/').split('/')[-1]
            corpo = api_moxy_dados(aid)
            dados = corpo[0].get_json() if isinstance(corpo, tuple) \
                else corpo.get_json()
            if not dados or dados.get('status') != 'ok':
                return jsonify({'status': 'sem_dados',
                                'mensagem': (dados or {}).get('mensagem')}), 200

            t = dados.get('tempo') or []
            canais = dados.get('canais') or {}
            blocos = ((dados.get('blocos') or {}).get('blocos')) or []
            ini = request.args.get('inicio', type=float)
            fim = request.args.get('fim', type=float)
            if ini is not None or fim is not None:
                a = ini if ini is not None else (t[0] if t else 0)
                b = fim if fim is not None else (t[-1] if t else 0)
                blocos = [x for x in blocos if x['t1'] >= a and x['t0'] <= b]

            art = (dados.get('artefactos') or {}).get('pct_acima_do_limiar')
            res = it.avaliar(
                t, canais, blocos, pct_artefacto=art,
                fraccao_final=request.args.get('fraccao', type=float)
                or it.FRACCAO_FINAL,
                corte_claro=(request.args.get('claro', type=float) or
                             it.CORTE_CLARO * 100) / 100.0,
                corte_ligeiro=(request.args.get('ligeiro', type=float) or
                               it.CORTE_LIGEIRO * 100) / 100.0,
                excluir_carga_repetida=(
                    None if request.args.get('repetida') is None
                    else request.args.get('repetida') == '0'))
            if not res.get('ok'):
                return jsonify({'status': 'sem_dados', **res}), 200

            # respostas sobrepostas pelo utilizador
            sobrepostas = {}
            for k, v in request.args.items():
                if k.startswith('resp_') and v:
                    q = k[5:]
                    if q in res['medicoes']['respostas']:
                        res['medicoes']['respostas'][q]['resposta'] = v
                        res['medicoes']['respostas'][q]['editada'] = True
                        sobrepostas[q] = v
            if sobrepostas:
                m = res['medicoes']
                pt = it.pontuar(m['respostas'], tem_thb=m['tem_thb'],
                                tem_hr=m['tem_hr'])
                res['pontuacao'] = pt
                res['interpretacao'] = it.interpretar(
                    pt['us']['score'], pt['pc']['score'],
                    pt['sem_resposta'], res.get('avisos'))
                res['respostas_editadas'] = sobrepostas

            res['status'] = 'ok'
            res['activity_id'] = aid
            res['niveis'] = it.NIVEIS
            res['figuras'] = it.figuras_das_perguntas()
            res['onde_mede'] = it.onde_mede_texto()
            res['faixas_2A'] = list(it.ESCALA_US['2A'])
            res['faixas_9'] = list(it.ESCALA_PC['9'])
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/moxy/resumo')
    def api_moxy_resumo():
        """Rede causal + 5-1-5 para varias sessoes, com o consenso.

        ?ids=a,b,c   ?lag=5  ?corr=0.30  ?claro=10  ?fraccao=0.5
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import rede_causal as rc
            import interpretacao_515 as it

            ids = [x.strip() for x in (request.args.get('ids') or '').split(',')
                   if x.strip()]
            if not ids:
                return jsonify({'status': 'erro',
                                'mensagem': 'sem ids'}), 400

            fora = []
            for aid in ids[:12]:
                linha = {'activity_id': aid}
                corpo = api_moxy_dados(aid)
                d = corpo[0].get_json() if isinstance(corpo, tuple) \
                    else corpo.get_json()
                if not d or d.get('status') != 'ok':
                    linha['erro'] = (d or {}).get('mensagem') or 'sem dados'
                    fora.append(linha)
                    continue

                t = d.get('tempo') or []
                canais = d.get('canais') or {}
                blocos = ((d.get('blocos') or {}).get('blocos')) or []
                g = d.get('corte_guardado') or {}
                pr = d.get('corte_proposto') or {}
                a = g.get('inicio_s') if g.get('inicio_s') is not None else (
                    pr.get('inicio_s') if pr.get('ok') else (t[0] if t else 0))
                b = g.get('fim_s') if g.get('fim_s') is not None else (
                    pr.get('fim_s') if pr.get('ok') else (t[-1] if t else 0))
                linha['corte'] = [a, b]
                bl = [x for x in blocos if x['t1'] >= a and x['t0'] <= b]
                idx = [i for i in range(len(t)) if a <= t[i] <= b]
                cj = {k: [v[i] for i in idx if i < len(v)]
                      for k, v in canais.items()}
                art = (d.get('artefactos') or {}).get('pct_acima_do_limiar')
                linha['pct_artefacto'] = art

                try:
                    r = rc.rede(
                        cj,
                        max_lag=request.args.get('lag', type=int) or rc.MAX_LAG,
                        corr_minima=request.args.get('corr', type=float)
                        or rc.CORR_MINIMA)
                    linha['rede'] = ({'limitador': r.get('limitador'),
                                      'n_dirigidas': r.get('n_dirigidas'),
                                      'canais': r.get('canais_usados')}
                                     if r.get('ok')
                                     else {'motivo': r.get('motivo')})
                except Exception as e:
                    linha['rede'] = {'erro': str(e)[:90]}

                # limiares por SmO2, para a comparacao longitudinal
                try:
                    import nirs_breakpoints as nbk
                    sm = canais.get('smo2') or []
                    hr_s = canais.get('heartrate') or []
                    pf = nbk.perfil_de_resposta(t, sm, bl) if sm else {}
                    mls = nbk.mlss_por_dessaturacao(t, sm, bl) if sm else {}
                    btx = nbk.bp_por_taxa(t, sm, bl) if sm else {}
                    bp1_w = pf.get('bp1_watts') if pf.get('ok') else None
                    bp2_w = (mls.get('mlss_estimado') if mls.get('ok')
                             else (btx.get('bp_watts') if btx.get('ok')
                                   else None))
                    linha['limiares'] = {
                        'perfil': pf.get('perfil'),
                        'perfil_ok': pf.get('ok'),
                        'perfil_motivo': pf.get('motivo'),
                        'smo2max': pf.get('smo2max'),
                        'smo2min': pf.get('smo2min'),
                        'amplitude': pf.get('amplitude'),
                        'bp1_w': bp1_w,
                        'bp1_bpm': nbk.fc_na_carga(bl, t, hr_s, bp1_w),
                        'bp2_w': bp2_w,
                        'bp2_bpm': nbk.fc_na_carga(bl, t, hr_s, bp2_w),
                        'bp2_origem': ('padrão de dessaturação'
                                       if mls.get('ok') else
                                       'quebra na taxa' if btx.get('ok')
                                       else None),
                        'bp2_motivo': (None if bp2_w is not None else
                                       (mls.get('motivo')
                                        or btx.get('motivo'))),
                        'n_degraus': len([x for x in bl if x.get('on')]),
                        'leitura': (nbk.LEITURA_DO_PERFIL.get(
                            pf.get('perfil') or '') or {}),
                    }
                except Exception as e:
                    linha['limiares'] = {'erro': f'{type(e).__name__}: {e}'}

                try:
                    v = it.avaliar(
                        t, canais, bl, pct_artefacto=art,
                        fraccao_final=request.args.get('fraccao', type=float)
                        or it.FRACCAO_FINAL,
                        corte_claro=(request.args.get('claro', type=float)
                                     or it.CORTE_CLARO * 100) / 100.0)
                    linha['i515'] = ({'pontuacao': {
                        'us': {k: v['pontuacao']['us'][k]
                               for k in ('pontos', 'max', 'score')},
                        'pc': {k: v['pontuacao']['pc'][k]
                               for k in ('pontos', 'max', 'score')}},
                        'interpretacao': v['interpretacao'],
                        'avisos': v.get('avisos')}
                        if v.get('ok') else {'motivo': v.get('motivo')})
                except Exception as e:
                    linha['i515'] = {'erro': str(e)[:90]}
                fora.append(linha)

            # ── consenso ────────────────────────────────────────────────
            # Contagem simples do limitador por eixo. Ponderar por qualidade
            # seria mais fino, mas escondia quantas sessoes ha' de cada
            # lado, que e' o que interessa saber primeiro.
            def _contar(chave, caminho):
                c = {}
                for x in fora:
                    v = x.get(caminho[0]) or {}
                    for k in caminho[1:]:
                        v = (v or {}).get(k) or {}
                    n = v.get('limitador') or v.get('sistema')
                    if n:
                        c[n] = c.get(n, 0) + 1
                return c

            cons = {
                'rede': _contar('rede', ['rede', 'limitador']),
                'us': _contar('us', ['i515', 'interpretacao', 'us']),
                'pc': _contar('pc', ['i515', 'interpretacao', 'pc']),
            }
            resumo = {}
            for eixo, c in cons.items():
                if not c:
                    resumo[eixo] = {'mais_comum': None, 'contagem': {}}
                    continue
                top = max(c, key=c.get)
                n_tot = sum(c.values())
                resumo[eixo] = {
                    'mais_comum': top, 'n': c[top], 'de': n_tot,
                    'concordancia_pct': round(c[top] / n_tot * 100),
                    'contagem': c,
                    'unanime': len(c) == 1 and n_tot > 1}

            n_maus = sum(1 for x in fora
                         if (x.get('pct_artefacto') or 0) > 30)
            return jsonify({
                'status': 'ok', 'n_sessoes': len(fora),
                'sessoes': fora, 'consenso': resumo,
                'n_com_artefacto_alto': n_maus,
                'nota': ('o consenso e uma contagem, nao uma media: com 3 '
                         'sessoes a dizer periferico e 2 cardiaco, o que '
                         'interessa e que ha 2 a discordar, nao que 60% '
                         'ganha. Concordancia abaixo de 70% significa que '
                         'nao ha padrao estavel'
                         + (f'. {n_maus} sessao(oes) com mais de 30% de '
                            'artefacto na cinta -- as leituras de FC dessas '
                            'nao sao de confianca' if n_maus else ''))})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/moxy/limiares/<path:activity_id>')
    def api_moxy_limiares(activity_id):
        """Breakpoints de SmO2, CER e detector de hipocapnia.

        ?inicio=&fim=   ?corte_plato=5   ?janela_plato=30
        ?estavel=0.5    declive de SmO2 abaixo do qual e' estavel
        ?exaustao=1     os blocos terminaram por exaustao
        """
        try:
            import os as _os
            import sys as _sys
            _sys.path.insert(0, _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'utils'))
            import nirs_breakpoints as nbk

            aid = str(activity_id).strip().strip('/').split('/')[-1]
            corpo = api_moxy_dados(aid)
            d = corpo[0].get_json() if isinstance(corpo, tuple) \
                else corpo.get_json()
            if not d or d.get('status') != 'ok':
                return jsonify({'status': 'sem_dados',
                                'mensagem': (d or {}).get('mensagem')}), 200

            t = d.get('tempo') or []
            canais = d.get('canais') or {}
            blocos = ((d.get('blocos') or {}).get('blocos')) or []
            ini = request.args.get('inicio', type=float)
            fim = request.args.get('fim', type=float)
            if ini is not None or fim is not None:
                a = ini if ini is not None else (t[0] if t else 0)
                b = fim if fim is not None else (t[-1] if t else 0)
                blocos = [x for x in blocos if x['t1'] >= a and x['t0'] <= b]

            smo2 = canais.get('smo2') or []
            # min e delta de SmO2 por bloco de trabalho
            ons = []
            for b in blocos:
                if not b.get('on'):
                    continue
                vs = [smo2[i] for i in range(min(len(t), len(smo2)))
                      if b['t0'] <= t[i] <= b['t1'] and smo2[i] is not None]
                if not vs:
                    continue
                bb = dict(b)
                bb['smo2_min'] = min(vs)
                bb['smo2_medio'] = sum(vs) / len(vs)
                bb['delta_smo2'] = nbk.delta_smo2_do_bloco(
                    t, smo2, b['t0'], b['t1'])
                ons.append(bb)

            mod = None
            try:
                import db as _db
                r = _db._exec("SELECT type FROM activities WHERE id = ?",
                              (aid,), fetch='one')
                if r:
                    from config import TYPE_MAP
                    mod = TYPE_MAP.get(r[0])
            except Exception:
                pass

            # Metodo principal: padrao de dessaturacao por bloco. E' o que
            # corresponde a este protocolo. A regressao segmentada fica como
            # secundaria, porque foi desenhada para rampa continua.
            mlss = nbk.mlss_por_dessaturacao(
                t, smo2, blocos,
                estavel=request.args.get('estavel', type=float)
                or nbk.ESTAVEL_POR_MIN)
            # Breakpoint pela TAXA de dessaturacao: e' o metodo que serve a
            # blocos curtos e a poucos degraus, e o que o Rogers usa nas
            # escadas. A regressao sobre os minimos fica como terceira via.
            # Metodo canonico do Arnold para este protocolo: media do
            # ultimo minuto de cada degrau, e classificacao do perfil.
            perfil = nbk.perfil_de_resposta(t, smo2, blocos)
            bp_taxa = nbk.bp_por_taxa(t, smo2, blocos)
            bp = nbk.breakpoints(ons, mod)
            pl = nbk.plato(t, smo2,
                           janela=request.args.get('janela_plato', type=int)
                           or nbk.PLATO_JANELA_S,
                           corte=request.args.get('corte_plato', type=float)
                           or nbk.PLATO_CORTE) if smo2 else None
            ensaios = [(b['delta_smo2'], b['t1'] - b['t0']) for b in ons
                       if b.get('delta_smo2') is not None]
            ce = nbk.cer(
                ensaios,
                ate_exaustao=request.args.get('exaustao') == '1')
            hp = nbk.hipocapnia(blocos, t, canais)

            return jsonify({
                'status': 'ok', 'activity_id': aid, 'modalidade': mod,
                'perfil_resposta': perfil,
                'mlss_dessaturacao': mlss,
                'bp_taxa': bp_taxa,
                'breakpoints': bp, 'plato': pl, 'cer': ce, 'hipocapnia': hp,
                'blocos_usados': [
                    {'watts': b.get('watts_medio'),
                     'smo2_min': round(b['smo2_min'], 1),
                     'delta_smo2': b.get('delta_smo2'),
                     'duracao_s': round(b['t1'] - b['t0'])} for b in ons]})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    VERSAO_ANALISE = '2026-08-30'

    @app.route('/api/moxy/analise/<path:activity_id>', methods=['POST'])
    def api_moxy_guardar_analise(activity_id):
        """Corre tudo e grava o resultado.

        Gravar de novo a mesma actividade SUBSTITUI: quando o método
        melhora, basta voltar a correr e o registo fica com a versão nova.
        A versao_analise diz com que código o valor foi calculado, para
        nao se comparar um BP de hoje com um de um método antigo.
        """
        try:
            import drive_db_perfil as ddp
            aid = str(activity_id).strip().strip('/').split('/')[-1]

            lim = api_moxy_limiares(aid)
            lim = lim[0].get_json() if isinstance(lim, tuple) else lim.get_json()
            itp = api_moxy_interpretacao(aid)
            itp = itp[0].get_json() if isinstance(itp, tuple) else itp.get_json()
            rd = api_moxy_rede(aid)
            rd = rd[0].get_json() if isinstance(rd, tuple) else rd.get_json()

            if (lim or {}).get('status') != 'ok':
                return jsonify({'status': 'sem_dados',
                                'mensagem': (lim or {}).get('mensagem')}), 200

            pf = lim.get('perfil_resposta') or {}
            ml = lim.get('mlss_dessaturacao') or {}
            bt = lim.get('bp_taxa') or {}
            ip = (itp or {}).get('interpretacao') or {}
            pt = (itp or {}).get('pontuacao') or {}
            rl = (rd or {}).get('limitador') or {}

            bp2 = (ml.get('mlss_estimado') if ml.get('ok')
                   else bt.get('bp_watts') if bt.get('ok') else None)
            s2 = MX_SESSOES_CACHE.get(aid, {})
            linha = (
                aid, lim.get('modalidade'), s2.get('data'),
                pf.get('perfil'), pf.get('bp1_watts'), None,
                bp2, None,
                ('padrão de dessaturação' if ml.get('ok')
                 else 'quebra na taxa' if bt.get('ok') else None),
                pf.get('smo2max'), pf.get('smo2min'),
                len([x for x in ((lim.get('blocos_usados')) or [])]),
                (pt.get('us') or {}).get('score'),
                (ip.get('us') or {}).get('limitador'),
                (pt.get('pc') or {}).get('score'),
                (ip.get('pc') or {}).get('limitador'),
                rl.get('sistema'),
                json.dumps(rl.get('controlo_pct') or {}, ensure_ascii=False),
                ((lim.get('hipocapnia') or {}).get('z_maximo')),
                None, None, VERSAO_ANALISE,
                json.dumps({'limiares': lim, 'i515': itp, 'rede': rd},
                           ensure_ascii=False)[:400000],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

            cn = ddp.get_conn()
            cn.execute(
                """INSERT OR REPLACE INTO moxy_analises
                   (activity_id, modalidade, data, perfil, bp1_w, bp1_bpm,
                    bp2_w, bp2_bpm, bp2_origem, smo2max, smo2min, n_degraus,
                    us_score, us_limitador, pc_score, pc_limitador,
                    rede_limitador, rede_pct, pct_artefacto, corte_inicio_s,
                    corte_fim_s, versao_analise, json_completo, data_gravacao)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                linha)
            cn.commit()
            ok, det = ddp.upload()
            cn.close()
            return jsonify({'status': 'ok' if ok else 'gravado_sem_upload',
                            'activity_id': aid, 'versao': VERSAO_ANALISE,
                            'drive': det})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/moxy/analises')
    def api_moxy_analises():
        """Análises gravadas.  ?modalidade=Row"""
        try:
            import drive_db_perfil as ddp
            cond, args = [], []
            if request.args.get('modalidade'):
                cond.append('modalidade = ?')
                args.append(request.args['modalidade'])
            w = ('WHERE ' + ' AND '.join(cond)) if cond else ''
            cn = ddp.get_conn()
            cols = ('activity_id, modalidade, data, perfil, bp1_w, bp2_w, '
                    'bp2_origem, smo2max, smo2min, n_degraus, us_limitador, '
                    'pc_limitador, rede_limitador, versao_analise, '
                    'data_gravacao')
            rows = cn.execute(
                f'SELECT {cols} FROM moxy_analises {w} ORDER BY data DESC',
                tuple(args)).fetchall()
            cn.close()
            nomes = [c.strip() for c in cols.split(',')]
            return jsonify({'status': 'ok', 'n': len(rows),
                            'analises': [dict(zip(nomes, r)) for r in rows]})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

    @app.route('/api/moxy/debug/<path:activity_id>')
    def api_moxy_debug(activity_id):
        """Tudo em bruto de uma sessão, para diagnóstico.

        Existe para poder pedir-se o output e ver o que se passou, em vez
        de adivinhar a partir do que aparece no ecrã.
        """
        try:
            aid = str(activity_id).strip().strip('/').split('/')[-1]
            fora = {'activity_id': aid, 'versao': VERSAO_ANALISE}
            for nome, fn in (('dados', api_moxy_dados),
                             ('limiares', api_moxy_limiares),
                             ('interpretacao', api_moxy_interpretacao),
                             ('rede', api_moxy_rede)):
                try:
                    r = fn(aid)
                    j = r[0].get_json() if isinstance(r, tuple) else r.get_json()
                    if nome == 'dados' and isinstance(j, dict):
                        # streams inteiros nao servem para nada aqui
                        j = {k: v for k, v in j.items() if k != 'canais'}
                        j['canais_resumo'] = {
                            k: {'n': len(v),
                                'min': min([x for x in v if x is not None],
                                           default=None),
                                'max': max([x for x in v if x is not None],
                                           default=None)}
                            for k, v in ((r[0].get_json() if isinstance(r, tuple)
                                          else r.get_json()).get('canais')
                                         or {}).items()}
                    fora[nome] = j
                except Exception as e:
                    fora[nome] = {'erro': f'{type(e).__name__}: {e}',
                                  'trace': traceback.format_exc()[-1200:]}
            return jsonify(fora)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    @app.route('/api/moxy/corte', methods=['POST'])
    def api_moxy_corte():
        """Grava o intervalo a analisar de uma sessao.

        Corpo: activity_id, inicio_s, fim_s, modalidade, data, nota.
        Gravar de novo a mesma actividade substitui -- a chave e' o id.
        """
        try:
            import drive_db_perfil as ddp
            c = request.get_json(silent=True) or {}
            aid = str(c.get('activity_id') or '').strip()
            if not aid:
                return jsonify({'status': 'erro',
                                'mensagem': 'activity_id em falta'}), 400
            cn = ddp.get_conn()
            cn.execute(
                """INSERT OR REPLACE INTO moxy_cortes
                   (activity_id, modalidade, data, inicio_s, fim_s, origem,
                    proposto_s, nota, data_gravacao)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (aid, c.get('modalidade'), c.get('data'),
                 c.get('inicio_s'), c.get('fim_s'),
                 c.get('origem') or 'utilizador', c.get('proposto_s'),
                 c.get('nota'),
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            cn.commit()
            ok, det = ddp.upload()
            cn.close()
            return jsonify({'status': 'ok' if ok else 'gravado_sem_upload',
                            'activity_id': aid, 'drive': det})
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app
