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

    @app.route('/api/moxy/dados/<path:activity_id>')
    def api_moxy_dados(activity_id):
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
                    return jsonify({
                        'status': 'removida',
                        'mensagem': ('esta actividade ja nao existe na '
                                     'Intervals.icu e foi removida da base '
                                     'local. Provavelmente foi apagada e '
                                     'recarregada com outro id -- carrega em '
                                     '"Actualizar sessões" para apanhar a nova'),
                        'id_usado': aid, 'linhas_removidas': removidas}), 200
                return jsonify({'status': 'erro', 'mensagem': f'API: {err}',
                                'id_usado': aid}), 200
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
                return jsonify({
                    'status': 'sem_dados', 'id': aid,
                    'mensagem': ('sem streams de SmO2 ou THb reconhecidos. '
                                 'Se algum dos streams abaixo for o sensor '
                                 '(pode chegar como dev_field_N sem nome), '
                                 'acrescenta o nome em CANAIS no api_moxy.py'),
                    'streams_na_actividade': sorted(streams),
                    'detalhe_dos_streams': detalhe}), 200

            n = len(next(iter(canais.values())))
            tempo = streams.get('time') or list(range(n))

            res = mn.processar(
                tempo, canais, hz=1.0,
                acima=request.args.get('acima', type=float) or 95.0,
                corte_outlier=request.args.get('outlier', type=float) or 3.0,
                fc=request.args.get('fc', type=float) or 0.02,
                normalizar=request.args.get('normalizar'))

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
            art_max = request.args.get('artefacto_max', type=float) or 5.0
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
                        corte_outlier=request.args.get('outlier', type=float) or 3.0,
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

            # blocos ON/OFF e proposta de corte
            wt = res['canais'].get('watts')
            if wt:
                bl = mn.detectar_blocos(res['tempo'], wt, hz=1.0)
                res['blocos'] = bl
                res['corte_proposto'] = mn.propor_corte(bl)
            else:
                res['blocos'] = {'ok': False, 'motivo': 'sem potencia'}
                res['corte_proposto'] = {'ok': False, 'motivo': 'sem potencia'}

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
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

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
