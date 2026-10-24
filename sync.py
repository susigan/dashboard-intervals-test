"""Sincronizacao Intervals.icu -> Postgres."""

import json
import time
from datetime import datetime, timedelta

import db
from config import ATHLETE_ID, ANOS_HISTORICO
from api_client import (icu_get, icu_get_many, norm_tipo, num,
                        kj_da_atividade, parse_streams, athlete_id_real)


def _ts(v):
    """ISO-8601 da API -> datetime, tolerante a sufixos."""
    if not v or not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


RECONCILIACAO_MAX_DIAS = 365


def _assinatura_relevante(a):
    """O que a deteccao usa: tags, intervalos e o indicador de potencia ignorada."""
    return json.dumps([a.get('tags'), a.get('interval_summary'), a.get('icu_ignore_power')],
                      sort_keys=True, ensure_ascii=False, default=str)


def reconciliar_antigas(dias_inicio=21, dias_fim=120):
    """Reconciliacao DIRECIONADA de atividades antigas, fora da janela incremental.

    Custo fixo: UM pedido a API para a janela [hoje - dias_fim, hoje - dias_inicio], seja qual for o numero de
    atividades. Compara so o que a deteccao usa (tags, interval_summary, icu_ignore_power) com o JSON ja guardado
    e grava apenas as atividades novas ou alteradas (idempotente: a segunda execucao sem mudancas grava nada).
    Nao toca nas curvas de potencia: isso e feito por /api/sync (curvas de 5 anos).
    `newest` e enviado, mas a janela e reaplicada localmente (ver abaixo): a correcao nao depende
    de a API interpretar `newest` de forma especifica."""
    if not db.ENABLED:
        return {'ok': False, 'erro': 'DATABASE_URL nao configurada'}
    dias_inicio = max(0, int(dias_inicio))
    dias_fim = min(RECONCILIACAO_MAX_DIAS, int(dias_fim))
    if dias_fim <= dias_inicio:
        return {'ok': False, 'erro': 'janela invalida: dias_fim tem de ser maior que dias_inicio'}
    t0 = time.time()
    hoje = datetime.now().date()
    oldest = hoje - timedelta(days=dias_fim)
    newest = hoje - timedelta(days=dias_inicio)
    data, err = icu_get(f"/athlete/{ATHLETE_ID}/activities",
                        {"oldest": oldest.isoformat(), "newest": newest.isoformat()}, timeout=120)
    if err:
        return {'ok': False, 'erro': err, 'pedidos': 1}
    acts_recebidas = data if isinstance(data, list) else data.get("data", [])
    # A janela aplica-se TAMBEM localmente: se a API ignorar `newest` ou o tratar de forma inclusiva/exclusiva,
    # nada fora de [oldest, newest] e escrito. Escrita por id: nunca duplica.
    dentro = [a for a in acts_recebidas
              if oldest.isoformat() <= (a.get('start_date_local') or '')[:10] <= newest.isoformat()]
    fora_da_janela = len(acts_recebidas) - len(dentro)
    acts = list({a['id']: a for a in dentro if a.get('id')}.values())   # um id, uma classificacao
    guardadas = {r['id']: r for r in (db.load_activities(desde=oldest.isoformat()) or []) if r.get('id')}
    alteradas, novas = [], []
    for a in acts:
        if not a.get('id') or to_row(a) is None:
            continue
        ant = guardadas.get(a['id'])
        if ant is None:
            novas.append(a)
        elif _assinatura_relevante(ant) != _assinatura_relevante(a):
            alteradas.append(a)
    rows = [to_row(a) for a in alteradas + novas]
    ins, upd = db.upsert_activities(rows) if rows else (0, 0)
    return {'ok': True, 'modo': 'reconciliacao', 'pedidos': 1,
            'janela': [oldest.isoformat(), newest.isoformat()], 'recebidas': len(acts_recebidas),
            'fora_da_janela': fora_da_janela,
            'alteradas': [a['id'] for a in alteradas], 'novas': [a['id'] for a in novas],
            'inseridas': ins, 'actualizadas': upd, 'segundos': round(time.time() - t0, 2)}


def to_row(a):
    """Actividade da API -> linha da tabela. O JSON completo vai para 'raw',
    por isso nada se perde: as colunas sao so para indexar e agregar."""
    d = (a.get('start_date_local') or '')[:10]
    if len(d) != 10:
        return None
    return {
        'id': a.get('id'),
        'athlete_id': a.get('icu_athlete_id'),
        'date': d,
        'start_local': _ts(a.get('start_date_local')),
        'type_raw': a.get('type'),
        'type': norm_tipo(a.get('type')),
        'name': a.get('name'),
        'elapsed_time': int(num(a.get('elapsed_time'))) or None,
        'moving_time': int(num(a.get('moving_time'))) or None,
        'distance_m': num(a.get('icu_distance')) or num(a.get('distance')),
        'kj': kj_da_atividade(a),
        'kj_acima_ftp': num(a.get('icu_joules_above_ftp')) / 1000.0,
        'z1_kj': num(a.get('Z1KJ')), 'z2_kj': num(a.get('Z2KJ')), 'z3_kj': num(a.get('Z3KJ')),
        'z1_sec': num(a.get('Z1sec')), 'z2_sec': num(a.get('Z2sec')), 'z3_sec': num(a.get('Z3sec')),
        'training_load': num(a.get('icu_training_load')),
        'rpe': num(a.get('icu_rpe')) or None,
        'xss': num(a.get('SS')),
        'aerobic': num(a.get('Aerobic')),
        'glycolytic': num(a.get('Glycolytic')),
        'sprint': num(a.get('Pmax')),
        'epoc': num(a.get('EPOC')),
        'elevation': num(a.get('total_elevation_gain')),
        'avg_hr': num(a.get('average_heartrate')),
        'max_hr': num(a.get('max_heartrate')),
        'avg_watts': num(a.get('icu_weighted_avg_watts')) or num(a.get('icu_average_watts')),
        'ftp': num(a.get('icu_pm_ftp')) or num(a.get('icu_ftp')),
        'source': a.get('source'),
        'icu_sync_date': _ts(a.get('icu_sync_date')),
        'analyzed': _ts(a.get('analyzed')),
        'raw': a,
    }


def sync_activities(modo='incremental', dias_recuo=21):
    """
    full        — puxa ANOS_HISTORICO anos e reescreve tudo
    incremental — puxa desde (data mais recente na BD - dias_recuo)

    O recuo existe porque a Intervals.icu recalcula actividades antigas
    quando mudas FTP ou zonas: icu_pm_ftp, Z1/Z2/Z3 KJ e icu_training_load
    sao derivados e mudam retroactivamente. Sem recuo, ficarias com valores
    obsoletos na base.
    """
    if not db.ENABLED:
        return {'ok': False, 'erro': 'DATABASE_URL nao configurada'}

    t0 = time.time()
    hoje = datetime.now()

    if modo == 'full':
        oldest = (hoje - timedelta(days=int(365.25 * ANOS_HISTORICO))).date()
    else:
        ult = db.ultima_data()
        oldest = ((ult - timedelta(days=dias_recuo)) if ult
                  else (hoje - timedelta(days=int(365.25 * ANOS_HISTORICO))).date())

    data, err = icu_get(f"/athlete/{ATHLETE_ID}/activities",
                        {"oldest": oldest.strftime("%Y-%m-%d")}, timeout=120)
    if err:
        db.log_sync(modo, oldest, 0, 0, 0, time.time() - t0, err)
        return {'ok': False, 'erro': err}

    acts = data if isinstance(data, list) else data.get("data", [])
    rows = [r for r in (to_row(a) for a in acts) if r]
    ins, upd = db.upsert_activities(rows)
    secs = round(time.time() - t0, 2)
    db.log_sync(modo, oldest, len(acts), ins, upd, secs)

    return {'ok': True, 'modo': modo, 'oldest': oldest.isoformat(),
            'recebidas': len(acts), 'inseridas': ins, 'actualizadas': upd,
            'segundos': secs}


# Tipos crus da Intervals.icu por modalidade. O parametro 'type' do endpoint
# aceita um desporto e inclui os tipos relacionados, mas ser explicito evita
# surpresas com VirtualRide/VirtualRow.
# Fallback: nome do "sport" na API por modalidade. So usado se a base ainda
# nao tiver actividades dessa modalidade — normalmente os tipos vem de la.
TIPOS_API = {'Bike': 'Ride', 'Row': 'Rowing', 'Run': 'Run', 'Ski': 'AlpineSki'}


def tipos_reais():
    """Tipos crus por modalidade, lidos da base.

    Evita adivinhar como a API chama cada desporto: se tens VirtualRow e
    Rowing, pedimos os dois. O parametro 'type' agrupa variantes do mesmo
    desporto, por isso pedir a mais nao faz mal — pedir o nome errado faz.
    """
    if not db.ENABLED:
        return {}
    rows = db._exec("""SELECT type, type_raw, COUNT(*) FROM activities
                       WHERE type_raw IS NOT NULL AND type <> 'WeightTraining'
                       GROUP BY type, type_raw ORDER BY COUNT(*) DESC""",
                    fetch='all') or []
    out = {}
    for mod, raw, _n in rows:
        out.setdefault(mod, []).append(raw)
    return out


def sync_power_curves(modalidades=None, anos=None):
    """Curvas de potencia por sessao, para calcular recordes.

    /athlete/{id}/activity-power-curves devolve, NUMA SO chamada por
    modalidade, o melhor valor de cada duracao para todas as sessoes do
    periodo. E o que substitui os custom fields MMP: aqui ficamos com o
    numero e a data, e podemos comparar com o historico.
    """
    if not db.ENABLED:
        return {'ok': False, 'erro': 'DATABASE_URL nao configurada'}

    t0 = time.time()
    anos = anos or ANOS_HISTORICO
    oldest = (datetime.now() - timedelta(days=int(365.25 * anos))).strftime("%Y-%m-%d")
    newest = datetime.now().strftime("%Y-%m-%d")
    secs = ','.join(str(s) for s in db.DURACOES)

    id_atleta = athlete_id_real()
    reais = tipos_reais()
    mods = modalidades or (list(reais) or list(TIPOS_API))
    total, detalhe = 0, {}
    vistos = set()

    for mod in mods:
        # um pedido por tipo cru; o primeiro costuma trazer ja todos os do
        # mesmo desporto, e os seguintes so confirmam
        candidatos = reais.get(mod) or [TIPOS_API.get(mod, mod)]
        rows_mod, erros_mod, usados = [], [], []

        for tipo_api in candidatos:
            params = {"oldest": oldest, "newest": newest,
                      "type": tipo_api, "secs": secs}
            data, err = icu_get(f"/athlete/{id_atleta}/activity-power-curves",
                                params, timeout=180)
            # "0" significa "eu proprio" nas actividades, mas este endpoint
            # nao resolve o atalho e responde 403. Tentamos o id numerico.
            if err and '403' in err and id_atleta != ATHLETE_ID:
                data, err = icu_get(f"/athlete/{ATHLETE_ID}/activity-power-curves",
                                    params, timeout=180)
            if err:
                erros_mod.append(f"{tipo_api}: {err}")
                continue
            usados.append(tipo_api)

            secs_resp = (data or {}).get('secs') or db.DURACOES
            for c in ((data or {}).get('curves') or []):
                watts = c.get('watts')
                aid = c.get('id')
                d = (c.get('start_date_local') or '')[:10]
                if not watts or not aid or len(d) != 10 or aid in vistos:
                    continue
                vistos.add(aid)
                rows_mod.append({
                    'activity_id': aid, 'type': mod, 'date': d,
                    'weight': c.get('weight'), 'secs': secs_resp, 'watts': watts,
                })

        n = db.upsert_power_curves(rows_mod)
        # Historico verificavel (Etapa 13): so acrescenta versao quando o conteudo de uma curva mudou.
        # Sem a migracao aplicada, ou em qualquer erro, nao faz nada e a sincronizacao segue.
        if rows_mod:
            try:
                import modelo_ir_curvas
                from datetime import timezone as _tz
                modelo_ir_curvas.registar_seguro(rows_mod, datetime.now(_tz.utc).replace(tzinfo=None))
            except Exception:  # noqa: BLE001
                pass
        total += n
        detalhe[mod] = {'ok': not erros_mod or n > 0, 'sessoes': n,
                        'tipos_pedidos': candidatos, 'tipos_ok': usados}
        if erros_mod:
            detalhe[mod]['erros'] = erros_mod

    resultado = {'ok': True, 'oldest': oldest, 'total': total, 'athlete_id': id_atleta,
                 'por_modalidade': detalhe, 'tipos_na_base': reais,
                 'segundos': round(time.time() - t0, 2)}
    # Deteccao automatica de observacoes MMP (season, all-time, recente): corre sobre as curvas
    # que acabaram de ser gravadas. So faz INSERT na sua propria tabela; uma falha aqui nunca
    # estraga a sincronizacao das curvas.
    try:
        import observacoes_auto
        resultado['observacoes_auto'] = observacoes_auto.atualizar(origem='sync')
    except Exception as e:
        import traceback
        traceback.print_exc()
        resultado['observacoes_auto'] = {'ok': False, 'erro': f'{type(e).__name__}: {e}'}
    # Modelo IR (experimental): recalibra so as unidades cujas observacoes MMP elegiveis (ou a
    # carga ate ao ultimo esforco) mudaram; sem mudancas nao grava nada. Falhar nunca estraga
    # a sincronizacao.
    try:
        import modelo_ir_servico
        resultado['modelo_ir'] = modelo_ir_servico.apos_deteccao(
            resultado.get('observacoes_auto'), origem='sync')
    except Exception as e:
        import traceback
        traceback.print_exc()
        resultado['modelo_ir'] = {'ok': False, 'erro': f'{type(e).__name__}: {e}'}
    return resultado


def sync_streams(activity_id):
    """Guarda os streams de UMA actividade. Chamado quando abres o detalhe
    (lazy loading): nunca fazemos bulk de milhares de requests."""
    if not db.ENABLED:
        return None
    sdata, err = icu_get(f"/activity/{activity_id}/streams",
                         {"includeDefaults": "true"}, timeout=60)
    if err or not sdata:
        return None
    streams, meta, _ = parse_streams(sdata)
    if streams:
        db.upsert_streams(activity_id, meta, streams)
    return streams, meta


def sync_streams_bloco(limite=60, tipos=None, desde=None):
    """Carrega streams de varias sessoes de uma vez.

    Um pedido por sessao — por isso vai em blocos. Com limite=60 podes correr
    varias vezes seguidas sem chegar perto dos 2500/15min da Intervals.icu.
    Devolve quantas faltam, para saberes se precisas de repetir.
    """
    if not db.ENABLED:
        return {'ok': False, 'erro': 'DATABASE_URL nao configurada'}

    t0 = time.time()
    pendentes = db.actividades_sem_streams(limite, tipos, desde)
    if not pendentes:
        return {'ok': True, 'nada_a_fazer': True, 'streams': db.streams_stats()}

    pedidos = {a['id']: (f"/activity/{a['id']}/streams",
                         {"includeDefaults": "true"}) for a in pendentes}
    res = icu_get_many(pedidos, max_workers=4)

    ok, vazias, erros = 0, 0, 0
    for aid, (sdata, err) in res.items():
        if err:
            erros += 1
            continue
        streams, meta, _w = parse_streams(sdata)
        if streams:
            db.upsert_streams(aid, meta, streams)
            ok += 1
        else:
            vazias += 1

    st = db.streams_stats()
    return {'ok': True, 'pedidas': len(pendentes), 'guardadas': ok,
            'sem_streams': vazias, 'erros': erros,
            'segundos': round(time.time() - t0, 2),
            'faltam': st.get('por_carregar'),
            'streams': st}
