"""Tab PMC — Performance Management Chart.

Carga vem da Intervals.icu (icu_training_load, ja na base de dados).
Wellness e composicao corporal vem dos Google Sheets, como no dashboard
original: HRV/RMSSD, HRR, sono, stress, fadiga, humor, dores musculares,
peso, gordura, calorias e macros.
"""

import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from flask import jsonify, request

import db
import pmc
import sheets_client as sheets
from api_client import fetch_activities, norm_tipo, num
from config import CICLICOS, CORES_MOD, ANOS_HRV, limite_hrv
from tabs.base import page, explicacao

SLUG = 'pmc'


# Validacao do CP final (depois da selecao, tambem para o fallback).
PISO_CP_W = 50.0        # C1: CP abaixo disto nao e plausivel em Bike/Row/Ski/Run
P1_MAX_W = 2000.0       # C3: P(1 s) acima disto e suspeito; so rejeita com divergencia
TOL_DIVERGENCIA = 0.20  # C2: divergencia curva vs icu_pm_cp (confirma C3; alerta)
MODS_CP = ('Bike', 'Row', 'Ski', 'Run')


def cp_valido(v, tipo=None):
    """True se v e um CP utilizavel. Booleanos, nao finitos, nao positivos e,
    nas modalidades com CP, valores abaixo de PISO_CP_W sao recusados."""
    if v is None or isinstance(v, bool):
        return False
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(x) or x <= 0:
        return False
    if tipo in MODS_CP and x < PISO_CP_W:
        return False
    return True


def _campo_cp(a, tipo=None):
    """Campo da Intervals.icu que da o CP da sessao (ja validado), ou None."""
    for campo in ('icu_pm_cp', 'icu_pm_ftp'):
        if cp_valido(num(a.get(campo)) if not isinstance(a.get(campo), bool) else None, tipo):
            return campo
    return None


def cp_bruto_da_atividade(a, tipo=None):
    """CP de uma sessao a partir dos campos da Intervals.icu (sem a curva).

    Ordem: icu_pm_cp e depois icu_pm_ftp. icu_rolling_ftp, EFTP e icu_ftp nao
    entram: sao estimativas de FTP, nao de CP, e divergem do CP da curva
    aceite (ver auditoria do CP). Um candidato que falha cp_valido passa ao
    seguinte; se nenhum passa, devolve None.
    """
    campo = _campo_cp(a, tipo)
    return num(a.get(campo)) if campo else None


def curva_suspeita(cp_curva, p1, cp_icu):
    """C3: a curva e rejeitada so se P(1 s) > P1_MAX_W E a curva divergir mais
    de TOL_DIVERGENCIA do icu_pm_cp. Um pico de 1 s isolado pode ser sprint
    real; a divergencia da fonte confirma a corrupcao. Sem icu_pm_cp nao ha
    confirmacao e a curva nao e rejeitada por este criterio."""
    if p1 is None or p1 <= P1_MAX_W or not cp_icu:
        return False
    return abs(cp_curva / cp_icu - 1) > TOL_DIVERGENCIA


def selecionar_cp(sessoes, cp_curva):
    """CP final de cada sessao: curva aceita, senao icu_pm_cp, senao icu_pm_ftp.

    Cada candidato passa por cp_valido e, para a curva, por curva_suspeita.
    Se nenhum passa, a sessao fica com CP None (nunca zero nem valor artificial).
    Altera sessoes[i]['cp'] e ['w_prime']; devolve contadores para a interface.
    """
    cont = {'da_curva': 0, 'do_icu_pm_cp': 0, 'do_icu_pm_ftp': 0,
            'curva_rejeitada_piso': 0, 'curva_rejeitada_p1': 0,
            'alerta_divergencia_icu': 0, 'sem_cp': 0}
    for s in sessoes:
        tipo = s.get('type')
        if tipo == 'WeightTraining':   # fora das analises de CP
            s['cp'] = None
            continue
        icu = s.get('cp')          # ja validado em cp_bruto_da_atividade
        r = cp_curva.get(s['id'])
        if r:
            if not cp_valido(r['cp'], tipo):
                cont['curva_rejeitada_piso'] += 1
            elif curva_suspeita(r['cp'], r.get('p1'), icu):
                cont['curva_rejeitada_p1'] += 1
            else:
                s['cp'] = r['cp']
                s['w_prime'] = r['w_prime'] or s.get('w_prime')
                cont['da_curva'] += 1
                if icu and abs(r['cp'] / icu - 1) > TOL_DIVERGENCIA:
                    cont['alerta_divergencia_icu'] += 1
                continue
        if icu is not None:
            if s.get('cp_campo') == 'icu_pm_ftp':
                cont['do_icu_pm_ftp'] += 1
            else:
                cont['do_icu_pm_cp'] += 1
        else:
            s['cp'] = None
            if tipo in MODS_CP:
                cont['sem_cp'] += 1
    return cont


def eftp_da_atividade(a):
    """Campo 'EFTP' (semântica não confirmada; não é FTP medido nem eFTP persistente do atleta).
    Uso restrito à comparação com o CP da curva."""
    return num(a.get('EFTP'))


def _sheets(force=False):
    """Wellness e composicao corporal. A cache vive no sheets_client, para
    que as tabs nao dependam umas das outras."""
    return sheets.carregar(force)


_cache_pmc = {'chave': None, 'valor': None, 'time': None}
TTL_PMC = 900
# Versao da chave da cache do FMT. Subir sempre que o payload ganhar campos
# que dependam de codigo novo: assim nenhuma resposta antiga e reutilizada.
# v2: inclui evolucao_performance (o FMT em si nao muda).
# v3: evolucao_performance renomeia ftp_estimado -> ftp_configurado e deixa de excluir
#     sequencias sem evidencia (so sinaliza).
# v4: icu_pm_ftp passa a ser eftp_atividade (eFTP da atividade); ftp_medido e eftp
#     deixam de existir; o payload da evolucao ganha a chave 'fontes'.
CACHE_VERSAO_PMC = 4


def _digest(obj):
    """SHA-256 de uma estrutura JSON canonica.

    A chave da cache e o digest das ENTRADAS efetivas do calculo, nao o numero
    de sessoes nem a ultima data: qualquer alteracao de HRV, CP, carga, janela
    (desde) ou completude muda a chave.
    """
    blob = json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _entradas_fmt(serie, sessoes, wellness, desde_hrv):
    """Campos que alteram o resultado de pmc.calcular_fmt, e nada mais."""
    return {
        'versao_cache': CACHE_VERSAO_PMC,
        'serie': [(d.get('date'), d.get('load')) for d in (serie or [])],
        'sessoes': [(s.get('id'), s.get('date'), s.get('type'), s.get('tl'),
                     s.get('cp'), s.get('w_prime')) for s in (sessoes or [])],
        'wellness': [(w.get('date'), w.get('hrv'), w.get('sleep_quality'),
                      w.get('stress'), w.get('soreness'), w.get('fatiga'))
                     for w in (wellness or [])],
        'desde_hrv': desde_hrv,
    }


def api_data():
    acts = fetch_activities()
    if not acts:
        return jsonify({'error': 'sem actividades'}), 500

    sessoes = []
    for a in acts:
        d = (a.get('start_date_local') or '')[:10]
        if len(d) != 10:
            continue
        sessoes.append({
            'id': a.get('id'), 'date': d, 'type': norm_tipo(a.get('type')),
            'name': a.get('name'), 'tl': num(a.get('icu_training_load')),
            'horas': (num(a.get('elapsed_time')) or num(a.get('moving_time'))) / 3600,
            'rpe': a.get('icu_rpe'), 'xss': num(a.get('SS')),
            # proxies de performance para ajustar o gamma
            'cp': cp_bruto_da_atividade(a, norm_tipo(a.get('type'))),
            'cp_campo': _campo_cp(a, norm_tipo(a.get('type'))),
            'w_prime': num(a.get('icu_pm_w_prime')) or None,
            # so' para a conversao pace<->watts da Run (regressao_pace_watts)
            'distancia_m': num(a.get('icu_distance')) or num(a.get('distance')),
            'watts_medio': (num(a.get('icu_average_watts'))
                           or num(a.get('average_watts'))),
            'duracao_s': num(a.get('moving_time')) or num(a.get('elapsed_time')),
            # eFTP da PROPRIA Intervals.icu, so' para comparar com o CP
            # da curva ajustada -- fontes diferentes, podem divergir
            'eftp_icu': eftp_da_atividade(a),
            # kJ -- para o modelo TRIMP~kJ (CTL vs KJ). icu_joules e' o
            # integral real do stream de potencia; watts_medio*duracao e'
            # so' o fallback quando falta o icu_joules
            'kj': (num(a.get('icu_joules')) / 1000.0
                  if num(a.get('icu_joules')) else None),
        })

    desde = request.args.get('desde') or None
    serie = pmc.calcular(sessoes, 'tl', desde=desde)
    mods = pmc.por_modalidade(sessoes, CICLICOS, 'tl', desde=desde)

    wellness, corporal, erros_sheets = _sheets()

    # CP ajustado a curva de potencia tem prioridade sobre o icu_pm_cp: aquele
    # e a estimativa de uma sessao isolada e, nos dias sem esforcos maximos,
    # subestima muito e enche a serie de ruido.
    cp_curva = {}
    try:
        for r in db.cp_por_sessao():
            if r['r2'] >= 0.80:      # so ajustes que sao mesmo uma recta
                cp_curva[r['activity_id']] = r
    except Exception as e:
        print(f"cp_por_sessao: {e}")
    cp_cont = selecionar_cp(sessoes, cp_curva)

    try:
        ftlm_res = pmc.calcular_ftlm(sessoes, wellness, serie, CICLICOS)
        erro_ftlm = None
    except Exception as e:
        import traceback
        traceback.print_exc()
        ftlm_res, erro_ftlm = None, f'{type(e).__name__}: {e}'

    # calcular_fmt corre a calibracao com permutacao — cara. A cache so e
    # reutilizada quando TODAS as entradas do calculo sao as mesmas. O erro do
    # FTLM (erro_ftlm) e independente desta cache e nunca e apagado por ela.
    desde_fmt = limite_hrv()
    chave_fmt = _digest(_entradas_fmt(serie, sessoes, wellness, desde_fmt))
    agora = datetime.now()
    if (_cache_pmc['chave'] == chave_fmt and _cache_pmc['time']
            and (agora - _cache_pmc['time']).total_seconds() < TTL_PMC):
        fmt_res = _cache_pmc['valor']
    else:
        fmt_res = None
    try:
        if fmt_res is None:
            fmt_res = pmc.calcular_fmt(sessoes, wellness, serie,
                                       desde_hrv=desde_fmt)
            # um erro de calculo (ou falta de dados) nao e um resultado valido:
            # nao fica em cache, e a proxima chamada tenta de novo
            if not (fmt_res or {}).get('erro'):
                _cache_pmc.update({'chave': chave_fmt, 'valor': fmt_res,
                                   'time': agora})
    except Exception as e:
        import traceback
        traceback.print_exc()
        fmt_res = {'erro': f'{type(e).__name__}: {e}'}

    # os parametros calibrados do FMT alimentam tambem o homeostatico
    par = (fmt_res or {}).get('params_usados') or {}
    cal_fmt = (fmt_res or {}).get('calibracao') or {}
    tau_ok = (cal_fmt.get('canal1_tau') or {}).get('fonte') == 'dados'
    lag_ok = (cal_fmt.get('canal2_lag') or {}).get('fonte') == 'dados'

    try:
        homeo = pmc.modelo_homeostatico(
            serie, sessoes,
            tau_sugerido=par.get('tau_carga') if tau_ok else None,
            lag_hrv_sugerido=par.get('lag_hrv') if lag_ok else None)
        homeo_mod = pmc.homeostatico_por_modalidade(serie, sessoes, CICLICOS)
        # a interface so mostra a serie como ajuste quando a validacao passa
        if homeo:
            homeo['validacao'] = pmc.estado_homeostatico(homeo)
        for _r in homeo_mod.values():
            _r['validacao'] = pmc.estado_homeostatico(_r)
        alos = pmc.indice_alostatico(
            serie, homeo, wellness,
            p_ant=(request.args.get('ant_ini'), request.args.get('ant_fim'))
                  if request.args.get('ant_ini') else None,
            p_rec=(request.args.get('rec_ini'), request.args.get('rec_fim'))
                  if request.args.get('rec_ini') else None)
    except Exception as e:
        import traceback
        traceback.print_exc()
        homeo, homeo_mod, alos = None, {}, None

    # Evolucao da performance. Le os campos CRUS de FTP (icu_pm_ftp, icu_ftp,
    # EFTP) e nao o campo 'ftp' da base, que mistura fontes e grava zero para
    # ausencia. Os campos sao passados como vem: None e ausencia, 0 e zero.
    entradas_evol = []
    for a in acts:
        d_ev = (a.get('start_date_local') or '')[:10]
        if len(d_ev) != 10:
            continue
        entradas_evol.append({
            'id': a.get('id'), 'date': d_ev, 'type': norm_tipo(a.get('type')),
            'icu_pm_ftp': a.get('icu_pm_ftp'), 'icu_ftp': a.get('icu_ftp'),
            'EFTP': a.get('EFTP'),
        })
    try:
        curvas_evol = db.load_power_curves() or []
    except Exception as e:
        print(f"load_power_curves (evolucao): {e}")
        curvas_evol = []
    try:
        evolucao = pmc.evolucao_performance(
            entradas_evol, curvas_evol, serie[-1]['date'] if serie else None)
    except Exception as e:
        import traceback
        traceback.print_exc()
        evolucao = {'status': 'erro', 'erro': f'{type(e).__name__}: {e}'}

    try:
        dtrimp_dkj = pmc.dtrimp_dkj(sessoes, CICLICOS)
    except Exception as e:
        import traceback
        traceback.print_exc()
        dtrimp_dkj = {}

    try:
        eficiencia = pmc.eficiencia_rolling(sessoes, CICLICOS)
    except Exception as e:
        import traceback
        traceback.print_exc()
        eficiencia = {}

    fim = serie[-1] if serie else {}
    return jsonify({
        'status': 'OK',
        'serie': serie,
        'por_modalidade': mods,
        'sessoes': sessoes,
        'wellness': wellness or [],
        'corporal': corporal or [],
        'escala_1a5': sheets.ESCALA_1A5,
        'erros_sheets': erros_sheets,
        'sheets_ok': sheets.disponivel(),
        'actual': {
            'ctl': fim.get('ctl'), 'atl': fim.get('atl'),
            'tsb': fim.get('tsb'), 'ramp': fim.get('ramp'),
            'estado': pmc.estado_forma(fim.get('tsb')),
        },
        'alertas': pmc.alertas(serie, wellness),
        'ftlm': ftlm_res, 'erro_ftlm': erro_ftlm,
        'cp_fonte': {**cp_cont,
            'nota': "curva ajustada a P(t)=W'/t+CP, 2-20 min, R2>=0.80; rejeitada "
                    "se P(1 s)>2000 W e divergir >20% do icu_pm_cp; piso 50 W em "
                    "Bike/Row/Ski/Run; sem valor plausivel = sem CP"},
        'fmt': fmt_res,
        'homeostatico': homeo, 'homeostatico_mod': homeo_mod,
        'alostatico': alos,
        'evolucao_performance': evolucao,
        'dtrimp_dkj': dtrimp_dkj,
        'eficiencia_kj': eficiencia,
        'cores': CORES_MOD, 'ciclicos': CICLICOS,
    })


_cache_cal = {'chave': None, 'valor': None, 'time': None}
TTL_CAL = 3600


def api_calibracao_dados():
    """Calibracao isolada, para inspeccao e exportacao.

    Corre o mesmo calculo do FMT mas devolve so os parametros e a evidencia.
    Nao e preciso exportar CSV nenhum: as series ja estao na base de dados.
    """
    acts = fetch_activities()
    if not acts:
        return {'erro': 'sem actividades'}

    sessoes = []
    for a in acts:
        d = (a.get('start_date_local') or '')[:10]
        if len(d) != 10:
            continue
        sessoes.append({
            'id': a.get('id'), 'date': d, 'type': norm_tipo(a.get('type')),
            'tl': num(a.get('icu_training_load')),
            'cp': cp_bruto_da_atividade(a, norm_tipo(a.get('type'))),
            'cp_campo': _campo_cp(a, norm_tipo(a.get('type'))),
            'w_prime': num(a.get('icu_pm_w_prime')) or None,
        })

    cp_curva = {}
    try:
        for r in db.cp_por_sessao():
            if r['r2'] >= 0.80:
                cp_curva[r['activity_id']] = r
    except Exception:
        pass
    selecionar_cp(sessoes, cp_curva)

    wellness, _c, _e = _sheets()
    serie = pmc.calcular(sessoes, 'tl')

    completo = request.args.get('completo') in ('1', 'true', 'yes')
    # ?desde=YYYY-MM-DD limita as analises que dependem de HRV: a janela
    # comeca nesse dia. Util quando as medicoes so comecaram a meio do historico.
    desde_pedido = request.args.get('desde')
    if desde_pedido and not re.match(r'^\d{4}-\d{2}-\d{2}$', desde_pedido):
        return {'erro': 'desde deve ser YYYY-MM-DD'}
    desde_hrv = desde_pedido or limite_hrv()
    # primeiro e ultimo dia com HRV real dentro da janela usada pelo calculo
    hrv_ini, hrv_fim, hrv_dias = pmc._janela_hrv(
        [d['date'] for d in serie], wellness, desde_hrv)
    # a chave cobre TODAS as entradas que alteram a resposta: janela (desde),
    # HRV, CP, sessoes, carga e completude
    chave = _digest({'entradas': _entradas_fmt(serie, sessoes, wellness, desde_hrv),
                     'completo': completo})
    agora = datetime.now()
    if (_cache_cal['chave'] == chave and _cache_cal['time']
            and (agora - _cache_cal['time']).total_seconds() < TTL_CAL):
        return {**_cache_cal['valor'], 'de_cache': True}

    res = pmc.calcular_fmt(sessoes, wellness, serie, desde_hrv=desde_hrv)
    if not res or res.get('erro'):
        return {'erro': (res or {}).get('erro', 'nao foi possivel calibrar')}

    cal = res.get('calibracao') or {}
    # O teste de eventos e barato; a segmentacao por ano/modalidade e a
    # ancora sao ~35 buscas com permutacao. Ficam atras de ?completo=1,
    # senao cada carregamento da tab espera minutos por elas.
    eventos = pmc.teste_eventos(sessoes, wellness, serie, CICLICOS,
                                desde=desde_hrv)
    # A ancora corre SEMPRE: e a analise com mais hipoteses de dar sinal, e
    # depois de vectorizar o _ewm e os percentis passou a custar segundos.
    try:
        # a CP de um teste de 2022 e comparavel com a de 2026, ao contrario
        # do HRV — por isso aqui usa-se o historico completo
        ancora = pmc.calibrar_com_ancora(serie, CICLICOS, atividades=acts)
    except Exception as e:
        import traceback
        traceback.print_exc()
        ancora = {'erro': f'{type(e).__name__}: {e}'}

    # A segmentacao por ano e modalidade sao ~30 buscas; fica atras da flag.
    if completo:
        segmentado = pmc.calibrar_segmentado(sessoes, wellness, serie, CICLICOS,
                                             desde=desde_hrv)
    else:
        segmentado = {'nota': 'por ano e modalidade — pedir com ?completo=1'}

    resposta = {
        'status': 'OK',
        'completo': completo,
        'dias': len(serie),
        'de': serie[0]['date'] if serie else None,
        'ate': serie[-1]['date'] if serie else None,
        'sessoes': len(sessoes),
        'cp_de_curva': len(cp_curva),
        'dimensoes_fmt': res.get('dimensoes'),
        'parametros': res.get('params_usados'),
        'calibracao': cal,
        'janela_hrv': {
            'desde': desde_hrv, 'anos': ANOS_HRV,
            'primeiro_dia_hrv': hrv_ini, 'ultimo_dia_hrv': hrv_fim,
            'dias_hrv': hrv_dias,
            'motivo': ('as analises com HRV usam so os ultimos '
                       f'{ANOS_HRV:g} anos — dados mais antigos podem vir de '
                       'outro dispositivo ou protocolo. Carga, CP e curvas de '
                       'potencia continuam a usar o historico completo.'),
            'configuravel': 'ANOS_HRV no Railway, ou ?desde=YYYY-MM-DD'},
        'ancora_testes': ancora,
        'segmentado': segmentado,
        'teste_eventos': eventos,
        'onde_sao_usados': {
            'tau_carga': 'canal 1 do mapa de atencao (decaimento da carga)',
            'lag_hrv': 'canal 2 (onde o HRV cai mais depois da carga)',
            'lag_super': 'canal 3 (janela de supercompensacao)',
            'largura_super': 'canal 3 (largura da janela)',
            'tau_risco': 'canal 4 (horizonte do sinal de risco)',
            'limiares_lambda1': 'classificacao focal vs multissistemico',
        },
    }
    _cache_cal.update({'chave': chave, 'valor': resposta, 'time': agora})
    return resposta


def api_sheets_debug():
    return jsonify(sheets.diagnostico())


EXPLICACOES = {
    'ctlg': ('O que e o CTL&gamma; e o FTLM fraccionario?', r"""
<p>O PMC classico trata a memoria do treino como um decaimento exponencial: cada
dia que passa, o peso de um treino cai numa fraccao fixa. Ao fim de tres meses,
um treino praticamente deixou de existir para o modelo.</p>

<p>O <b>FTLM fraccionario</b> (Della Mattia, 2025) substitui isso por um kernel
de Riemann-Liouville, em que o peso cai segundo uma <b>lei de potencia</b>:</p>

<div class="form">CTL&gamma;(t) = &Sigma;<sub>k</sub> Load(t&minus;k) &middot; k<sup>&gamma;&minus;1</sup> / &Gamma;(&gamma;)</div>

<p>A diferenca pratica: os treinos antigos nunca desaparecem, so pesam cada vez
menos. Isso aproxima-se mais do que se observa em atletas com anos de base — a
adaptacao estrutural persiste muito para la das seis semanas do CTL.</p>

<p>O <b>&gamma;</b> controla o comprimento dessa memoria:</p>
<ul>
<li><code>&gamma; proximo de 0.1</code> — memoria curta, a serie converge depressa</li>
<li><code>&gamma; proximo de 0.9</code> — memoria muito longa, a serie cresce quase sem limite</li>
</ul>

<p>Nao escolhemos o &gamma;: procuramos, entre 0.10 e 0.90, aquele que maximiza o
R&sup2; entre o CTL&gamma; e um indicador real teu. Sao dois &gamma; independentes:</p>
<ul>
<li><b>&gamma;<sub>perf</sub></b> — ajustado contra a tua CP por sessao, no proprio dia</li>
<li><b>&gamma;<sub>rec</sub></b> — ajustado contra a tendencia do teu LnRMSSD, com um dia
de desfasamento (a carga de ontem explica o HRV de hoje)</li>
</ul>
<p>Os lags sao fixos, nao estimados: &gamma;<sub>perf</sub> usa a carga do proprio dia
(lag 0) contra a CP suavizada por media movel causal de 3 dias; &gamma;<sub>rec</sub> usa
a carga de ontem (lag 1) contra a tendencia do LnRMSSD.</p>
<p>Se o ajuste nao passa nos criterios (R&sup2; minimo, &gamma; no extremo da grelha, ou
uma permutacao que nao supera o acaso), o &gamma; usado e o padr&atilde;o <b>0.35</b> e
a interface indica isso, com o motivo. O R&sup2; mostrado e sempre o do &gamma;
efetivamente aplicado, nunca o do candidato rejeitado.</p>

<p class="nota">Porque e que as duas curvas tem escalas tao diferentes: com
&gamma;=0.9 o expoente e &minus;0.1 e a soma quase nao decai, chegando aos milhares;
com &gamma;=0.1 o expoente e &minus;0.9 e converge para umas dezenas. Por isso o eixo
direito esta em indice 0-100 por defeito — compara as formas, nao as grandezas.</p>
"""),
    'fases': ('Como sao detectadas as fases de treino?', r"""
<p>Cada dia e classificado cruzando tres sinais: o declive do CTL&gamma; a 14 dias
(<code>&Delta;CTL&gamma;</code>), o HRV relativo em desvios-padrao, e o WEED — a media
dos z-scores de stress, dores e cansaco.</p>

<p>Os limiares nao sao numeros fixos: sao <b>percentis moveis de 60 dias</b> dos
teus proprios dados. O que conta como "carga a subir muito" e relativo ao teu
historico recente, nao a uma tabela generica.</p>

<ul>
<li><b>Overreach</b> — HRV abaixo do p10, WEED acima do p90, carga acima da mediana</li>
<li><b>Fatigue</b> — carga a subir e HRV abaixo do p20</li>
<li><b>Build</b> — carga a subir forte (p70+) com HRV ainda aceitavel (p30+)</li>
<li><b>Peak</b> — carga estavel e HRV acima do p60</li>
<li><b>Recovery</b> — carga a cair e HRV a recuperar</li>
</ul>

<p>A ordem importa: Overreach e testado antes de Fatigue, e Fatigue antes de
Build. Sem isso, um dia de sobrecarga real seria classificado como Build so por
a carga estar a subir.</p>

<p>Ha ainda uma salvaguarda: se estiveres ha mais de 10 dias sem treinar, as
fases que implicam carga activa deixam de fazer sentido — o declive pode estar
positivo so por inercia da media exponencial.</p>

<p>A <b>fase global ponderada</b> e a moda das fases de cada modalidade, pesada
pelo CTL&gamma; de cada uma: se o Bike domina a tua carga, e o estado do Bike que
manda no estado global.</p>
"""),
    'fmt': ("O que e o tensor FMT e o mapa de atencao?", r"""
<p>Todas as metricas anteriores olham para uma dimensao de cada vez. O FMT
(Della Mattia, 2019) olha para a <b>estrutura de covariacao entre todas</b>.</p>

<p>Cada dia tem um vector de estado com cinco dimensoes — carga, HRV, W&prime;,
sono e WEED. O tensor e o momento de segunda ordem das variacoes diarias
numa janela de 28 dias:</p>

<div class="form">F(d) = (1/L) &middot; &Sigma; &Delta;x(t) &otimes; &Delta;x(t)<sup>T</sup></div>

<p>O resultado e uma matriz simetrica 5&times;5. A <b>diagonal</b> tem a
variancia de cada dimensao; fora da diagonal estao as covariacoes — se a carga
e o HRV se movem juntos, essa celula acende.</p>

<ul>
<li><b>&kappa; = tr(F)</b> — a soma da diagonal. E o equivalente enriquecido do
TSS: alto quando varias dimensoes mudam de forma abrupta e simultanea.</li>
<li><b>Valores proprios</b> — dizem <i>onde</i> esta o stress. Se &lambda;&#8321;
domina, o stress e <b>focal</b>: quase toda a variabilidade vem de uma direccao.
Se estao equilibrados, e <b>multissistemico</b>.</li>
</ul>

<p>O argumento do paper para isto: o TSS e um mapa escalar, e qualquer mapa
escalar e muitos-para-um sobre o espaco de trajectorias fisiologicas. Duas
sessoes com o mesmo NP — e portanto o mesmo TSS — deixam o atleta em estados
mensuravelmente diferentes no dia seguinte. O escalar descarta exactamente a
informacao que interessa.</p>

<p>O caso operacional mais util e a <b>fadiga silenciosa</b>: TSB positivo (o
modelo classico diz "pronto") mas &kappa; a subir na dimensao autonomica. Essa
configuracao precede episodios de queda de rendimento que o CTL/ATL nao sinaliza.</p>

<h3 style="color:#E67E22">Sobre o mapa de atencao — leia isto</h3>

<p>No paper, os quatro canais <b>emergem</b> de um Transformer treinado numa
coorte de 30 atletas &times; 365 dias. Nao temos esse modelo treinado.</p>

<p>O que esta aqui sao <b>kernels explicitos</b> que reproduzem o comportamento
descrito para cada canal: decaimento exponencial para a acumulacao de carga,
janela em d-14 a d-21 para a supercompensacao, e por ai fora. Sao uteis para
ler a janela de 28 dias, mas <b>nao sao pesos aprendidos</b> — nao ha aqui nada
que tenha descoberto padroes sozinho.</p>

<p>A excepcao e o canal <b>Similaridade entre tensores</b>, que e atencao no
sentido literal da equacao (4) do paper: <code>softmax(QK&#7488;/&radic;d)</code>
com Q e K a serem os proprios vec(F), sem projeccoes aprendidas. Diz quais dos
28 dias tem uma estrutura de covariacao parecida com a de hoje.</p>

<p class="nota">Treinar o Transformer a serio exigiria uma coorte com alvos
rotulados (falha de execucao no dia seguinte, &Delta;CP a 28 dias). Com os dados
de um atleta so, o modelo sobreajustaria — daria previsoes confiantes e erradas.</p>
"""),
    'homeo': ('O que e o modelo homeostatico?', r"""
<p>O PMC classico assume que o teu fitness responde a 42 dias e a fadiga a 7,
para toda a gente. O modelo homeostatico pergunta: <b>e para ti?</b></p>

<div class="form">p&#770;(t) = p&#8320; + K&#8321;&middot;EWM(carga, T&#8321;) &minus; K&#8322;&middot;EWM(carga, T&#8322;)</div>

<p>E o modelo de Banister: a performance e o que o fitness acrescenta menos o
que a fadiga tira. Os quatro parametros sao estimados dos teus dados:</p>
<ul>
<li><b>K&#8321;</b> — quanto ganhas de fitness por unidade de carga</li>
<li><b>K&#8322;</b> — quanto pagas de fadiga por unidade de carga</li>
<li><b>T&#8321;</b> — em quantos dias a adaptacao e absorvida</li>
<li><b>T&#8322;</b> — em quantos dias a fadiga se dissipa</li>
</ul>

<p>Procuramos a combinacao de T&#8321; e T&#8322; que melhor explica a tua serie de CP,
e para cada uma resolvemos K&#8321; e K&#8322; por minimos quadrados.</p>

<p><b>Quando aparece "defeito 42/7":</b> ou tens menos de 20 pontos de CP nessa
modalidade, ou nenhuma combinacao deu K&#8321; e K&#8322; ambos positivos. K&#8322; negativo
significaria que treinar nao custa fadiga nenhuma — recusamos esse ajuste em
vez de mostrar numeros sem sentido fisico.</p>

<p class="nota">Repara sempre no R&sup2;. Um ajuste com R&sup2; de 0.02 explica 2% da
variacao da tua CP: os parametros existem, mas nao sustentam decisoes. R&sup2;
baixo e comum quando a CP por sessao depende mais do tipo de treino do dia do
que do estado de forma.</p>
"""),
    'alos': ('O que e o indice alostatico?', r"""
<p>Homeostasia e manter o equilibrio. <b>Alostasia</b> e mudar o ponto de
equilibrio para responder a uma exigencia — o que o treino faz. <b>Allostatic
overload</b> e quando essa mudanca deixa de ser compensada.</p>

<p>O indice compara dois periodos em seis dimensoes e resume num numero entre
&minus;1 e +1:</p>

<div class="form">score = sinal &middot; clip(&Delta;% / 50, &minus;1, +1)</div>

<ul>
<li><b>Reserva pico</b>, <b>CTL fitness</b>, <b>Recovery TSB</b>, <b>HRV
matinal</b>, <b>Sono</b> — subir e bom</li>
<li><b>HR repouso</b> — subir e mau, por isso o sinal inverte-se</li>
</ul>

<p>Uma variacao de 50% satura o score dessa dimensao. O total e a media das
dimensoes com dados.</p>

<ul>
<li><code>acima de +0.20</code> — boa adaptacao</li>
<li><code>entre &minus;0.10 e +0.20</code> — estavel</li>
<li><code>abaixo de &minus;0.10</code> — sobrecarga</li>
</ul>

<p class="nota">Os periodos por defeito sao os ultimos 60 dias contra os 60
anteriores. Faz sentido alinha-los com os teus blocos de treino reais.</p>
"""),
}


BODY = r"""
<h1>PMC — Performance Management Chart</h1>
<div class="sub" id="sub">A carregar...</div>
<div id="sugestoesTeste"></div>
<div id="irPrincipal" style="margin:14px 0;border:1px solid #30363d;border-radius:8px;padding:12px 14px">
<h3 style="font-size:15px;margin:0 0 4px">Curvas de influencia por modalidade (exploratorias, nao validadas)</h3>
<div id="irPrincipalAviso" style="font-size:12.5px;color:#d29922;margin-bottom:6px">A carregar...</div>
<div class="chartbox"><canvas id="chIrxMain" height="300"></canvas></div>
<div id="irxLegMain" style="font-size:12px;margin-top:4px"></div>
<div id="irPrincipalEstados" style="font-size:12.5px;margin-top:8px"></div>
<div id="irPrincipalGlobal" style="font-size:12.5px;margin-top:8px;color:#c9d1d9"></div>
</div>
<details class="expl" id="registoTestes" style="margin:10px 0" ontoggle="abrirRegisto(this)"><summary>Deteccao automatica de observacoes MMP (season, all-time, recente)</summary><div class="expl-corpo" id="registoTestesCorpo">A carregar...</div></details>
<details class="expl" id="estimadorExp" style="margin:10px 0" ontoggle="abrirEstimador(this)"><summary>Estimador experimental de potencia por modalidade (diagnostico)</summary><div class="expl-corpo" id="estimadorExpCorpo">A carregar...</div></details>
<details class="expl" id="validacaoExp" style="margin:10px 0" ontoggle="abrirValidacao(this)"><summary>LEGADO &mdash; Validacao preditiva exploratoria do estimador de 60/300/1200 s (nao valida o IR de MMP3)</summary><div class="expl-corpo" id="validacaoExpCorpo">A carregar...</div></details>
<details class="expl" id="irTecnico" style="margin:10px 0"><summary>Diagnosticos tecnicos do IR (secao recolhida; nao e o grafico principal)</summary><div class="expl-corpo">
<details class="expl" id="irExp" style="margin:10px 0" ontoggle="abrirIR(this)"><summary>Modelo IR: curvas de influencia por modalidade (experimental, ajuste historico)</summary><div class="expl-corpo" id="irExpCorpo">A carregar...</div></details>
<details class="expl" id="irExploratorio" style="margin:10px 0" ontoggle="abrirIRExploratorio(this)"><summary>IR EXPLORATORIO &mdash; diagnostico tecnico, nao validado (P0 livre; comparacao por duracao de 60 a 1800 s)</summary><div class="expl-corpo" id="irExploratorioCorpo">A carregar...</div></details>
<details class="expl" id="irSombra" style="margin:10px 0" ontoggle="abrirIRSombra(this)"><summary>IR SOMBRA &mdash; previsao experimental vs observado (validacao temporal; experimental; nao promove)</summary><div class="expl-corpo" id="irSombraCorpo">Abra para escolher a seleccao e carregar.</div></details>
</div></details>
<details class="expl" id="distCargaExp" style="margin:10px 0" ontoggle="abrirDistCarga(this)"><summary>Distribuicao da carga por modalidade (Bike, Row, Run, Ski): Training Load ou TRIMP, observada, sem musculacao</summary><div class="expl-corpo" id="distCargaExpCorpo">A carregar...</div></details>

<div class="cards" id="kpis"></div>
<div id="faseCard"></div>
<div id="alertas"></div>

<h2>PMC — CTL / ATL / TSB / FTLM</h2>
__EXPL_ctlg__
<div class="sub" id="subPMC">CTL 42d e ATL 7d no eixo esquerdo &middot; CTL&gamma; no eixo direito
  &middot; carga diaria empilhada por modalidade em baixo</div>
<div class="controls">
  <label class="sel">Janela
    <select id="janelaPMC">
      <option value="90" selected>90 dias</option>
      <option value="180">6 meses</option>
      <option value="365">1 ano</option>
      <option value="0">Tudo</option>
    </select></label>
  <label class="sel"><input type="checkbox" id="verFTLM" checked> Mostrar CTL&gamma;</label>
  <label class="sel"><input type="checkbox" id="verFases" checked> Bandas de fase</label>
  <label class="sel">CTL&gamma; em
    <select id="escCTLgPMC">
      <option value="indice" selected>Indice 0-100</option>
      <option value="real">Valores reais</option>
    </select></label>
</div>
<div class="chartbox">
  <div class="legend" id="lgPMC"></div>
  <canvas id="chPMC" height="330"></canvas>
  <canvas id="chLoad" height="120"></canvas>
</div>

<h2>CTL&gamma; por modalidade</h2>
__EXPL_fases__
<div class="sub">Um painel por desporto. Linha cheia = CTL&gamma; com o &gamma; ajustado
  a essa modalidade; ponteado = CTL e ATL classicos, como referencia.<br>
  <b>O CTL&gamma; bruto nao e comparavel entre modalidades:</b> o kernel e
  k<sup>&gamma;&minus;1</sup>, por isso &gamma;=0.9 da dezenas de milhar e
  &gamma;=0.1 da dezenas. Compara a coluna em percentagem.</div>
<div id="painelMods" class="grid2"></div>
<div class="wrap" style="max-height:280px;margin-bottom:14px"><table>
  <thead><tr id="gHead"></tr></thead><tbody id="gBody"></tbody></table></div>

<h2>FMT — tensor 5&times;5 e mapa de atencao</h2>
<div class="sub" id="subFMT5"></div>
<div style="display:grid;grid-template-columns:1fr 1.4fr 1fr;gap:14px;">
  <div class="chartbox">
    <div class="legend"><span>Regime do dia (&kappa; vs TSB)</span></div>
    <div id="regimeHojeCard"></div>
  </div>
  <div class="chartbox">
    <div class="legend"><span>Matriz de covariacoes F(d)</span></div>
    <canvas id="chMatriz" height="260"></canvas>
  </div>
  <div class="chartbox">
    <div class="legend"><span>Valores proprios</span></div>
    <canvas id="chEigen" height="260"></canvas>
  </div>
</div>
<div id="leituraFMT"></div>
<div class="controls">
  <label class="sel">Canal <select id="canalFMT"></select></label>
</div>
<div class="chartbox">
  <div class="legend" id="lgAtencao"></div>
  <canvas id="chAtencao" height="200"></canvas>
</div>
<div class="sub" id="notaAtencao" style="font-style:italic"></div>
<div id="explicacaoCanalFMT" style="margin-top:6px;"></div>
<div id="poderBox"></div>
<h3>Calibracao dos parametros</h3>
<div id="veredicto"></div>
<div class="sub" id="notaCal"></div>
<div class="wrap" style="max-height:260px;margin-bottom:14px"><table>
  <thead><tr id="calHead"></tr></thead><tbody id="calBody"></tbody></table></div>

<h2>Curvatura &kappa; ao longo do tempo</h2>
<details style="margin-bottom:10px;">
  <summary class="sub" style="cursor:pointer;">Sobre a metodologia dos canais (paper vs dados) — abrir para ler</summary>
__EXPL_fmt__
</details>
<div class="sub" id="subFMT"></div>
<div class="chartbox">
  <div class="legend" id="lgFMT"></div>
  <canvas id="chFMT" height="220"></canvas>
</div>



<h2>Evolução da performance</h2>
<div class="sub">
  Tendência de FTP/eFTP e melhores esforços por modalidade: mediana da janela
  recente contra a janela anterior de mesmo tamanho. Zero e ausência nunca entram
  como valor. Valores repetidos e extremos são sinalizados, não removidos.
  O modelo fisiológico (homeostático) não está validado e não aparece aqui.
</div>
<div class="controls">
  <label class="sel">Modalidade
    <select id="evolMod">
      <option value="Bike" selected>Bike</option>
      <option value="Row">Row</option>
      <option value="Run">Run</option>
      <option value="Ski">Ski</option>
    </select></label>
  <label class="sel">Janela
    <select id="evolJanela">
      <option value="90">90 dias</option>
      <option value="180" selected>180 dias</option>
      <option value="365">365 dias</option>
    </select></label>
  <label class="sel">Fonte
    <select id="evolFonte">
      <option value="todas" selected>Todas</option>
      <option value="eftp_atividade">eFTP da atividade</option>
      <option value="ftp_configurado">FTP configurado</option>
      <option value="eftp_nao_confirmado">EFTP (não confirmado)</option>
    </select></label>
</div>
<div class="sub" id="evolSub"></div>
<div class="sub" id="evolExplica"><b>Como ler:</b> cada ponto representa um registro, não uma conclusão sobre a sua performance.
<ul style="margin:6px 0 0 18px;padding:0">
<li><b>eFTP da atividade:</b> estimativa do Intervals.icu para a sessão completa, incluindo recuperações. Depende da estrutura do treino; um valor baixo isolado não significa perda de capacidade e não equivale ao FTP configurado.</li>
<li><b>FTP configurado:</b> valor definido nas configurações do Intervals.icu. É uma referência, não uma medição da capacidade atual.</li>
<li><b>EFTP:</b> campo de significado não confirmado. É mostrado separadamente, sem interpretação.</li>
<li><b>Melhores esforços:</b> potência observada exatamente em 1, 5, 12 e 20 minutos, lida diretamente da curva, sem interpolação.</li>
</ul>
A comparação usa a mediana de duas janelas do mesmo tamanho: a recente e a anterior. Só é apresentada quando há observações e cobertura temporal suficientes. Valores repetidos e extremos suspeitos são sinalizados, não removidos automaticamente. As séries podem ter quantidades diferentes de registros.
</div>
<div class="wrap" style="margin-bottom:10px"><table>
  <thead><tr id="evolHead"></tr></thead><tbody id="evolBody"></tbody></table></div>
<div class="wrap" style="margin-bottom:10px"><table>
  <thead><tr><th>Série</th><th>Valor de referência</th><th>Data</th><th>Nota</th></tr></thead>
  <tbody id="evolCfgBody"></tbody></table></div>
<div class="sub">Melhores esforços observados diretamente na curva de potência
  (sem interpolação). Mesmas janelas e critérios de suficiência do FTP.</div>
<div class="wrap" style="margin-bottom:14px"><table>
  <thead><tr id="evolMeHead"></tr></thead><tbody id="evolMeBody"></tbody></table></div>
<div id="evolGraficos"></div>
<details class="sub" id="evolDetDados" style="margin-bottom:14px"><summary><b>Detalhes dos dados e qualidade</b></summary>
<div class="sub" id="evolPopulacao"></div>
<div id="evolAvisos"></div>
<details class="sub" id="evAudit"><summary id="evAuditResumo">Detalhes por observação (auditoria)</summary><div id="evAuditBody"></div></details>
<details class="sub" id="evEftpTab"><summary id="evEftpTabResumo">Pontos do eFTP da atividade (tabela)</summary><div id="evEftpTabBody"></div></details>
</details>

<div id="homeoLegado" hidden>
<h2>Modelo homeostatico — reserva de performance</h2>
__EXPL_homeo__
<div class="sub" id="subHomeo"></div>
<div class="cards" id="homeoKpis"></div>
<div class="controls">
  <label class="sel">Modalidade
    <select id="homeoMod"><option value="">Global</option></select></label>
  <label class="sel">Vista
    <select id="homeoVista">
      <option value="todo" selected>Tudo</option>
      <option value="banda">Fit + banda</option>
      <option value="fits">So os fits</option>
    </select></label>
  <label class="sel">Suavização <select id="homeoSuav"><option value="retro" selected>Retrospectiva (padrão)</option><option value="causal">Causal</option></select></label>
  <label class="sel"><input type="checkbox" id="homeoMods" checked> Sobrepor modalidades</label>
</div>
<div class="controls" style="font-size:12px">
  <label class="sel">Periodo anterior
    <input type="date" id="haIni" style="min-width:auto">
    <input type="date" id="haFim" style="min-width:auto"></label>
  <label class="sel">Periodo recente
    <input type="date" id="hrIni" style="min-width:auto">
    <input type="date" id="hrFim" style="min-width:auto"></label>
</div>
<div id="homeoAnalise"></div>
<div class="chartbox">
  <div class="legend" id="lgHomeo"></div>
  <canvas id="chHomeo" height="280"></canvas>
</div>
<div class="wrap" style="max-height:260px;margin-bottom:14px"><table>
  <thead><tr id="hmHead"></tr></thead><tbody id="hmBody"></tbody></table></div>
</div>

<h2>Indice alostatico</h2>
__EXPL_alos__
<div class="sub" id="subAlos"></div>
<div id="alosCard"></div>
<div class="wrap" style="max-height:320px;margin-bottom:14px"><table>
  <thead><tr id="alosHead"></tr></thead><tbody id="alosBody"></tbody></table></div>

<h2>CTL vs KJ — quanto custa cada kJ</h2>
<div class="sub">
  Adaptado do dashboard Streamlit (susigan/dashboard, tab_ctl_kj.py).
  TRIMP aqui é sessão-RPE (duração×RPE), ajustado por IF só quando há
  watts e CP — este projecto não lê zonas de FC.
</div>
<div style="display:flex;gap:8px;margin-bottom:12px">
  <button id="ctlkjBtnCoef" onclick="ctlkjMostrarSubtab('coef')"
    style="background:#1c2331;border:1px solid #5DADE2;color:#5DADE2;
    padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">
    Coeficientes</button>
  <button id="ctlkjBtnEf" onclick="ctlkjMostrarSubtab('ef')"
    style="background:#161b22;border:1px solid #30363d;color:#8b949e;
    padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">
    Eficiência</button>
</div>

<div id="ctlkjCoefBox">
  <div class="sub">
    dTRIMP/dkJ por modalidade e tipo de sessão (OLS: TRIMP ~
    kJ_trabalho + densidade). Quanto mais baixo, mais eficiente —
    menos custo interno por kJ de trabalho externo.
  </div>
  <div class="wrap" style="max-height:340px;margin-bottom:14px"><table>
    <thead><tr id="ctlkjCoefHead"></tr></thead>
    <tbody id="ctlkjCoefBody"></tbody></table></div>
</div>

<div id="ctlkjEfBox" style="display:none">
  <div class="sub">
    eff = TRIMP/kJ_trabalho, mediana semanal, rolling de 4 semanas. A
    subir = o mesmo kJ está a custar mais (fadiga acumulada). A descer
    = o mesmo kJ está a custar menos (adaptação).
  </div>
  <div id="ctlkjEfCards"></div>
</div>

<h2>Exportar dados</h2>
<div class="sub">Os mesmos dados que o dashboard usa, para analisares por fora.
  O CSV abre directamente no pandas ou no Excel.</div>
<div class="wrap" style="margin-bottom:14px"><table>
  <thead><tr><th>Ficheiro</th><th>O que tem</th><th class="num">Formato</th></tr></thead>
  <tbody>
  <tr><td>Atividades</td><td>uma linha por sessao: carga, kJ, zonas, HR, potencia</td>
    <td class="num"><a href="/api/export/atividades.csv">CSV</a></td></tr>
  <tr><td>Wellness</td><td>HRV, HR repouso, sono, stress, cansaco, dores</td>
    <td class="num"><a href="/api/export/wellness.csv">CSV</a></td></tr>
  <tr><td>Corporal</td><td>peso, gordura, calorias, macros</td>
    <td class="num"><a href="/api/export/corporal.csv">CSV</a></td></tr>
  <tr><td>Curvas de potencia</td><td>uma linha por sessao e duracao</td>
    <td class="num"><a href="/api/export/curvas.csv">CSV</a> &middot;
      <a href="/api/export/curvas.json">JSON</a></td></tr>
  <tr><td>Testes maximos</td><td>esforcos detectados — a ancora de performance</td>
    <td class="num"><a href="/api/export/testes.csv">CSV</a></td></tr>
  <tr><td>CP ajustado</td><td>CP e W&prime; por sessao, de P(t)=W&prime;/t+CP</td>
    <td class="num"><a href="/api/export/cp.csv">CSV</a></td></tr>
  <tr><td>CP de fallback</td><td>CP bruto das sessoes sem curva aceita: icu_pm_cp e, na falta dele, icu_pm_ftp. icu_rolling_ftp, EFTP e icu_ftp nao entram (ver auditoria do CP)</td>
    <td class="num"><a href="/api/export/cp_fallback.csv">CSV</a></td></tr>
  <tr><td>Serie diaria</td><td>CTL, ATL, TSB dia a dia</td>
    <td class="num"><a href="/api/export/serie_diaria.csv">CSV</a></td></tr>
  <tr><td><b>Tudo</b></td><td>todos os anteriores num so ficheiro</td>
    <td class="num"><a href="/api/export/tudo.json">JSON</a></td></tr>
  </tbody></table></div>

<h2>Descarregar os dados</h2>
<div class="sub">Dados em bruto, sem as transformacoes deste codigo — para
  correres as tuas proprias analises noutro sitio.</div>
<div id="exportBox"></div>

<div class="sub" style="margin-top:20px">
  <a href="/api/pmc" target="_blank">JSON do PMC</a> &middot;
  <a href="/api/export" target="_blank">Indice das exportacoes</a> &middot;
  <a href="/api/calibracao" target="_blank">Calibracao</a> &middot;
  <a href="/api/protocolo" target="_blank">Protocolo de testes</a> &middot;
  <a href="/api/debug/sheets" target="_blank">Diagnostico dos Sheets</a>
</div>
"""

JS = r"""
let D=null;
const COR={ctl:'#5DADE2',atl:'#E74C3C',tsb:'#2ECC71',load:'#30363d'};

function janelaPMC(arr){
 const n=parseInt(document.getElementById('janelaPMC').value,10);
 return (n>0&&arr.length>n)?arr.slice(-n):arr;
}

// linhas sobre um eixo comum, com barras de carga por tras
function drawLinhas(canvasId,legendId,dados,series,cores,labels,opcoes){
 opcoes=opcoes||{};
 const o=ctx(canvasId,opcoes.height||300); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const ativas=series.filter(s=>dados.some(d=>d[s]!=null));
 if(legendId)document.getElementById(legendId).innerHTML=ativas.map(s=>
  '<span class="tog'+(opcoes.off&&opcoes.off[s]?' off':'')+'" data-c="'+canvasId+
  '" data-k="'+s+'"><i style="background:'+(cores[s]||'#8b949e')+'"></i>'+
  ((labels&&labels[s])||s)+'</span>').join('');
 if(!dados.length){noData(g,W,H);return;}

 const vis=ativas.filter(s=>!(opcoes.off&&opcoes.off[s]));
 const PL=48,PR=48,PT=14,PB=28,w=W-PL-PR,h=H-PT-PB;
 const n=dados.length;
 const X=i=>PL+w*(n>1?i/(n-1):0.5);

 // bandas de fase ao fundo, para ler o contexto de cada periodo
 if(opcoes.fases&&D&&D.ftlm){
  const leg=D.ftlm.fases_legenda||{};
  let ini=0;
  for(let i=1;i<=dados.length;i++){
   const mudou=(i===dados.length)||(dados[i].fase!==dados[ini].fase);
   if(!mudou)continue;
   const f=leg[dados[ini].fase];
   if(f&&dados[ini].fase!=='TRANSITION'){
    g.fillStyle=hexRgba(f.cor,0.10);
    g.fillRect(X(ini),PT,Math.max(1,X(i-1)-X(ini)),h);}
   ini=i;}
 }
 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

 // barras de carga diaria, escala propria, ao fundo
 if(opcoes.barras&&dados.some(d=>d[opcoes.barras])){
  const bv=dados.map(d=>d[opcoes.barras]||0);
  const bmx=Math.max.apply(null,bv)||1;
  const bw=Math.max(1,w/n*0.7);
  g.fillStyle='rgba(88,101,116,0.45)';
  dados.forEach(function(d,i){
   const v=d[opcoes.barras]||0; if(!v)return;
   const bh=h*0.32*v/bmx;
   g.fillRect(X(i)-bw/2,PT+h-bh,bw,bh);});
 }

 if(!vis.length){noData(g,W,H,'Todas as series desligadas');return;}

 // Modo de escala. Series com gamma diferente diferem em ordens de grandeza
 // (gamma=0.9 chega a 13000, gamma=0.1 a 55), por isso partilhar eixo esconde
 // a de menor amplitude.
 //   'partilhada' — um so eixo (CTL/ATL/TSB, mesma unidade)
 //   'propria'    — cada serie no seu eixo, rotulos para as duas primeiras
 //   'indice'     — tudo em 0-100 face ao proprio maximo (compara formas)
 const modo=opcoes.escala||'partilhada';
 const lim={};
 vis.forEach(function(s){
  let a=Infinity,b=-Infinity;
  dados.forEach(function(d){const v=d[s];if(v==null)return;
   if(v<a)a=v; if(v>b)b=v;});
  if(!isFinite(a)){a=0;b=1;}
  if(a>0&&modo!=='indice')a=0;
  if(b===a)b=a+1;
  lim[s]=[a,b];});

 let mn=Infinity,mx=-Infinity;
 if(modo==='partilhada'){
  vis.forEach(function(s){mn=Math.min(mn,lim[s][0]);mx=Math.max(mx,lim[s][1]);});
  if(!isFinite(mn)){noData(g,W,H);return;}
  if(mn>0)mn=0; if(mx===mn)mx=mn+1;
 } else if(modo==='indice'){ mn=0; mx=100; }

 function Yde(s,v){
  if(modo==='partilhada')return PT+h-(v-mn)/(mx-mn)*h;
  if(modo==='indice'){const[a,b]=lim[s];return PT+h-((v-a)/(b-a)*100)/100*h;}
  const[a,b]=lim[s]; return PT+h-(v-a)/(b-a)*h;
 }

 if(modo==='partilhada'&&mn<0){        // linha do zero, para o TSB
  const y0=PT+h-(0-mn)/(mx-mn)*h;
  g.strokeStyle='#484f58';g.setLineDash([3,3]);g.beginPath();
  g.moveTo(PL,y0);g.lineTo(PL+w,y0);g.stroke();g.setLineDash([]);}

 vis.forEach(function(s){
  g.strokeStyle=cores[s]||'#8b949e';g.lineWidth=1.8;g.beginPath();
  let st=false;
  dados.forEach(function(d,i){
   const v=d[s]; if(v==null){st=false;return;}
   const x=X(i),y=Yde(s,v);
   if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);});
  g.stroke();});

 function fmtEixo(v){
  const a=Math.abs(v);
  if(a>=10000)return (v/1000).toFixed(0)+'k';
  if(a>=100)return Math.round(v);
  return v.toFixed(1);}

 g.font='10px sans-serif';
 if(modo==='partilhada'){
  g.fillStyle='#8b949e';g.textAlign='right';
  for(let i=0;i<=4;i++)g.fillText(fmtEixo(mx-(mx-mn)*i/4),PL-6,PT+h*i/4+3);
 } else if(modo==='indice'){
  g.fillStyle='#8b949e';g.textAlign='right';
  for(let i=0;i<=4;i++)g.fillText(Math.round(100-100*i/4)+'%',PL-6,PT+h*i/4+3);
 } else {
  // eixo esquerdo para a 1a serie, direito para a 2a
  vis.slice(0,2).forEach(function(s,idx){
   const[a,b]=lim[s]; const dir=idx===1;
   g.fillStyle=cores[s]||'#8b949e'; g.textAlign=dir?'left':'right';
   for(let i=0;i<=4;i++)
    g.fillText(fmtEixo(b-(b-a)*i/4),dir?PL+w+6:PL-6,PT+h*i/4+3);});
 }
 g.fillStyle='#8b949e';g.textAlign='center';
 const step=Math.ceil(n/8);
 dados.forEach(function(d,i){if(i%step!==0)return;
  g.fillText((d.date||'').slice(0,7),X(i),H-8);});
 g.textAlign='left';

 registarTip(canvasId,function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  if(x<PL||x>PL+w)return '';
  const i=Math.round((x-PL)/w*(n-1));
  if(i<0||i>=n)return '';
  const d=dados[i];
  let html='<div class="th">'+d.date+'</div>';
  vis.forEach(function(s){
   if(d[s]==null)return;
   html+=linhaTip(cores[s]||'#8b949e',(labels&&labels[s])||s,
    (Math.abs(d[s])>=100?Math.round(d[s]):d[s].toFixed(1)));});
  if(opcoes.barras&&d[opcoes.barras])
   html+=linhaTip('#586574','Carga',Math.round(d[opcoes.barras]));
  if(opcoes.fases&&d.fase&&D.ftlm){
   const f=(D.ftlm.fases_legenda||{})[d.fase];
   if(f)html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;'+
    'padding-top:4px"><span>Fase</span><b style="color:'+f.cor+'">'+f.label+'</b></div>';
   if(d.dctlg!=null)html+=linhaTip('#8b949e','ΔCTLγ',d.dctlg.toFixed(4)+'/d');}
  if(opcoes.estado&&d.tsb!=null){
   const e=estadoDe(d.tsb);
   html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;'+
    'padding-top:4px"><span>Forma</span><b style="color:'+e.cor+'">'+e.label+'</b></div>';}
  return html;});

 // clicar na legenda liga/desliga
 if(legendId)document.querySelectorAll('#'+legendId+' span.tog').forEach(function(sp){
  sp.onclick=function(){
   opcoes.off[sp.dataset.k]=!opcoes.off[sp.dataset.k];
   if(opcoes.redraw)opcoes.redraw();};});
}

function estadoDe(tsb){
 if(tsb>25)return{label:'Muito fresco',cor:'#5DADE2'};
 if(tsb>5)return{label:'Fresco',cor:'#2ECC71'};
 if(tsb>-10)return{label:'Neutro',cor:'#F4D03F'};
 if(tsb>-30)return{label:'Em carga',cor:'#E67E22'};
 return{label:'Muito carregado',cor:'#E74C3C'};
}

let OFFP={},OFFM={},OFFF={},OFFG={},OFFK={},OFFH={};

// Reserva de performance: dois periodos lado a lado, cada um com o seu
// ajuste Savitzky-Golay e banda +/-1 SD, com o pico marcado. As modalidades
// sobrepoem-se ao global para se ver qual esta a puxar a reserva.
const CORANT='#27ae60', CORREC='#e67e22';

function periodosHomeo(serie){
 const d=serie.map(x=>x.date);
 function val(id){const v=document.getElementById(id).value;return v||null;}
 let aI=val('haIni'),aF=val('haFim'),rI=val('hrIni'),rF=val('hrFim');
 if(!aI||!aF||!rI||!rF){
  // por defeito: os ultimos 60 dias contra os 60 anteriores
  const n=d.length;
  rF=d[n-1]; rI=d[Math.max(0,n-60)];
  aF=d[Math.max(0,n-61)]; aI=d[Math.max(0,n-120)];
  document.getElementById('haIni').value=aI;
  document.getElementById('haFim').value=aF;
  document.getElementById('hrIni').value=rI;
  document.getElementById('hrFim').value=rF;}
 return {ant:[aI,aF],rec:[rI,rF]};
}

// so um ajuste valido (estado_homeostatico no servidor) pode ser mostrado como tal
function homeoValido(x){return !!(x&&x.validacao&&x.validacao.valido);}
// rotulo descritivo do R2 (nao decide validade): so aparece num ajuste valido
function homeoCapacidade(x){return (homeoValido(x)&&x.validacao&&x.validacao.capacidade)||'';}
// Campos ja existentes no payload. So escolhe qual versao ja calculada se desenha.
function homeoCampos(){
 const causal=((document.getElementById('homeoSuav')||{}).value==='causal');
 return causal
  ?{suave:'p_hat_suave_causal',sup:'banda_sup_causal',inf:'banda_inf_causal',nome:'causal, usa apenas os dias até cada dia'}
  :{suave:'p_hat_suave',sup:'banda_sup',inf:'banda_inf',nome:'retrospectiva, usa também dias posteriores'};}
function drawHomeo(){
 const H=D.homeostatico;
 if(!H){const o=ctx('chHomeo',300);if(o)noData(o.g,o.W,o.H);return;}
 const vista=document.getElementById('homeoVista').value;
 const modSel=document.getElementById('homeoMod').value;
 const verMods=document.getElementById('homeoMods').checked;
 const HM=D.homeostatico_mod||{};
 const principal=(modSel&&HM[modSel])?HM[modSel]:H;
 if(!homeoValido(principal)){
  const o=ctx('chHomeo',300);
  if(o)noData(o.g,o.W,o.H,'Sem ajuste valido: '+((principal.validacao&&principal.validacao.motivo)||'sem dados'));
  document.getElementById('lgHomeo').innerHTML='';
  document.getElementById('homeoAnalise').innerHTML='';
  return;}
 const P=periodosHomeo(principal.serie);
 const K=homeoCampos();

 const dentro=(d,r)=>d>=r[0]&&d<=r[1];
 const ant=principal.serie.filter(x=>dentro(x.date,P.ant));
 const rec=principal.serie.filter(x=>dentro(x.date,P.rec));
 if(!ant.length&&!rec.length){
  const o=ctx('chHomeo',300);if(o)noData(o.g,o.W,o.H,'Periodos sem dados');return;}

 const o=ctx('chHomeo',300); if(!o)return;
 const g=o.g,W=o.W,H2=o.H;
 const PL=52,PR=16,PT=16,PB=26,w=W-PL-PR,h=H2-PT-PB;
 const total=ant.length+rec.length;
 const gap=Math.round(w*0.03);
 const wA=ant.length?(w-gap)*ant.length/total:0;
 const wR=rec.length?(w-gap)*rec.length/total:0;
 const XA=i=>PL+wA*(ant.length>1?i/(ant.length-1):0.5);
 const XR=i=>PL+wA+gap+wR*(rec.length>1?i/(rec.length-1):0.5);

 // modalidades a sobrepor: sempre que a caixa esteja ligada
 const mods=(verMods&&vista==='todo')?Object.keys(HM).filter(m=>homeoValido(HM[m])):[];
 const idxMod={};
 mods.forEach(function(m){
  const ix={};(HM[m].serie||[]).forEach(r=>{ix[r.date]=r[K.suave];});
  idxMod[m]=ix;});

 let mn=Infinity,mx=-Infinity;
 [ant,rec].forEach(seg=>seg.forEach(function(d){
  [d[K.inf],d[K.sup],d[K.suave]].forEach(function(v){
   if(v==null)return;if(v<mn)mn=v;if(v>mx)mx=v;});}));
 mods.forEach(m=>[ant,rec].forEach(seg=>seg.forEach(function(d){
  const v=idxMod[m][d.date];if(v==null)return;
  if(v<mn)mn=v;if(v>mx)mx=v;})));
 if(!isFinite(mn)){noData(g,W,H2);return;}
 if(mx===mn)mx=mn+1;
 const marg=(mx-mn)*0.08; mn-=marg; mx+=marg;
 const Y=v=>PT+h-(v-mn)/(mx-mn)*h;

 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

 function desenhar(seg,X,cor,fill){
  if(!seg.length)return null;
  if(vista!=='fits'){
   g.fillStyle=fill;g.beginPath();let ok=false;
   seg.forEach(function(d,i){const v=d[K.sup];if(v==null)return;
    if(!ok){g.moveTo(X(i),Y(v));ok=true;}else g.lineTo(X(i),Y(v));});
   for(let i=seg.length-1;i>=0;i--){const v=seg[i][K.inf];if(v==null)continue;
    g.lineTo(X(i),Y(v));}
   g.closePath();g.fill();}
  if(vista==='todo'&&!OFFH.bruto){
   g.strokeStyle=hexRgba(cor,0.30);g.lineWidth=1;g.beginPath();let st=false;
   seg.forEach(function(d,i){const v=d.p_hat;if(v==null){st=false;return;}
    if(!st){g.moveTo(X(i),Y(v));st=true;}else g.lineTo(X(i),Y(v));});
   g.stroke();}
  g.strokeStyle=cor;g.lineWidth=2.4;g.beginPath();let st2=false;
  let pico=null,pi=0;
  seg.forEach(function(d,i){const v=d[K.suave];if(v==null){st2=false;return;}
   if(pico===null||v>pico){pico=v;pi=i;}
   if(!st2){g.moveTo(X(i),Y(v));st2=true;}else g.lineTo(X(i),Y(v));});
  g.stroke();
  // marcador do pico
  if(pico!==null){
   g.fillStyle=cor;g.beginPath();g.arc(X(pi),Y(pico),4,0,Math.PI*2);g.fill();
   g.fillStyle=cor;g.font='10px sans-serif';g.textAlign='center';
   g.fillText('pico '+Math.round(pico),X(pi),Y(pico)-8);g.textAlign='left';}
  return pico===null?null:{v:pico,d:seg[pi].date};
 }

 // modalidades por tras
 mods.forEach(function(m){
  if(OFFH[m])return;
  const cor=(D.cores||{})[m]||'#8b949e';
  [[ant,XA,[3,3]],[rec,XR,[]]].forEach(function(par){
   g.strokeStyle=cor;g.lineWidth=1.4;g.globalAlpha=0.8;
   g.setLineDash(par[2]);g.beginPath();let st=false;
   par[0].forEach(function(d,i){const v=idxMod[m][d.date];
    if(v==null){st=false;return;}
    if(!st){g.moveTo(par[1](i),Y(v));st=true;}else g.lineTo(par[1](i),Y(v));});
   g.stroke();g.setLineDash([]);g.globalAlpha=1;});});

 const picoA=desenhar(ant,XA,CORANT,'rgba(39,174,96,0.13)');
 const picoR=desenhar(rec,XR,CORREC,'rgba(230,126,34,0.13)');

 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText(Math.round(mx-(mx-mn)*i/4),PL-6,PT+h*i/4+3);
 g.textAlign='center';
 [[ant,XA],[rec,XR]].forEach(function(par){
  const seg=par[0],X=par[1],step=Math.ceil(Math.max(1,seg.length/4));
  seg.forEach(function(d,i){if(i%step!==0)return;
   g.fillText(d.date.slice(5),X(i),H2-8);});});
 g.textAlign='left';

 const itens=[['__a','Anterior · '+P.ant[0]+' a '+P.ant[1]+' (n='+ant.length+')',CORANT],
              ['__r','Recente · '+P.rec[0]+' a '+P.rec[1]+' (n='+rec.length+')',CORREC]];
 if(vista==='todo')itens.push(['bruto','p̂ diário bruto (sem suavização)','rgba(150,150,150,0.4)']);
 mods.forEach(m=>itens.push([m,m,(D.cores||{})[m]||'#8b949e']));
 document.getElementById('lgHomeo').innerHTML=itens.map(x=>
  (x[0].indexOf('__')===0
   ? '<span><i style="background:'+x[2]+'"></i>'+x[1]+'</span>'
   : '<span class="tog'+(OFFH[x[0]]?' off':'')+'" data-k="'+x[0]+'">'+
     '<i style="background:'+x[2]+'"></i>'+x[1]+'</span>')).join('');
 document.querySelectorAll('#lgHomeo span.tog').forEach(function(sp){
  sp.onclick=function(){OFFH[sp.dataset.k]=!OFFH[sp.dataset.k];drawHomeo();};});

 // leitura neutra: descreve a curva estimada; nao conclui adaptacao, fadiga ou causa
 const el=document.getElementById('homeoAnalise');
 const dt=x=>String(x||'').split('-').reverse().join('/');
 let txt='<div style="background:#161b22;border-left:3px solid #30363d;padding:9px 13px;border-radius:0 6px 6px 0;font-size:13px;margin:10px 0">'+
  '<b>Como ler:</b> a linha é a <b>curva estimada pelo modelo</b> ('+K.nome+'), não observações diárias. '+
  'A faixa é ±1 desvio-padrão móvel de 14 dias da própria curva ajustada; não é intervalo de confiança nem previsão. '+
  'Os períodos anterior e recente são desenhados separadamente, sem ligação entre eles.';
 if(vista==='todo')txt+=' O ponto cinzento claro é o p̂ diário bruto, sem suavização.';
 if(mods.length)txt+=' Modalidades sobrepostas: tracejado no período anterior, contínuo no recente.';
 if(picoA!=null&&picoR!=null&&picoA.v!==0){
  const dif=picoR.v-picoA.v, pct=dif/picoA.v*100;
  txt+=' Pico da curva estimada: anterior '+Math.round(picoA.v)+' ('+dt(picoA.d)+'), recente '+Math.round(picoR.v)+' ('+dt(picoR.d)+'); diferença '+(dif>=0?'+':'')+Math.round(dif)+' ('+(pct>=0?'+':'')+pct.toFixed(0)+'%). Isto descreve a curva ajustada; não mostra adaptação, fadiga nem causa.';}
 el.innerHTML=txt+'</div>';

 [[ant,XA],[rec,XR]].forEach(function(par,k){
  registarTip('chHomeo',function(mxp,myp,rw){
   const esc=rw/W,x=mxp/esc;
   let seg,X,rot;
   if(x>=PL&&x<=PL+wA){seg=ant;X=XA;rot='Anterior';}
   else if(x>=PL+wA+gap&&x<=PL+w){seg=rec;X=XR;rot='Recente';}
   else return '';
   if(!seg.length)return '';
   let melhor=0,dist=1e9;
   seg.forEach(function(d,i){const dd=Math.abs(X(i)-x);if(dd<dist){dist=dd;melhor=i;}});
   const d=seg[melhor];
   let html='<div class="th">'+rot+' · '+d.date+'</div>'+
    linhaTip(rot==='Anterior'?CORANT:CORREC,'curva estimada',d[K.suave]);
   mods.forEach(function(m){
    if(OFFH[m])return;
    const v=idxMod[m][d.date];if(v==null)return;
    html+=linhaTip((D.cores||{})[m]||'#8b949e',m,v);});
   html+='<div class="tr"><span>Banda (±1 DP móvel)</span><b>'+d[K.inf]+' a '+d[K.sup]+'</b></div>';
   return html;});});
}

const MOTIVOS={
 ok:'ajustado',
 poucos_pontos_cp:'poucos pontos de CP',
 k_negativo:'K&#8322; sairia negativo',
 r2_nao_positivo:'R&sup2; nao positivo',
 sem_tentativas:'sem tentativas'};
function motivoHomeo(v){
 if(homeoValido(v))return 'ajustado (R² '+v.r2+')'+(homeoCapacidade(v)?' — capacidade explicativa '+homeoCapacidade(v):'');
 let t=(v.motivo&&v.motivo!=='ok'&&MOTIVOS[v.motivo])||'sem ajuste válido';
 if(v.motivo==='poucos_pontos_cp')t+=' ('+v.n_testes+'/20)';
 if(v.motivo==='k_negativo'&&v.melhor_rejeitado)
  t+=' — o melhor daria K&#8321;='+v.melhor_rejeitado.k1+
     ' K&#8322;='+v.melhor_rejeitado.k2+' (R&sup2; '+v.melhor_rejeitado.r2+')';
 return t;
}

function tabelaHomeo(){
 const HM=D.homeostatico_mod||{},H=D.homeostatico;
 document.getElementById('hmHead').innerHTML=
  ['Modalidade','K₁','K₂','T₁','T₂','R²','p̂ actual','Ajuste']
   .map((c,i)=>'<th class="'+(i&&i<7?'num':'')+'">'+c+'</th>').join('');
 function linha(nome,v,cor){
  const s=v.serie||[];
  const ok=homeoValido(v);
  const ult=(ok&&s.length)?s[s.length-1][homeoCampos().suave]:'—';
  const par=x=>ok?x:'—';
  return '<tr><td style="color:'+cor+'">'+nome+'</td>'+
   '<td class="num">'+par(v.k1)+'</td><td class="num">'+par(v.k2)+'</td>'+
   '<td class="num">'+(ok?v.t1+'d':'—')+'</td><td class="num">'+(ok?v.t2+'d':'—')+'</td>'+
   '<td class="num">'+v.r2+'</td><td class="num">'+ult+'</td>'+
   '<td style="font-size:12px;color:'+(ok?'#2ECC71':'#E67E22')+'" '+
   'title="'+(v.nota||'')+'">'+motivoHomeo(v)+'</td></tr>';}
 const l=[];
 if(H)l.push(linha('Global',H,'#e6e6e6'));
 Object.keys(HM).forEach(m=>l.push(linha(m,HM[m],(D.cores||{})[m]||'#e6e6e6')));
 document.getElementById('hmBody').innerHTML=l.join('');
}

function mostrarHomeo(){
 const H=D.homeostatico;
 if(!H){document.getElementById('subHomeo').innerHTML=
   '<span class="err">indisponivel</span>';return;}
 document.getElementById('subHomeo').innerHTML=
  'p̂(t) = p₀ + K₁·EWM(carga,T₁) − K₂·EWM(carga,T₂) · '+H.nota+
  (homeoValido(H)?(homeoCapacidade(H)?' <b style="color:#E67E22">Capacidade explicativa '+homeoCapacidade(H)+': R² '+H.r2+'. O ajuste e aceite pelo modelo, mas explica pouco do CP.</b>':''):' <span class="err">Sem ajuste valido: '+((H.validacao&&H.validacao.motivo)||'sem dados')+'</span>');
 const selM=document.getElementById('homeoMod');
 const HM=D.homeostatico_mod||{};
 if(selM.options.length<=1)
  selM.innerHTML='<option value="">Global</option>'+
   Object.keys(HM).map(m=>'<option>'+m+'</option>').join('');
 const hv=homeoValido(H);
 document.getElementById('homeoKpis').innerHTML=[
  ['K₁ (ganho fitness)',hv?H.k1:'—'],['K₂ (ganho fadiga)',hv?H.k2:'—'],
  ['T₁ (τ fitness)',hv?H.t1+'d':'—'],['T₂ (τ fadiga)',hv?H.t2+'d':'—'],
  ['R²'+(homeoCapacidade(H)?' (muito baixo)':''),H.r2],['Pontos de CP',H.n_testes]
 ].map(k=>'<div class="card"><div class="label">'+k[0]+'</div>'+
  '<div class="value">'+k[1]+'</div></div>').join('');
 drawHomeo(); tabelaHomeo();
}

function mostrarAlos(){
 const A=D.alostatico;
 if(!A){document.getElementById('subAlos').innerHTML=
   '<span class="err">indisponivel</span>';return;}
 const e=A.estado;
 document.getElementById('subAlos').innerHTML=
  A.n_dims+' de 6 dimensoes · anterior '+A.periodo_anterior.join(' a ')+
  ' · recente '+A.periodo_recente.join(' a ')+
  '<br><span style="font-size:12px">'+A.formula+
  ' · scores: ['+(A.scores||[]).join(', ')+'] → media '+A.total+
  '</span>';
 const pct=Math.round((A.total+1)/2*100);
 document.getElementById('alosCard').innerHTML=
  '<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;'+
  'padding:16px 20px;margin-bottom:12px">'+
  '<div style="font-size:11px;color:#8b949e;text-transform:uppercase;'+
  'letter-spacing:.5px;margin-bottom:6px">Adaptacao vs sobrecarga alostatica</div>'+
  '<div style="font-size:30px;font-weight:600;color:'+e.cor+'">'+
  (A.total>=0?'+':'')+A.total.toFixed(2)+
  '<span style="font-size:14px;color:#8b949e"> /±1.00</span></div>'+
  '<div style="font-size:13px;font-weight:600;color:'+e.cor+';margin:4px 0 12px">'+
  e.label+'</div>'+
  '<div style="display:flex;justify-content:space-between;font-size:10px;'+
  'color:#8b949e;margin-bottom:4px"><span>SOBRECARGA</span><span>ESTAVEL</span>'+
  '<span>ADAPTACAO</span></div>'+
  '<div style="position:relative;height:10px;border-radius:5px;'+
  'background:linear-gradient(to right,#e74c3c,#f39c12 45%,#27ae60)">'+
  '<div style="position:absolute;left:'+pct+'%;top:-3px;width:16px;height:16px;'+
  'border-radius:50%;background:'+e.cor+';border:2px solid #0e1117;'+
  'transform:translateX(-50%)"></div></div>'+
  '<div style="font-size:12px;color:#8b949e;margin-top:8px">'+e.desc+'</div></div>';

 document.getElementById('alosHead').innerHTML=
  ['Dimensao','Anterior (n dias)','Recente (n dias)','Δ %','Score']
   .map((c,i)=>'<th class="'+(i?'num':'')+'">'+c+'</th>').join('');
 document.getElementById('alosBody').innerHTML=A.dimensoes.map(function(d){
  if(d.ant==null)
   return '<tr><td>'+d.dim+'</td><td class="num" colspan="4" '+
    'style="color:#484f58">sem dados</td></tr>';
  if(d.delta_pct==null) return '<tr><td>'+d.dim+'</td><td class="num">'+d.ant+' '+d.unidade+'</td>'+
   '<td class="num">'+d.rec+' '+d.unidade+'</td><td class="num" colspan="2" style="color:#8b949e">'+
   (d.motivo||'sem variacao calculavel')+'</td></tr>';
  const cor=d.score>=0?'#2ECC71':'#E74C3C';
  return '<tr><td>'+d.dim+'</td>'+
   '<td class="num">'+d.ant+' '+d.unidade+
    '<span style="color:#8b949e;font-size:11px"> n='+d.n_ant+'</span></td>'+
   '<td class="num">'+d.rec+' '+d.unidade+
    '<span style="color:#8b949e;font-size:11px"> n='+d.n_rec+'</span></td>'+
   '<td class="num" style="color:'+cor+'">'+(d.delta_pct>=0?'+':'')+
    d.delta_pct.toFixed(1)+'%'+
    (d.metodo&&d.metodo.indexOf('absoluta')!==-1?
     ' <span style="color:#5DADE2" title="'+d.metodo+' — o TSB oscila em torno '+
     'de zero, a percentagem sobre a base seria instavel">abs</span>':'')+
    (d.saturado?
    ' <span style="color:#E67E22" title="acima de 50%: o score satura em ±1">▲</span>':'')+
    '</td>'+
   '<td class="num" style="color:'+cor+'">'+(d.score>=0?'+':'')+
    d.score.toFixed(3)+'</td></tr>';}).join('');
}


function hexRgba(h,a){h=h.replace('#','');
 return 'rgba('+parseInt(h.slice(0,2),16)+','+parseInt(h.slice(2,4),16)+','+
  parseInt(h.slice(4,6),16)+','+a+')';}

// Um painel por modalidade, como no dashboard: CTLgamma no eixo esquerdo
// (escala propria, porque cada modalidade tem o seu gamma) e CTL/ATL
// classicos ponteados no direito, so como contexto.
function drawCTLg(){
 const pm=(D.ftlm||{}).por_modalidade||{};
 const mods=Object.keys(pm);
 const cont=document.getElementById('painelMods');
 if(!mods.length){cont.innerHTML='<div class="sub">Sem dados</div>';return;}

 if(cont.dataset.n!==String(mods.length)){
  cont.dataset.n=String(mods.length);
  cont.innerHTML=mods.map(m=>
   '<div class="chartbox" style="margin-bottom:0">'+
   '<div class="legend" id="lgM'+m+'"></div>'+
   '<canvas id="chM'+m+'" height="190"></canvas></div>').join('');
 }

 // CTL/ATL classicos por modalidade, para o eixo direito
 const clas={};
 (D.sessoes||[]).forEach(function(s){
  clas[s.type]=clas[s.type]||{};
  clas[s.type][s.date]=(clas[s.type][s.date]||0)+(s.tl||0);});

 mods.forEach(function(m){
  const serie=janelaPMC(pm[m].serie||[]);
  const o=ctx('chM'+m,190); if(!o)return;
  const g=o.g,W=o.W,H=o.H;
  const PL=44,PR=40,PT=20,PB=20,w=W-PL-PR,h=H-PT-PB,n=serie.length;
  if(!n){noData(g,W,H);return;}
  const X=i=>PL+w*(n>1?i/(n-1):0.5);
  const cor=(D.cores||{})[m]||'#8b949e';

  // CTL e ATL classicos desta modalidade
  const datas=serie.map(d=>d.date);
  const cargas=datas.map(d=>(clas[m]||{})[d]||0);
  function ewmJS(v,span){const a=2/(span+1);let p=null;
   return v.map(function(x){p=(p===null)?x:a*x+(1-a)*p;return p;});}
  const ctl=ewmJS(cargas,42),atl=ewmJS(cargas,7);

  let mn=Infinity,mx=-Infinity;
  serie.forEach(function(d){const v=d.ctlg;if(v==null)return;
   if(v<mn)mn=v;if(v>mx)mx=v;});
  if(!isFinite(mn)){noData(g,W,H);return;}
  if(mn>0)mn=0; if(mx===mn)mx=mn+1;
  const Y=v=>PT+h-(v-mn)/(mx-mn)*h;
  const cmx=Math.max.apply(null,ctl.concat(atl,[1]));
  const Y2=v=>PT+h-v/cmx*h;

  g.strokeStyle='#21262d';g.lineWidth=1;
  for(let i=0;i<=3;i++){const y=PT+h*i/3;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}

  // classicos, ponteados e esbatidos
  [[ctl,[2,3],0.45],[atl,[5,3],0.30]].forEach(function(par){
   g.strokeStyle=cor;g.setLineDash(par[1]);g.globalAlpha=par[2];g.lineWidth=1.3;
   g.beginPath();
   par[0].forEach(function(v,i){const y=Y2(v);
    if(i===0)g.moveTo(X(i),y);else g.lineTo(X(i),y);});
   g.stroke();g.setLineDash([]);g.globalAlpha=1;});

  // CTLgamma
  g.strokeStyle=cor;g.lineWidth=2.4;g.beginPath();let st=false;
  serie.forEach(function(d,i){const v=d.ctlg;if(v==null){st=false;return;}
   if(!st){g.moveTo(X(i),Y(v));st=true;}else g.lineTo(X(i),Y(v));});
  g.stroke();

  g.fillStyle=cor;g.font='11px sans-serif';g.textAlign='left';
  g.fillText(m,PL,12);
  g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
  for(let i=0;i<=3;i++)g.fillText(Math.round(mx-(mx-mn)*i/3),PL-5,PT+h*i/3+3);
  g.textAlign='left';
  for(let i=0;i<=3;i++)g.fillText(Math.round(cmx-cmx*i/3),PL+w+5,PT+h*i/3+3);
  g.textAlign='center';
  const step=Math.ceil(n/4);
  serie.forEach(function(d,i){if(i%step!==0)return;
   g.fillText(d.date.slice(2,7),X(i),H-6);});
  g.textAlign='left';

  const v=pm[m];
  const leg=(D.ftlm||{}).fases_legenda||{};
  const f=leg[v.fase]||{};
  const em=gammaEstado(v);
  const rotuloG=em.padrao?' (padrão, ajuste rejeitado)':
    (em.origem==='calibracao'?' (calibrado)':'');
  document.getElementById('lgM'+m).innerHTML=
   '<span><i style="background:'+cor+'"></i>CTLγ γ='+v.gamma+rotuloG+
    ' R²='+(em.r2!=null?em.r2:'—')+'</span>'+
   '<span style="opacity:.6"><i style="background:'+cor+'"></i>CTL/ATL</span>'+
   (f.label?'<span style="color:'+f.cor+'">'+f.label+'</span>':'');

  registarTip('chM'+m,function(mxp,myp,rw){
   const esc=rw/W,x=mxp/esc;
   if(x<PL||x>PL+w)return '';
   const i=Math.round((x-PL)/w*(n-1));
   if(i<0||i>=n)return '';
   return '<div class="th">'+m+' · '+serie[i].date+'</div>'+
    linhaTip(cor,'CTLγ',serie[i].ctlg)+
    linhaTip(cor,'CTL (42d)',Math.round(ctl[i]))+
    linhaTip(cor,'ATL (7d)',Math.round(atl[i]));});
 });
}

// ─── FMT 5x5: matriz, valores proprios e mapa de atencao ────────────────
function corCel(v,mx){
 // azul (baixo) -> amarelo -> vermelho (alto), como a Figura 1 do paper
 const t=mx>0?Math.max(0,Math.min(1,v/mx)):0;
 if(t<0.5){const u=t/0.5;
  return 'rgb('+Math.round(59+(234-59)*u)+','+Math.round(130+(179-130)*u)+','+
   Math.round(246+(8-246)*u)+')';}
 const u=(t-0.5)/0.5;
 return 'rgb('+Math.round(234+(220-234)*u)+','+Math.round(179+(38-179)*u)+','+
  Math.round(8+(38-8)*u)+')';
}

function drawMatriz(){
 const F=D.fmt;
 const o=ctx('chMatriz',260); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 if(!F||F.erro||!F.resumo){noData(g,W,H,(F&&F.erro)||'Sem tensor');return;}
 const R=F.resumo, nomes=R.nomes, M=R.matriz, n=nomes.length;
 const PL=64,PT=26,PR=14,PB=14;
 const cel=Math.min((W-PL-PR)/n,(H-PT-PB)/n);
 let mx=0;
 M.forEach(l=>l.forEach(v=>{if(v!=null&&Math.abs(v)>mx)mx=Math.abs(v);}));
 for(let i=0;i<n;i++)for(let j=0;j<n;j++){
  const v=M[i][j];
  g.fillStyle=v==null?'#21262d':corCel(Math.abs(v),mx);
  g.fillRect(PL+j*cel,PT+i*cel,cel-1,cel-1);
  if(i===j){g.strokeStyle='#5DADE2';g.lineWidth=2;
   g.strokeRect(PL+j*cel,PT+i*cel,cel-1,cel-1);}
  if(cel>26&&v!=null){
   g.fillStyle=Math.abs(v)/mx>0.55?'#0d1117':'#e6e6e6';
   g.font='9px sans-serif';g.textAlign='center';
   g.fillText(v.toFixed(2),PL+j*cel+cel/2,PT+i*cel+cel/2+3);}
 }
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 nomes.forEach((nm,i)=>g.fillText(nm,PL-6,PT+i*cel+cel/2+3));
 g.textAlign='center';
 nomes.forEach((nm,j)=>g.fillText(nm,PL+j*cel+cel/2,PT-8));
 g.textAlign='left';
 g.fillStyle='#5DADE2';g.font='10px sans-serif';
 g.fillText('diagonal = κ = '+R.kappa,PL,H-2);

 registarTip('chMatriz',function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc,y=myp/esc;
  const j=Math.floor((x-PL)/cel), i=Math.floor((y-PT)/cel);
  if(i<0||j<0||i>=n||j>=n)return '';
  const v=M[i][j]; if(v==null)return '';
  return '<div class="th">'+nomes[i]+' × '+nomes[j]+'</div>'+
   '<div class="tr"><span>'+(i===j?'Variancia':'Covariancia')+'</span><b>'+
   v.toFixed(4)+'</b></div>'+
   (i===j?'<div class="tr"><span>Contributo para κ</span><b>'+
    (v/R.kappa*100).toFixed(0)+'%</b></div>':'');});
}

function drawEigen(){
 const F=D.fmt;
 const o=ctx('chEigen',260); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 if(!F||F.erro||!F.resumo){noData(g,W,H);return;}
 const ev=F.resumo.eigen.filter(v=>v>0);
 if(!ev.length){noData(g,W,H);return;}
 const PL=44,PT=16,PR=16,PB=30,w=W-PL-PR,h=H-PT-PB;
 const tot=ev.reduce((a,b)=>a+b,0), mx=ev[0];
 const bw=w/ev.length;
 g.strokeStyle='#21262d';
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 ev.forEach(function(v,i){
  const bh=h*v/mx;
  g.fillStyle=i===0?'#E67E22':'#5DADE2';g.globalAlpha=0.85;
  g.fillRect(PL+i*bw+bw*0.2,PT+h-bh,bw*0.6,bh);g.globalAlpha=1;
  g.fillStyle='#e6e6e6';g.font='10px sans-serif';g.textAlign='center';
  g.fillText((v/tot*100).toFixed(0)+'%',PL+i*bw+bw/2,PT+h-bh-5);
  g.fillStyle='#8b949e';
  g.fillText('λ'+(i+1),PL+i*bw+bw/2,H-8);});
 g.textAlign='right';g.fillStyle='#8b949e';
 for(let i=0;i<=4;i++)g.fillText((mx-mx*i/4).toFixed(2),PL-5,PT+h*i/4+3);
 g.textAlign='left';

 registarTip('chEigen',function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  const i=Math.floor((x-PL)/bw);
  if(i<0||i>=ev.length)return '';
  return '<div class="th">λ'+(i+1)+'</div>'+
   linhaTip(i===0?'#E67E22':'#5DADE2','Valor próprio',ev[i].toFixed(3))+
   linhaTip('#8b949e','% da variância total',(ev[i]/tot*100).toFixed(1)+'%');});
}

function drawAtencao(){
 const F=D.fmt;
 const o=ctx('chAtencao',200); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 if(!F||F.erro||!F.canais){noData(g,W,H);return;}
 const c=document.getElementById('canalFMT').value;
 const A=F.canais[c];
 if(!A){noData(g,W,H,'Canal indisponivel');return;}
 const p=A.pesos, n=p.length;
 const PL=44,PT=16,PR=14,PB=28,w=W-PL-PR,h=H-PT-PB;
 const mx=Math.max.apply(null,p)||1;
 const bw=w/n;
 g.strokeStyle='#21262d';
 for(let i=0;i<=3;i++){const y=PT+h*i/3;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 p.forEach(function(v,i){
  const bh=h*v/mx;
  g.fillStyle=A.cor;g.globalAlpha=0.35+0.65*(v/mx);
  g.fillRect(PL+i*bw+1,PT+h-bh,bw-2,bh);g.globalAlpha=1;});
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='center';
 const step=Math.ceil(n/8);
 A.lag.forEach(function(l,i){if(i%step!==0)return;
  g.fillText(l===0?'hoje':'d-'+l,PL+i*bw+bw/2,H-8);});
 g.textAlign='right';
 for(let i=0;i<=3;i++)g.fillText((mx-mx*i/3*100).toFixed(0)+'%',PL-5,PT+h*i/3+3);
 g.textAlign='left';

 document.getElementById('lgAtencao').innerHTML=
  '<span><i style="background:'+A.cor+'"></i>'+A.nome+'</span>'+
  '<span style="color:#8b949e">'+A.desc+'</span>';

 // explicacao do canal escolhido, no lugar do texto fixo generico --
 // reaproveita A.desc (ja calculado) e a fonte do proprio parametro
 // deste canal (D.fmt.calibracao), sem calcular nada de novo
 const chaveCal={load:'canal1_tau', hrv:'canal2_lag', super:'canal3_lag',
                risco:'canal4_lag'}[c];
 const cal=(D.fmt.calibracao||{})[chaveCal];
 const boxExpl=document.getElementById('explicacaoCanalFMT');
 if(boxExpl){
  let fonteTxt='';
  if(cal){
   fonteTxt = cal.fonte==='dados'
    ? ' <span style="color:#3FB950;">✓ calibrado com os teus dados</span> (n='+cal.n+', r²='+((cal.r2||0)*100).toFixed(0)+'%)'
    : ' <span style="color:#E67E22;">⚠ valor de referência do paper</span> — descreve outros atletas, não ti (dados insuficientes para calibrar este parâmetro)';
  } else if(c==='similar'){
   fonteTxt = ' <span style="color:#48C9B0;">geometria dos próprios tensores</span> — sem parâmetro a calibrar, é a similaridade real entre os dias.';
  }
  boxExpl.innerHTML='<div style="border-left:3px solid '+A.cor+';padding:6px 10px;">'+
   '<b style="color:'+A.cor+';">'+A.nome+'</b><br>'+
   '<span style="font-size:12px;">'+A.desc+'</span><br>'+
   '<span style="font-size:11px;">'+fonteTxt+'</span></div>';
 }

 registarTip('chAtencao',function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  const i=Math.floor((x-PL)/bw);
  if(i<0||i>=n)return '';
  return '<div class="th">'+A.datas[i]+' · '+(A.lag[i]===0?'hoje':'d-'+A.lag[i])+
   '</div>'+linhaTip(A.cor,'Peso',(A.pesos[i]*100).toFixed(1)+'%');});
}

function blocoPoder(){
 const E=(D.teste_eventos||{}).por_modalidade||{};
 const linhas=[];
 Object.keys(E).forEach(function(m){
  const p=E[m].poder; if(!p||p.poder_actual==null)return;
  const baixo=p.poder_actual<0.8;
  linhas.push('<tr><td>'+m+'</td>'+
   '<td class="num">'+E[m].maior_efeito+'</td>'+
   '<td class="num">+'+E[m].melhor_lag+'d</td>'+
   '<td class="num">'+p.n_por_grupo+'</td>'+
   '<td class="num" style="color:'+(baixo?'#E67E22':'#2ECC71')+'">'+
    Math.round(p.poder_actual*100)+'%</td>'+
   '<td class="num">'+(p.n_para_80pct||'—')+'</td></tr>');});
 const el=document.getElementById('poderBox');
 if(!el)return;
 if(!linhas.length){el.innerHTML='';return;}
 el.innerHTML='<h3>Poder estatistico</h3>'+
  '<div class="sub">Com quantos dias conseguirias detectar o efeito que '+
  'observas, se ele for real? Poder baixo significa que <b>nao detectar '+
  'nao e o mesmo que nao existir</b>.</div>'+
  '<div class="wrap"><table><thead><tr>'+
  ['Modalidade','d observado','Lag','n/grupo','Poder','n para 80%']
   .map((c,i)=>'<th class="'+(i?'num':'')+'">'+c+'</th>').join('')+
  '</tr></thead><tbody>'+linhas.join('')+'</tbody></table></div>';
}

function tabelaCalibracao(){
 const F=D.fmt, C=(F||{}).calibracao;
 if(!C){document.getElementById('calBody').innerHTML=
   '<tr><td class="loading">sem calibracao</td></tr>';return;}
 document.getElementById('calHead').innerHTML=
  ['Parametro','Valor','Fonte','r','r² (var. expl.)','n','Leitura']
   .map((c,i)=>'<th class="'+(i>0&&i<6?'num':'')+'">'+c+'</th>').join('');

 const linhas=[
  ['canal1_tau','τ do canal 1 (carga)','dias',
   'Ao fim de quantos dias a carga deixa de pesar no teu HRV?'],
  ['canal2_lag','lag do canal 2 (HRV)','dias',
   'Quantos dias depois da carga o teu HRV cai mais?'],
  ['canal3_lag','lag do canal 3 (supercomp.)','dias',
   'Quantos dias depois de um bloco a tua CP sobe mais?'],
  ['canal4_lag','τ do canal 4 (risco)','dias',
   'Em que horizonte o κ antecipa quedas de CP?'],
 ];
 let html=linhas.map(function(l){
  const v=C[l[0]]||{};
  const dados=v.fonte==='dados';
  const cor=dados?'#2ECC71':'#E67E22';
  const REJ=(C.parametros_rejeitados||{})[l[0]];
  const CORF={forte:'#2ECC71',moderada:'#F4D03F',fraca:'#E67E22',residual:'#E74C3C'};
  const cf=CORF[v.forca]||'#8b949e';
  let nota=dados?l[3]:(v.motivo||l[3]);
  if(REJ)
   nota='<span style="color:#E67E22">Rejeitado: '+REJ.motivo+'. '+
        'A usar o valor de referencia.</span>';
  else if(v.aviso_causalidade)
   nota='<span style="color:#E74C3C">⚠ '+v.aviso_causalidade+'</span>';
  else if(v.aviso)nota='<span style="color:#E67E22">⚠ '+v.aviso+'</span>';
  else if(v.interpretacao)nota=v.interpretacao;
  // Quando o valor foi encontrado mas rejeitado, mostrar os dois: o que os
  // dados deram e o que esta realmente a ser usado.
  const mostrado = REJ ? REJ.usado : v.valor;
  return '<tr><td>'+l[1]+'</td>'+
   '<td class="num">'+(mostrado!=null?mostrado+' '+l[2]:'—')+
    (REJ?'<br><span style="font-size:11px;color:#E74C3C">'+
     'dados deram '+REJ.valor_encontrado+' — nao usado</span>':'')+
    (v.fronteira?' <span style="color:#E67E22" title="valor no extremo da '+
     'grelha — nao e um optimo">⚠</span>':'')+'</td>'+
   '<td class="num" style="color:'+(REJ?'#E67E22':cor)+'" title="'+
    (REJ?REJ.motivo:(v.motivo||''))+'">'+
    (REJ?'referencia':(dados?'teus dados':'referencia'))+'</td>'+
   '<td class="num">'+(v.r!=null?v.r:'—')+'</td>'+
   '<td class="num" style="color:'+cf+'">'+
    (v.r2!=null?v.r2+' ('+v.variacao_explicada_pct+'%)':'—')+
    (v.forca?'<br><span style="font-size:11px">'+v.forca+'</span>':'')+'</td>'+
   '<td class="num">'+(v.n!=null?v.n:'—')+'</td>'+
   '<td style="font-size:12px;color:#8b949e">'+nota+
    (v.destendenciado?'<br><span style="color:#5DADE2">sem tendencia de longo '+
     'prazo (residuos face a media movel de ±90d)</span>':'')+
    '</td></tr>';}).join('');

 const L=C.limiares_lambda1||{};
 const dl=L.fonte==='dados';
 html+='<tr><td>Limiares λ₁ (focal / multi)</td>'+
  '<td class="num">'+(L.alto!=null?(L.alto*100).toFixed(0)+'% / '+
   (L.baixo*100).toFixed(0)+'%':'—')+'</td>'+
  '<td class="num" style="color:'+(dl?'#2ECC71':'#E67E22')+'">'+
   (dl?'teus dados':'referencia')+'</td>'+
  '<td class="num">—</td><td class="num">—</td>'+
  '<td class="num">'+(L.n||'—')+'</td>'+
  '<td style="font-size:12px;color:#8b949e">'+
   (dl?'media ±1 desvio do teu λ₁ (media '+L.media+', desvio '+L.desvio+
       '; p70/p30 seriam '+L.p70+'/'+L.p30+')'
     :(L.motivo||''))+'</td></tr>';
 document.getElementById('calBody').innerHTML=html;

 // veredicto: ha sinal utilizavel ou os canais sao decorativos?
 const V=C.veredicto;
 const ev=document.getElementById('veredicto');
 if(ev&&V)ev.innerHTML=
  '<div style="border-left:4px solid '+V.cor+';background:'+hexRgba(V.cor,0.08)+
  ';padding:11px 15px;border-radius:0 6px 6px 0;margin-bottom:10px;font-size:13px">'+
  '<b>'+({utilizavel:'Sinal utilizavel',fraco:'Sinal fraco',
          sem_sinal:'Sem sinal detectavel'}[V.nivel]||V.nivel)+'</b><br>'+
  V.texto+'</div>';

 const R=C.resumo||{};
 const el=document.getElementById('notaCal');
 if(el){
  let h=R.derivados_dos_dados+' de '+R.total+' parametros vem dos teus dados. '+R.nota;
  if((C.avisos||[]).length)
   h+='<div style="margin-top:8px;border-left:3px solid #E67E22;background:#161b22;'+
      'padding:8px 12px;border-radius:0 6px 6px 0;font-size:12px">'+
      '<b>A ter em conta:</b><br>'+C.avisos.join('<br>')+'</div>';
  el.innerHTML=h;}
}

// Regime do dia -- kappa (instabilidade do FMT) vs TSB (forma), classificado
// pelos MESMOS percentis p75/p25 ja' usados no diagnostico de blocos de CP
// (pmc.py, _diagnostico_cp) -- nao inventa um segundo criterio de limiar.
// Nao existia nenhuma versao ligada desta classificacao no app (so' um
// ficheiro fmt_graficos.py orfao, nunca importado) -- construida de raiz
// com a mesma logica de percentis ja usada no resto do projecto.
const REGIME_CFG = {
 fadiga:{label:'Fadiga silenciosa', cor:'#E74C3C',
   desc:'TSB positivo mas κ elevado — o corpo parece descansado nos números clássicos, mas a instabilidade autonómica está alta.'},
 acumulacao:{label:'Acumulação intensa', cor:'#F39C12',
   desc:'TSB negativo e κ elevado — carga alta e instabilidade alta ao mesmo tempo; bloco de trabalho pesado, sem ainda mostrar sinais de quebra.'},
 supercompensacao:{label:'Supercompensação', cor:'#27AE60',
   desc:'TSB positivo e κ baixo — forma alta com estabilidade autonómica alta; janela típica de melhor desempenho.'},
 recovery:{label:'Recovery', cor:'#2ECC71',
   desc:'TSB negativo mas κ baixo — ainda em défice de forma, porém o sistema autonómico já está estável; a recuperar sem sinais de stress.'},
 normal:{label:'Normal', cor:'#8b949e',
   desc:'κ dentro da faixa habitual (entre o teu p25 e p75 histórico) — sem sinal forte em nenhuma direcção.'},
};

// Regime em uma data i: percentis p25/p75 EXPANSIVOS, calculados so' com os
// valores de κ ate' i (inclusive). Acrescentar dias posteriores nao muda o
// regime de i. Hoje e' o ultimo indice, por isso o resultado de hoje coincide
// com o que daria sobre o historico inteiro.
function regimeNaData(serieK, i, tsb){
 const vals=[];
 for(let j=0;j<=i && j<serieK.length;j++){
  const v=serieK[j]&&serieK[j].kappa; if(v!=null) vals.push(v);}
 const kappa=vals.length?vals[vals.length-1]:null;
 if(vals.length<10 || tsb==null || kappa==null) return null;
 const ord=vals.slice().sort((a,b)=>a-b);
 function percentil(p){ const k=Math.min(ord.length-1,Math.floor(p*ord.length)); return ord[k]; }
 const p75=percentil(0.75), p25=percentil(0.25);
 const kAlto=kappa>p75, kBaixo=kappa<p25;
 let chave='normal';
 if(tsb>0 && kAlto) chave='fadiga';
 else if(tsb<0 && kAlto) chave='acumulacao';
 else if(tsb>0 && kBaixo) chave='supercompensacao';
 else if(tsb<0 && kBaixo) chave='recovery';
 return {chave:chave, kappa:kappa, p25:p25, p75:p75, tsb:tsb};
}

function mostrarRegimeHoje(){
 const box=document.getElementById('regimeHojeCard');
 if(!box) return;
 const serieK=(D.ftlm&&D.ftlm.serie)||[];
 const tsbHoje=(D.actual||{}).tsb;
 const r=regimeNaData(serieK, serieK.length-1, tsbHoje);
 if(!r){
  box.innerHTML='<span class="sub">dados insuficientes para classificar o regime de hoje</span>';
  return;
 }
 const cfg=REGIME_CFG[r.chave];
 box.innerHTML=
  '<div style="font-size:15px;font-weight:600;color:'+cfg.cor+';">'+cfg.label+'</div>'+
  '<div style="font-size:11px;color:#8b949e;margin-top:4px;">TSB hoje: '+r.tsb.toFixed(1)+
  ' &middot; κ hoje: '+r.kappa.toFixed(3)+
  '<br>κ até hoje (expansivo): p25='+r.p25.toFixed(3)+' &middot; p75='+r.p75.toFixed(3)+'</div>'+
  '<div style="font-size:12px;margin-top:8px;">'+cfg.desc+'</div>';
}

function mostrarFMT5(){
 const F=D.fmt;
 const sub=document.getElementById('subFMT5');
 if(!F||F.erro){
  sub.innerHTML='<span class="err">'+((F&&F.erro)||'FMT indisponivel')+'</span>';
  return;}
 const lim=(F.resumo||{}).limiares||{};
 sub.innerHTML='Tensor '+F.dimensoes.length+'&times;'+F.dimensoes.length+
  ' sobre janela de '+F.janela+' dias &middot; dimensoes: '+F.dimensoes.join(', ')+
  ' &middot; dia '+F.dia+
  (lim.fonte==='dados'||lim.fonte==='historico'
   ? '<br><span style="font-size:12px">Limiares focal/multissistemico da tua '+
     'distribuicao de λ₁ ('+(lim.focal_acima*100).toFixed(0)+'% / '+
     (lim.multi_abaixo*100).toFixed(0)+'%), sobre '+
     (lim.n_historico||lim.n||0)+' dias</span>'
   : '<br><span style="font-size:12px;color:#E67E22">Limiares de referencia '+
     '(0.55/0.35) — so '+(lim.n_historico||lim.n||0)+' dias de historico, '+
     'precisa de 60 para os derivar dos teus dados</span>');
 const L=(F.resumo||{}).leitura;
 document.getElementById('leituraFMT').innerHTML= L
  ? '<div style="border-left:3px solid '+L.cor+';background:#161b22;padding:9px 13px;'+
    'border-radius:0 6px 6px 0;font-size:13px;margin:4px 0 12px">'+L.texto+'</div>' : '';
 const sel=document.getElementById('canalFMT');
 if(!sel.options.length)
  sel.innerHTML=Object.keys(F.canais||{}).length?Object.keys(F.canais).map(k=>
   '<option value="'+k+'">'+F.canais[k].nome+'</option>').join(''):
   '<option value="">sem canais com dados</option>';
 document.getElementById('notaAtencao').textContent=F.nota_atencao||'';
 tabelaCalibracao(); blocoPoder();
 drawMatriz();drawEigen();drawAtencao();
}

function drawFMT(){
 if(!D.ftlm){return;}
 drawLinhas('chFMT','lgFMT',janelaPMC(D.ftlm.serie),['kappa','lambda1'],
  {kappa:'#E74C3C',lambda1:'#F4D03F'},
  {kappa:'κ (instabilidade)',lambda1:'λ₁ (dominancia)'},
  {off:OFFK,redraw:drawFMT,height:220,escala:'propria'});

 // legenda: que dimensao esta a puxar o kappa de hoje, e se o nivel
 // actual e' alto/baixo/normal -- reaproveita a MESMA matriz ja
 // desenhada em chMatriz (a diagonal) e os MESMOS percentis ja usados
 // no regime do dia, nada recalculado de raiz
 const box=document.getElementById('subFMT');
 if(!box) return;
 const R=(D.fmt||{}).resumo;
 const serieK=(D.ftlm.serie||[]).map(d=>d.kappa).filter(v=>v!=null);
 if(!R || !R.matriz || serieK.length<10){ box.innerHTML=''; return; }

 const nomes=R.nomes, M=R.matriz, kappaHoje=R.kappa;
 const contrib=nomes.map((nm,i)=>({nome:nm, valor:M[i][i], pct:kappaHoje?M[i][i]/kappaHoje*100:0}))
  .sort((a,b)=>b.valor-a.valor);

 const ord=serieK.slice().sort((a,b)=>a-b);
 function pct(p){ return ord[Math.min(ord.length-1,Math.floor(p*ord.length))]; }
 const p75=pct(0.75), p25=pct(0.25);
 let sinal, corSinal;
 if(kappaHoje>p75){ sinal='atenção — acima do teu p75 histórico'; corSinal='#E74C3C'; }
 else if(kappaHoje<p25){ sinal='estável — abaixo do teu p25 histórico'; corSinal='#3FB950'; }
 else { sinal='normal — dentro da faixa habitual (p25–p75)'; corSinal='#8b949e'; }

 box.innerHTML='<div style="font-size:12px;">'+
  '<b style="color:'+corSinal+';">κ hoje: '+sinal+'</b><br>'+
  '<span style="color:#8b949e;">O que está a puxar mais (contribuição para κ):</span> '+
  contrib.map(c=>c.nome+' <b>'+c.pct.toFixed(0)+'%</b>').join(' · ')+
  '</div>';
}
// ── estado dos gammas: origem (calibracao ou padrao), R² do gamma aplicado ──
// g = F.gammas.perf | F.gammas.rec (metadados no proprio objeto) ou
// por_modalidade[m] (metadados do ajuste em gamma_fit). Respostas antigas,
// sem 'origem', ficam sem rotulo de estado e mostram so o valor.
function gammaEstado(g){
 const gf=g.gamma_fit||g;
 const antigo=(g.origem===undefined);
 let r2=(g.r2_aplicado!=null?g.r2_aplicado:(antigo?g.r2:null));
 return {gamma:g.gamma, origem:g.origem, padrao:(g.origem==='padrao'),
         antigo:antigo, r2:r2, lag:g.lag, suav:g.suavizacao,
         motivo:gf.motivo||'',
         candidato:(gf.gamma_encontrado!=null?gf.gamma_encontrado:null)};
}
function gammaLagTxt(e){
 if(e.lag==null)return '';
 return ' · lag '+e.lag+(e.suav>1?' · média móvel '+e.suav+' d':'');
}
function gammaTexto(g){
 if(!g)return '—';
 const e=gammaEstado(g);
 const r2=(e.r2!=null?'R&sup2; do γ aplicado '+e.r2:'sem ajuste');
 if(e.antigo)return e.gamma+' (R&sup2; '+(g.r2)+')';
 if(e.padrao)return e.gamma+' <span title="'+e.motivo+'" style="color:#E67E22">padrão — ajuste rejeitado</span> ('+r2+')'+gammaLagTxt(e);
 return e.gamma+' calibrado ('+r2+')'+gammaLagTxt(e);
}
function tabelaGammas(){
 const pm=(D.ftlm||{}).por_modalidade||{};
 const mods=Object.keys(pm);
 document.getElementById('gHead').innerHTML=
  ['Modalidade','γ','R² (γ aplicado)','dias-alvo','Sessoes','CTLγ (% do proprio max)','Fase']
   .map((c,i)=>'<th class="'+(i&&i<6?'num':'')+'">'+c+'</th>').join('');
 const leg=(D.ftlm||{}).fases_legenda||{};
 // O CTLγ absoluto NAO e comparavel entre modalidades: gammas diferentes
 // dao ordens de grandeza diferentes (γ=0.9 chega a dezenas de milhar,
 // γ=0.1 a dezenas). Mostramos a percentagem do proprio maximo, que e
 // comparavel, e o valor bruto em segunda linha.
 document.getElementById('gBody').innerHTML=mods.map(function(m){
  const v=pm[m], f=leg[v.fase]||{}, e=gammaEstado(v);
  const gCel=e.padrao
   ? v.gamma+'<br><span style="font-size:11px;color:#E67E22" title="'+e.motivo+'">'+
     'padrão'+(e.candidato!=null?' (dados deram '+e.candidato+')':'')+'</span>'
   : v.gamma+(e.origem==='calibracao'
     ? '<br><span style="font-size:11px;color:#2ECC71">calibrado</span>':'');
  return '<tr><td style="color:'+(D.cores[m]||'#e6e6e6')+'">'+m+'</td>'+
   '<td class="num">'+gCel+'</td>'+
   '<td class="num">'+(e.r2!=null?e.r2:'—')+'</td>'+
   '<td class="num">'+v.n+'</td><td class="num">'+v.n_sessoes+'</td>'+
   '<td class="num">'+(v.ctlg_pct!=null?v.ctlg_pct+'%':'—')+
    '<br><span style="font-size:11px;color:#8b949e">'+
    (v.ctlg_actual>=1000?Math.round(v.ctlg_actual).toLocaleString('pt-PT')
     :v.ctlg_actual)+' bruto</span></td>'+
   '<td style="color:'+(f.cor||'#8b949e')+'">'+(f.label||v.fase)+'</td></tr>';
 }).join('');
}
// PMC principal: CTL/ATL/TSB a esquerda, CTLgamma a direita com valores
// reais (nao re-escalados), bandas de fase ao fundo e barras de carga por
// modalidade num painel proprio — como no dashboard original.
function drawPMC(){
 const verF=document.getElementById('verFTLM').checked;
 const verB=document.getElementById('verFases').checked;
 const dados=janelaPMC(D.serie);
 const F=D.ftlm;

 // juntar as series CTLgamma pela data
 let idxF={};
 if(verF&&F) (F.serie||[]).forEach(function(r){idxF[r.date]=r;});
 const comb=dados.map(function(d){
  const f=idxF[d.date]||{};
  return Object.assign({},d,{
   ctlg_perf:f.ctlg_perf,ctlg_rec:f.ctlg_rec,fase:f.fase,dctlg:f.dctlg});});

 const o=ctx('chPMC',330); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const PL=52,PR=56,PT=14,PB=22,w=W-PL-PR,h=H-PT-PB;
 const n=comb.length;
 if(!n){noData(g,W,H);return;}
 const X=i=>PL+w*(n>1?i/(n-1):0.5);

 // ── bandas de fase ──
 if(verB&&F){
  const leg=F.fases_legenda||{};
  let ini=0;
  for(let i=1;i<=n;i++){
   const mudou=(i===n)||(comb[i].fase!==comb[ini].fase);
   if(!mudou)continue;
   const ph=leg[comb[ini].fase];
   if(ph&&comb[ini].fase!=='TRANSITION'){
    g.fillStyle=hexRgba(ph.cor,0.10);
    g.fillRect(X(ini),PT,Math.max(1,X(i-1)-X(ini)),h);}
   ini=i;}
 }

 // ── eixo esquerdo: CTL, ATL, TSB (mesma unidade) ──
 const esq=['ctl','atl','tsb'].filter(k=>!OFFP[k]);
 let mn=0,mx=1;
 ['ctl','atl','tsb'].forEach(k=>comb.forEach(function(d){
  if(d[k]==null)return; if(d[k]<mn)mn=d[k]; if(d[k]>mx)mx=d[k];}));
 const YE=v=>PT+h-(v-mn)/(mx-mn)*h;

 g.strokeStyle='#21262d';g.lineWidth=1;
 for(let i=0;i<=4;i++){const y=PT+h*i/4;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();}
 if(mn<0){g.strokeStyle='#484f58';g.setLineDash([3,3]);g.beginPath();
  g.moveTo(PL,YE(0));g.lineTo(PL+w,YE(0));g.stroke();g.setLineDash([]);}

 // TSB preenchido ate zero, como no original
 if(esq.indexOf('tsb')!==-1){
  g.fillStyle='rgba(39,174,96,0.15)';g.beginPath();
  let st=false;
  comb.forEach(function(d,i){if(d.tsb==null)return;
   if(!st){g.moveTo(X(i),YE(0));st=true;} g.lineTo(X(i),YE(d.tsb));});
  if(st){g.lineTo(X(n-1),YE(0));g.closePath();g.fill();}
 }

 [['ctl',2.2],['atl',2.2],['tsb',1]].forEach(function(par){
  const k=par[0]; if(OFFP[k])return;
  g.strokeStyle=k==='tsb'?'rgba(39,174,96,0.55)':COR[k];
  g.lineWidth=par[1];g.beginPath();let st=false;
  comb.forEach(function(d,i){const v=d[k];if(v==null){st=false;return;}
   const x=X(i),y=YE(v); if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);});
  g.stroke();});

 // ── eixo direito: CTLgamma ──
 // Em indice, cada serie e normalizada ao seu proprio min-max: gamma
 // diferentes dao ordens de grandeza diferentes e sobreporiam-se mal.
 const modoG=document.getElementById('escCTLgPMC').value;
 const serG=['ctlg_perf','ctlg_rec'].filter(k=>verF&&!OFFP[k]);
 const limG={};
 serG.forEach(function(k){
  let a=Infinity,b=-Infinity;
  comb.forEach(function(d){const v=d[k];if(v==null)return;
   if(v<a)a=v;if(v>b)b=v;});
  if(!isFinite(a)){a=0;b=1;} if(b===a)b=a+1;
  limG[k]=[a,b];});
 let gmn=Infinity,gmx=-Infinity;
 if(modoG==='real'){
  serG.forEach(function(k){gmn=Math.min(gmn,limG[k][0]);gmx=Math.max(gmx,limG[k][1]);});
  if(gmn>0)gmn=0; if(gmx===gmn)gmx=gmn+1;
 } else { gmn=0; gmx=100; }
 const temG=serG.length&&isFinite(gmn);
 function YD(k,v){
  if(modoG==='real')return PT+h-(v-gmn)/(gmx-gmn)*h;
  const[a,b]=limG[k]; return PT+h-((v-a)/(b-a))*h;}
 if(temG){
  serG.forEach(function(k){
   g.strokeStyle=k==='ctlg_perf'?'#2980b9':'#8e44ad';
   g.lineWidth=1.6;g.setLineDash(k==='ctlg_perf'?[6,3]:[2,3]);
   g.globalAlpha=0.85;g.beginPath();let st=false;
   comb.forEach(function(d,i){const v=d[k];if(v==null){st=false;return;}
    const x=X(i),y=YD(k,v); if(!st){g.moveTo(x,y);st=true;}else g.lineTo(x,y);});
   g.stroke();g.setLineDash([]);g.globalAlpha=1;});
  g.fillStyle='#8e44ad';g.font='10px sans-serif';g.textAlign='left';
  for(let i=0;i<=4;i++){
   const txt = modoG==='indice' ? Math.round(100-100*i/4)+'%'
    : (Math.abs(gmx)>=10000?((gmx-(gmx-gmn)*i/4)/1000).toFixed(0)+'k'
       :Math.round(gmx-(gmx-gmn)*i/4));
   g.fillText(txt,PL+w+6,PT+h*i/4+3);}
 }

 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 for(let i=0;i<=4;i++)g.fillText(Math.round(mx-(mx-mn)*i/4),PL-6,PT+h*i/4+3);

 // ── legenda ──
 const it=[['ctl','CTL (fitness)',COR.ctl],['atl','ATL (fadiga)',COR.atl],
           ['tsb','TSB (forma)','#2ECC71']];
 if(verF&&F){
  const gg=F.gammas||{};
  const rotG=(x)=>(x?x.gamma+(x.origem==='padrao'?' padrão':(x.origem==='calibracao'?' calibrado':'')):'?');
  it.push(['ctlg_perf','CTLγ perf (γ='+rotG(gg.perf)+')','#2980b9']);
  it.push(['ctlg_rec','CTLγ rec (γ='+rotG(gg.rec)+')','#8e44ad']);}
 document.getElementById('lgPMC').innerHTML=it.map(x=>
  '<span class="tog'+(OFFP[x[0]]?' off':'')+'" data-k="'+x[0]+'">'+
  '<i style="background:'+x[2]+'"></i>'+x[1]+'</span>').join('');
 document.querySelectorAll('#lgPMC span.tog').forEach(function(sp){
  sp.onclick=function(){OFFP[sp.dataset.k]=!OFFP[sp.dataset.k];drawPMC();};});

 // ── tooltip ──
 registarTip('chPMC',function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  if(x<PL||x>PL+w)return '';
  const i=Math.round((x-PL)/w*(n-1));
  if(i<0||i>=n)return '';
  const d=comb[i];
  let html='<div class="th">'+d.date+'</div>';
  it.forEach(function(t){
   if(OFFP[t[0]]||d[t[0]]==null)return;
   const v=d[t[0]];
   let txt=Math.abs(v)>=1000?Math.round(v).toLocaleString('pt-PT'):v.toFixed(1);
   // o eixo pode estar em indice, mas o tooltip mostra sempre o valor real
   if(modoG==='indice'&&limG[t[0]]){
    const[a,b]=limG[t[0]];
    txt+=' <span style="color:#8b949e">('+Math.round((v-a)/(b-a)*100)+'%)</span>';}
   html+=linhaTip(t[2],t[1].split(' (')[0],txt);});
  if(d.load)html+=linhaTip('#586574','Carga',Math.round(d.load));
  if(d.tsb!=null){const e=estadoDe(d.tsb);
   html+='<div class="tr" style="border-top:1px solid #30363d;margin-top:4px;'+
    'padding-top:4px"><span>Forma</span><b style="color:'+e.cor+'">'+e.label+'</b></div>';}
  if(d.fase&&F){const ph=(F.fases_legenda||{})[d.fase];
   if(ph)html+='<div class="tr"><span>Fase</span><b style="color:'+ph.cor+'">'+
    ph.label+'</b></div>';}
  if(d.dctlg!=null)html+=linhaTip('#8b949e','ΔCTLγ',d.dctlg.toFixed(4)+'/d');
  return html;});

 drawLoad(comb,PL,PR,X);
}

// painel de barras: carga diaria empilhada por modalidade
function drawLoad(comb,PL,PR,X){
 const o=ctx('chLoad',120); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 const PT=6,PB=20,h=H-PT-PB,w=W-PL-PR,n=comb.length;
 const mods=(D.ciclicos||[]).concat(['WeightTraining']);
 const porDia={};
 (D.sessoes||[]).forEach(function(s){
  porDia[s.date]=porDia[s.date]||{};
  porDia[s.date][s.type]=(porDia[s.date][s.type]||0)+(s.tl||0);});
 let mx=0;
 comb.forEach(function(d){
  const v=porDia[d.date]||{};
  const t=mods.reduce((a,m)=>a+(v[m]||0),0); if(t>mx)mx=t;});
 if(!mx){noData(g,W,H,'Sem carga');return;}
 const bw=Math.max(1,w/n*0.8);
 comb.forEach(function(d,i){
  const v=porDia[d.date]||{}; let acc=0;
  mods.forEach(function(m){
   const val=v[m]||0; if(!val)return;
   const bh=h*val/mx;
   g.fillStyle=(D.cores||{})[m]||'#7F8C8D';
   g.fillRect(X(i)-bw/2,PT+h-acc-bh,bw,bh);
   acc+=bh;});});
 g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
 g.fillText(Math.round(mx),PL-6,PT+8);
 g.fillText('0',PL-6,PT+h);
 g.textAlign='center';
 const step=Math.ceil(n/8);
 comb.forEach(function(d,i){if(i%step!==0)return;
  g.fillText(d.date.slice(0,7),X(i),H-6);});
 g.textAlign='left';
 g.fillStyle='#8b949e';g.font='10px sans-serif';
 g.fillText('Carga por modalidade',PL+4,PT+10);
}
// ─── Testes sugeridos ───────────────────────────────────────────────────
// O painel sai dos dados: cada esforco maximo e detectado sozinho, por isso
// a sugestao desaparece quando o teste for feito. Nada para marcar a mao.
// Um unico pedido a /api/protocolo por carregamento (carregarSugestoes, chamada
// no fim do script); a apresentacao (mostrarSugestoes) so le SUG e pode correr
// de novo sem pedir nada — load() chama-a quando D ja existe, para recolorir.
// Assim as sugestoes nao dependem de /api/pmc (falha ou demora).
let SUG=null;
async function carregarSugestoes(){
 try{ SUG=await fetch('/api/protocolo').then(r=>r.json()); }
 catch(e){ return; }
 mostrarSugestoes();
}
function mostrarSugestoes(){
 const d=SUG;
 if(!d)return;
 const sug=d.sugestoes||[];
 const el=document.getElementById('sugestoesTeste');
 if(!el)return;
 if(!sug.length){
  el.innerHTML='<div style="border-left:3px solid #2ECC71;background:#161b22;'+
   'padding:9px 13px;border-radius:0 6px 6px 0;font-size:13px;margin:10px 0">'+
   'Todos os testes em dia. O sistema deteta esforcos maximos sozinho — '+
   'nao precisas de marcar nada.</div>';
  return;}

 // um por modalidade, o mais atrasado
 const porMod={};
 sug.forEach(function(s){
  if(!porMod[s.modalidade]||s.atraso_dias>porMod[s.modalidade].atraso_dias)
   porMod[s.modalidade]=s;});

 const cov=(d.cobertura||{}).por_modalidade||{};
 const cartoes=Object.keys(porMod).map(function(m){
  const s=porMod[m];
  const cor=(D&&D.cores?D.cores[m]:null)||'#8b949e';
  const urgente=s.urgencia==='alta';
  // quantos testes ja existem nessa duracao
  let n='';
  const c=(cov[m]||{})[s.nome];
  if(c)n=c.n+' esforcos detetados';
  return '<div style="flex:1;min-width:190px;background:#161b22;border:1px solid '+
   (urgente?'#E67E22':'#30363d')+';border-radius:8px;padding:10px 13px">'+
   '<div style="color:'+cor+';font-weight:600;font-size:13px">'+m+'</div>'+
   '<div style="font-size:13px;margin:3px 0">'+s.nome+'</div>'+
   '<div style="font-size:11px;color:#8b949e">'+s.mede+'</div>'+
   '<div style="font-size:12px;margin-top:6px;color:'+
    (urgente?'#E67E22':'#8b949e')+'">'+s.dias_desde_ultimo+'d desde o ultimo'+
    ' ('+Math.round(s.ultimo_valor)+'W)</div>'+
   (n?'<div style="font-size:11px;color:#8b949e">'+n+'</div>':'')+'</div>';
 }).join('');

 el.innerHTML='<details class="expl" open style="margin:10px 0"><summary>'+
  'Testes sugeridos ('+Object.keys(porMod).length+' modalidades)</summary>'+
  '<div class="expl-corpo">'+
  '<p style="font-size:13px">Estes esforcos produzem hoje um diagnostico '+
  '(se a carga explica a performance) e nao atualizam os parametros do '+
  'modelo. Nao sao obrigatorios e nao ha nada para marcar: uma prova, um bloco de intervalos '+
  'duro ou uma saida forte contam, desde que chegues perto do teu maximo '+
  'nessa duracao. A sugestao desaparece sozinha quando acontecer.</p>'+
  '<div style="display:flex;gap:10px;flex-wrap:wrap">'+cartoes+'</div>'+
  '</div></details>';
}

// <<registo-testes>>
// ─── Deteccao automatica de observacoes MMP (substitui o fluxo Confirmar/Rejeitar) ───
// Os esforcos perto de um maximo (melhor recente, season ou all-time) sao detetados e
// registados sozinhos depois de cada sincronizacao de curvas; entram no estimador e na
// validacao se forem cientificamente elegiveis, SEM confirmacao individual. Detetar nao e
// validar o modelo. A unica acao manual e uma exclusao OPCIONAL (nunca um requisito de inclusao).
// A lista so e pedida quando o painel e aberto; nenhuma acao manual sem clique e motivo escrito.
let REG=null, REG_CARREGADO=false, REG_FILTRO={mod:'',dur:'',inc:'',ref:''}, REG_ABRIR=null, REG_MSG='';
const REG_MAX=40;
const REG_REF={recente:'melhor recente',season:'season',all_time:'all-time'};
function regEsc(s){
 return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function regBadge(txt,cor,titulo){
 return '<span title="'+regEsc(titulo||'')+'" style="display:inline-block;padding:1px 7px;'+
  'border-radius:10px;font-size:11px;border:1px solid '+cor+';color:'+cor+'">'+regEsc(txt)+'</span>';}
function regW(v){return (v==null||isNaN(Number(v)))?'?':String(Math.round(Number(v)*10)/10);}
function regPct(v){return (v==null||isNaN(Number(v)))?'?':(Math.round(Number(v)*10)/10).toFixed(1)+'%';}
function regNota(txt){return '<div style="font-size:11px;color:#8b949e;margin-top:2px">'+txt+'</div>';}
function regTemporal(h){
 if(!h)return '';
 return '<div style="font-size:12px;margin:4px 0;color:#c9d1d9">Historico temporal ('+regEsc(h.estado||'?')+'): '+
  regEsc(h.n_conhecidas_no_dia_ou_antes)+' de '+regEsc(h.n_observacoes)+' observacoes foram detetadas no dia do '+
  'esforco ou antes; '+regEsc(h.n_conhecidas_so_depois)+' so depois; '+regEsc(h.n_sem_data_de_deteccao_verificavel)+
  ' sem data de deteccao verificavel. '+regEsc(h.nota||'')+' A validacao retrospetiva usa todas as observacoes '+
  'atuais; a validacao &quot;conhecido na epoca&quot; so usa as ja detetadas antes da data de cada alvo.</div>';}
function regCurta(o){
 const r=o.referencias&&o.referencias[o.referencia_tipo];
 const m=REG&&REG.criterio?REG.criterio.min_pontos_serie:null;
 if(!r||m==null||r.n_pontos==null||r.n_pontos>=m)return '';
 return ' '+regBadge('referencia curta: '+r.n_pontos+' pts','#d29922',
  'a referencia tem menos pontos anteriores do que o minimo de serie ('+m+'); detetada, mas pouco robusta. '+
  'Nao e um criterio de exclusao');}
function regOrdemDia(o){
 const n=o.ordem_mesmo_dia&&o.ordem_mesmo_dia.ambiguos;
 return n>0?regNota(n+' esforco(s) do mesmo dia sem hora que prove a ordem ficaram fora da referencia'):'';}
function regReferencia(o){
 if(o.referencia_tipo==null)return '<b>sem referencia anterior</b>'+regNota('primeiro ponto da serie: nao ha com que o comparar; nao entra no estimador');
 const t=REG_REF[o.referencia_tipo]||o.referencia_tipo||'?';
 const outras=Object.keys(o.referencias||{}).filter(function(k){return k!==o.referencia_tipo;})
  .map(function(k){const r=o.referencias[k];
   return regEsc(REG_REF[k]||k)+' '+regPct(r.pct)+(r.qualifica?' (qualifica)':'')+', '+regEsc(r.n_pontos)+' pts';});
 return '<b>'+regEsc(t)+'</b>'+(o.referencia_tipo==='season'&&o.season?' ('+regEsc(o.season)+')':'')+
  ': '+regW(o.referencia_watts)+' W'+regCurta(o)+
  regNota('o esforco e '+regPct(o.pct_da_referencia)+' desta referencia'+
   ((o.referencias&&o.referencias[o.referencia_tipo]&&o.referencias[o.referencia_tipo].n_pontos!=null)?
    ' ('+regEsc(o.referencias[o.referencia_tipo].n_pontos)+' pts na referencia)':'')+
   (outras.length?'<br>outras: '+outras.join('; '):''))+regOrdemDia(o);}
function regBadgeDeteccao(o){
 const sr=o.estado_deteccao==='sem_referencia_anterior';
 return (sr?regBadge('sem referencia','#d29922','sem pontos anteriores para comparar; registada mas nao usada'):regBadge('detetada','#58a6ff','detetada automaticamente; nao precisa de confirmacao'))+
  regNota('v'+regEsc(o.versao)+(o.detetado_em?' &middot; '+regEsc(String(o.detetado_em).slice(0,10)):'')+
   (o.versoes_anteriores&&o.versoes_anteriores.length?' &middot; '+o.versoes_anteriores.length+' versao(oes) anterior(es)':''));}
function regIgnoreParts(o){return !!(o&&o.ignore_parts_presente);}
function regBadgeElegibilidade(o){
 const e=o.elegibilidade_cientifica;
 if(e==='elegivel')return regBadge('elegivel','#2ECC71','icu_ignore_power=false (explicito) e sem ignore_parts');
 if(e==='inelegivel')return regBadge('inelegivel','#E74C3C','icu_ignore_power=true: a Intervals.icu ignora a potencia desta atividade');
 if(regIgnoreParts(o))return regBadge('ignore_parts: detetado, nao elegivel','#E67E22',
  'ignore_parts presente: o segmento valido nao pode ser identificado; fica visivel mas fora do estimador e da validacao');
 return regBadge('elegibilidade desconhecida','#E67E22',
  'icu_ignore_power ausente ou atividade nao encontrada; nao e tratada como elegivel');}
function regMotivoTexto(m){
 if(!m)return '';
 if(m==='exclusao_manual')return 'exclusao manual';
 if(m==='sem_referencia_anterior')return 'sem referencia anterior (primeiro ponto da serie)';
 if(m==='inelegivel:icu_ignore_power=true')return 'icu_ignore_power=true (potencia ignorada)';
 if(m==='elegibilidade_desconhecida:ignore_parts_nao_associavel_ao_trecho')
  return 'ignore_parts presente: segmento valido nao identificado';
 if(m.indexOf('elegibilidade_desconhecida:')===0)return 'elegibilidade desconhecida ('+m.slice(27)+')';
 if(m==='dados_divergente')return 'a curva atual ja nao confere com o valor detetado';
 if(m==='dados_sem_curva')return 'curva removida ou sem esta duracao';
 return m;}
function regBadgeUso(o){
 if(o.incluida_no_estimador)
  return regBadge('entra no estimador e na validacao','#2ECC71','elegivel e sem exclusao; sem confirmacao individual');
 return regBadge('fora do estimador e da validacao','#E74C3C','excluida cientificamente ou manualmente')+
  regNota(regEsc(regMotivoTexto(o.motivo_exclusao)));}
function regExclusaoManual(o){
 const m=o.exclusao_manual; if(!m)return '<span style="color:#8b949e">&mdash;</span>';
 return regBadge('excluida manualmente','#8b949e','revisao '+m.revisao+' (rejeitada) no registo')+
  regNota('sobre '+regW(m.watts)+' W'+(m.valor_diferente?' &middot; o valor mudou desde a exclusao (a exclusao mantem-se)':'')+
   (m.motivo?'<br>'+regEsc(m.motivo):''));}
function regTags(o){
 const t=(o.tags&&o.tags.tempo_ou_distancia)||[]; if(!t.length)return '';
 return ' '+t.map(function(x){return regBadge('tag '+x.texto,'#8b949e',
  x.tipo==='tempo'?(o.tags.coincide_duracao?'tag de tempo que coincide com a duracao':'tag de tempo'):
  'tag de distancia: informativa, nao convertida em duracao');}).join(' ');}
function regLista(){
 const l=(REG&&REG.observacoes)||[];
 const f=REG_FILTRO;
 return l.map(function(o,i){return {o:o,i:i};}).filter(function(x){
  return (!f.mod||x.o.modalidade===f.mod)&&(!f.dur||String(x.o.duracao_s)===f.dur)&&
   (!f.ref||x.o.referencia_tipo===f.ref)&&
   (!f.inc||(f.inc==='sim')===!!x.o.incluida_no_estimador);
 }).sort(function(a,b){return a.o.data<b.o.data?1:(a.o.data>b.o.data?-1:0);});}
function regFormulario(o){
 const dec=REG_ABRIR.dec, rem=dec==='confirmar';
 return '<tr><td colspan="9" style="background:#0d1117;padding:10px 12px">'+
  '<div style="font-size:13px;margin-bottom:4px"><b>'+(rem?'Remover a exclusao manual de':'Excluir manualmente')+'</b> '+
  regEsc(o.modalidade)+' '+regEsc(o.duracao_s)+' s de '+regEsc(o.data)+' ('+regW(o.watts)+' W, atividade '+
  regEsc(o.activity_id)+')</div>'+
  '<div style="font-size:12px;color:#8b949e;margin-bottom:4px">'+(rem?
   'Isto so levanta a exclusao; a observacao continua sujeita a elegibilidade cientifica.':
   'Opcional: a observacao passa a ficar fora do estimador e da validacao. O historico fica guardado e '+
   'a exclusao pode ser levantada depois.')+'</div>'+
  '<textarea id="regMotivo" rows="2" maxlength="500" placeholder="Motivo (obrigatorio)" '+
  'style="width:100%;max-width:560px;background:#161b22;color:#c9d1d9;border:1px solid #30363d;'+
  'border-radius:6px;padding:6px"></textarea>'+
  '<div style="margin-top:6px"><button type="button" onclick="regEnviar()">Registar '+
  (rem?'remocao da exclusao':'exclusao')+'</button> '+
  '<button type="button" onclick="regCancelar()">Cancelar</button></div></td></tr>';}
function regAcoes(o,i){
 const b=function(rot,dec,tip){
  return '<button type="button" title="'+regEsc(tip||'')+'" onclick="regAbrir('+i+',\''+dec+'\')">'+rot+'</button> ';};
 if(o.exclusao_manual)
  return o.elegibilidade_cientifica==='elegivel'?b('Remover exclusao','confirmar','levanta a exclusao manual; fica no historico'):'';
 return b('Excluir (opcional)','rejeitar','exclusao manual opcional; a inclusao nunca exige confirmacao');}
function htmlRegisto(d){
 if(!d||d.status!=='OK'||!Array.isArray(d.observacoes))
  return '<p class="sub">Nao foi possivel carregar as observacoes.</p>';
 const mods=Array.from(new Set(d.observacoes.map(function(o){return o.modalidade;})));
 const sel=function(chave,vals){
  return '<select onchange="regFiltro(\''+chave+'\',this.value)">'+
   vals.map(function(v){return '<option value="'+regEsc(v[0])+'"'+(REG_FILTRO[chave]===v[0]?' selected':'')+
    '>'+regEsc(v[1])+'</option>';}).join('')+'</select> ';};
 const lista=regLista();
 const mostra=lista.slice(0,REG_MAX);
 const linhas=mostra.map(function(x){
  const o=x.o;
  return '<tr><td>'+regEsc(o.modalidade)+' '+regEsc(o.duracao_s)+' s'+regTags(o)+'</td>'+
   '<td>'+regEsc(o.data)+'<div style="font-size:11px;color:#8b949e">'+regEsc(o.activity_id)+'</div></td>'+
   '<td class="num">'+regW(o.watts)+' W</td>'+
   '<td>'+regReferencia(o)+'</td>'+
   '<td>'+regBadgeDeteccao(o)+'</td>'+
   '<td>'+regBadgeElegibilidade(o)+'</td>'+
   '<td>'+regBadgeUso(o)+'</td>'+
   '<td>'+regExclusaoManual(o)+'</td>'+
   '<td>'+regAcoes(o,x.i)+'</td></tr>'+
   ((REG_ABRIR&&REG_ABRIR.i===x.i)?regFormulario(o):'');
 }).join('');
 const c=d.criterio||{};
 const total=d.observacoes.length;
 const incl=d.observacoes.filter(function(o){return o.incluida_no_estimador;}).length;
 return '<p style="font-size:13px">Os esforcos perto de um maximo (melhor recente, season ou all-time) sao '+
  '<b>detetados e registados automaticamente</b> depois de cada sincronizacao de curvas. Os que forem '+
  'cientificamente elegiveis entram no estimador e na validacao <b>sem confirmacao individual</b>. '+
  'Detetar nao e validar o modelo, e o maximo de uma curva nao e um teste laboratorial ou padronizado.</p>'+
  '<ul style="font-size:12px;color:#8b949e;margin:4px 0 8px 18px">'+
  (Array.isArray(d.limitacoes)?d.limitacoes.map(function(t){return '<li>'+regEsc(t)+'</li>';}).join(''):'')+'</ul>'+
  '<div style="font-size:12px;color:#8b949e;margin:4px 0">Limiar de proximidade '+
  regEsc(c.limiar_proximidade!=null?c.limiar_proximidade:'?')+' &middot; janela recente '+
  regEsc(c.janela_recente_dias!=null?c.janela_recente_dias:'?')+' d &middot; historico sincronizado '+
  regEsc(c.anos_historico!=null?c.anos_historico:'?')+' anos &middot; fronteiras de season: '+
  regEsc(c.season_fonte||'?')+' &middot; ultima deteccao '+
  regEsc(d.ultima_atualizacao?String(d.ultima_atualizacao).slice(0,16).replace('T',' '):'nunca')+
  '</div>'+
  regTemporal(d.historico_temporal)+
  '<div style="font-size:12px;margin:4px 0">'+total+' observacoes detetadas, '+incl+
  ' entram no estimador e '+(total-incl)+' ficam de fora. '+
  '<button type="button" onclick="regAtualizar()" title="Corre a deteccao sobre as curvas ja sincronizadas; '+
  'nao sincroniza curvas">Atualizar deteccao</button></div>'+
  '<div style="margin:6px 0">'+
  sel('mod',[['','todas as modalidades']].concat(mods.map(function(m){return [m,m];})))+
  sel('dur',[['','todas as duracoes'],['60','60 s'],['300','300 s'],['1200','1200 s']])+
  sel('ref',[['','todas as referencias'],['recente','melhor recente'],['season','season'],['all_time','all-time']])+
  sel('inc',[['','estimador: todas'],['sim','entram no estimador'],['nao','ficam de fora']])+'</div>'+
  (REG_MSG?'<div style="font-size:13px;margin:6px 0;color:#58a6ff">'+regEsc(REG_MSG)+'</div>':'')+
  (mostra.length?'<div class="wrap"><table><thead><tr>'+
   ['Esforco','Atividade e data','Potencia observada','Referencia usada','Estado de deteccao',
    'Elegibilidade cientifica','Estimador e validacao','Exclusao manual','Acao (opcional)'].map(function(h){
    return '<th>'+h+'</th>';}).join('')+'</tr></thead><tbody>'+linhas+'</tbody></table></div>'+
   (lista.length>mostra.length?'<div class="sub">A mostrar '+mostra.length+' de '+lista.length+
    ' (mais recentes primeiro). Usa os filtros para ver os restantes.</div>':''):
   '<p class="sub">'+(total?'Sem observacoes com estes filtros.':
    'Ainda nao ha observacoes detetadas. A deteccao corre depois de cada sincronizacao de curvas; '+
    'tambem podes carregar em Atualizar deteccao.')+'</p>');}
function regRender(){
 const el=document.getElementById('registoTestesCorpo');
 if(el)el.innerHTML=htmlRegisto(REG);}
async function carregarRegisto(){
 try{ REG=await fetch('/api/protocolo/observacoes-auto').then(function(r){return r.json();}); }
 catch(e){ REG=null; }
 REG_CARREGADO=!!REG;
 regRender();}
function abrirRegisto(det){
 if(det&&det.open&&!REG_CARREGADO)carregarRegisto();}
function regFiltro(chave,v){REG_FILTRO[chave]=v;REG_ABRIR=null;regRender();}
function regAbrir(i,dec){REG_ABRIR={i:i,dec:dec};REG_MSG='';regRender();}
function regCancelar(){REG_ABRIR=null;regRender();}
async function regAtualizar(){
 let j;
 try{ j=await fetch('/api/protocolo/observacoes-auto/atualizar',{method:'POST'}).then(function(r){return r.json();}); }
 catch(e){ REG_MSG='Falha de rede: a deteccao nao foi corrida.'; regRender(); return; }
 REG_MSG=(j&&j.status==='OK')?
  ('Deteccao corrida: '+regEsc(j.novas_versoes)+' versao(oes) nova(s), '+regEsc(j.retiradas)+' retirada(s). '+
   'Isto nao valida o modelo.'):((j&&(j.mensagem||j.erro))||'Nao foi possivel correr a deteccao.');
 await carregarRegisto();}
async function regEnviar(){
 if(!REG_ABRIR||!REG||!REG.observacoes)return;
 const o=REG.observacoes[REG_ABRIR.i]; if(!o)return;
 const motivo=((document.getElementById('regMotivo')||{}).value||'').trim();
 if(!motivo){REG_MSG='Escreve o motivo antes de registar.';regRender();return;}
 let j;
 try{
  j=await fetch('/api/protocolo/testes/decisao',{method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({modalidade:o.modalidade,duracao_s:o.duracao_s,activity_id:o.activity_id,
    decisao:REG_ABRIR.dec,motivo:motivo,confirmacao_explicita:true,origem_registo:'ui',
    metodo_confirmacao:'manual',aceita_elegibilidade_desconhecida:false,
    watts_vistos:o.watts})
  }).then(function(r){return r.json();});
 }catch(e){ REG_MSG='Falha de rede: nada foi registado.'; regRender(); return; }
 REG_ABRIR=null;
 const desatualizado=!!(j&&j.status!=='OK'&&j.campo==='watts_vistos');
 REG_MSG=(j&&j.status==='OK')?
  ((j.alterado?'Registado. ':'Sem alteracao. ')+'A exclusao manual e opcional e nao valida o modelo.'):
  (desatualizado?'O valor mudou depois de abrires esta lista: nada foi registado. '+
   'A lista foi recarregada com o valor atual.':
   ((j&&j.mensagem)||'Nao foi possivel registar.'));
 await carregarRegisto();}
// <</registo-testes>>
// <<estimador-experimental>>
// ─── Estimador experimental (Fase 2 do modelo progressivo) ────────────────
// So le /api/modelo/estimador, e so quando o painel e aberto. Nao escreve nada,
// nao altera graficos nem indicadores do PMC e nao foi validado preditivamente.
let EST_CARREGADO=false;
function estEsc(s){
 return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function htmlEstimador(j){
 if(!j||j.status!=='OK')return '<p style="font-size:13px">Nao foi possivel calcular: '+
  estEsc(j&&j.mensagem?j.mensagem:'sem resposta')+'</p>';
 const ROT={nao_calibrado:'nao calibrado',estimativa_unica:'1 teste (sem incerteza)',experimental:'experimental'};
 const lin=(j.itens||[]).map(function(i){
  const inc=i.incerteza?('&plusmn;'+i.incerteza.sd_w.toFixed(1)+' W (IC95 '+
   i.incerteza.ic95_w[0].toFixed(0)+'&ndash;'+i.incerteza.ic95_w[1].toFixed(0)+')'):
   '<span title="'+estEsc(i.motivo_sem_incerteza)+'">n/d</span>';
  const mot=Object.keys(i.motivos_exclusao||{}).map(function(k){
   return estEsc(k)+' ('+i.motivos_exclusao[k]+')';}).join('; ');
  return '<tr><td>'+estEsc(i.modalidade)+'</td><td>P'+estEsc(i.duracao_s)+'</td>'+
   '<td>'+estEsc(ROT[i.estado_calibracao]||i.estado_calibracao)+'</td>'+
   '<td>'+(i.n_detetadas==null?'&mdash;':i.n_detetadas)+'</td><td>'+i.n_confirmados+'</td><td>'+i.n_usadas+'</td><td>'+i.n_excluidas+'</td>'+
   '<td>'+(i.nivel_w==null?'&mdash;':i.nivel_w.toFixed(1)+' W')+'</td><td>'+inc+'</td>'+
   '<td>'+estEsc(i.ultima_observacao||'')+'</td><td style="font-size:11px">'+mot+'</td></tr>';
 }).join('');
 return '<p style="font-size:13px"><b>Experimental e nao validado preditivamente.</b> '+
  'Usa as observacoes detetadas automaticamente (melhor recente, season ou all-time), sem '+
  'confirmacao individual, desde que tenham icu_ignore_power=false explicito e nao tenham '+
  'ignore_parts (elegibilidade desconhecida fica de fora). Confirmados e so a contagem das que '+
  'tambem tem confirmacao manual; uma exclusao manual retira a observacao. Nao altera o PMC nem os parametros do modelo. A incerteza e a do nivel '+
  'sob o modelo, nao uma previsao.</p>'+
  '<table style="font-size:12px;border-collapse:collapse"><thead><tr><th>Modalidade</th><th>Duracao</th>'+
  '<th>Estado</th><th>Detetadas</th><th>Confirmados</th><th>Usados</th><th>Excluidos</th><th>Estimativa</th>'+
  '<th>Incerteza</th><th>Ultimo teste usado</th><th>Motivos de exclusao</th></tr></thead><tbody>'+
  lin+'</tbody></table>';
}
async function carregarEstimador(){
 const el=document.getElementById('estimadorExpCorpo');
 if(!el)return;
 el.innerHTML='A carregar...';
 try{
  const r=await fetch('/api/modelo/estimador');
  el.innerHTML=htmlEstimador(await r.json());
  EST_CARREGADO=true;
 }catch(e){
  el.innerHTML='<p style="font-size:13px">Erro ao carregar o estimador.</p>';
 }
}
function abrirEstimador(d){
 if(d&&d.open&&!EST_CARREGADO)carregarEstimador();
}
// <</estimador-experimental>>
// <<validacao-exploratoria>>
// ─── Validacao preditiva exploratoria (Fase 3) ───────────────────────────
// So le /api/modelo/validacao, e so quando o painel e aberto. Nao escreve nada, nao altera
// graficos nem indicadores do PMC e nao muda o estimador. As sugestoes de esforcos sao
// oportunidades de recolha de dados, nunca uma condicao para a aplicacao funcionar.
let VAL_CARREGADO=false;
function htmlValidacao(j){
 if(!j||j.status!=='OK')return '<p style="font-size:13px">Nao foi possivel calcular: '+
  estEsc(j&&j.mensagem?j.mensagem:'sem resposta')+'</p>';
 const SIT={sem_alvos:'sem alvos (provisorio)',amostra_insuficiente:'amostra insuficiente (provisorio)',
  avaliavel_diferenca_nao_demonstrada:'avaliavel: sem diferenca demonstrada',
  avaliavel_m2_melhor:'avaliavel: estimador melhor que M0 e M1',
  avaliavel_m2_pior:'avaliavel: estimador pior que um baseline'};
 const f1=function(x){return x==null?'&mdash;':Number(x).toFixed(1);};
 const nz=function(x){return x==null?'&mdash;':estEsc(x);};
 const retro=((j.modos||{}).retrospectivo||{}).itens||[];
 const epoca={};((((j.modos||{}).conhecido_na_epoca)||{}).itens||[]).forEach(function(i){
  epoca[i.modalidade+'|'+i.duracao_s]=i;});
 const lin=retro.map(function(i){
  const o=i.orientacao_dados||{},c=i.criterios||{},M=i.metodos||{};
  const m=function(k){return (M[k]||{});};
  const e=epoca[i.modalidade+'|'+i.duracao_s];
  const ht=e&&e.historico_temporal;
  const ep=e?(nz(e.n_alvos)+' alvos'+(ht&&ht.estado==='sem_historico_temporal'?' (sem historico temporal ainda)':'')):'&mdash;';
  const fa=o.faltam;
  let falta='';
  if(fa){
   const partes=[];
   if(o.observacoes_a_acrescentar>0)partes.push('&asymp;'+o.observacoes_a_acrescentar+' esforcos novos em datas distintas');
   if(fa.dias_cobertura>0)partes.push('cobertura temporal: faltam '+fa.dias_cobertura+' dias');
   falta=partes.length?partes.join('; '):'criterios de amostra cumpridos';
  }
  const tu=o.teste_util;
  const util=tu?('<br><span style="color:#8b949e">Util: <b>'+estEsc(tu.nome)+'</b> ('+estEsc(tu.mede)+'). '+estEsc(tu.condicao)+'</span>')
   :(o.motivo_sem_sugestao==='amostra_suficiente_para_avaliar'?'<br><span style="color:#8b949e">Amostra suficiente para avaliar: nao ha teste a sugerir so para aumentar o numero.</span>'
   :(o.motivo_sem_sugestao?'<br><span style="color:#8b949e">Sem sugestao especifica: '+estEsc(o.motivo_sem_sugestao)+'.</span>':''));
  const m1=i.n_origens_m1_ordem_ambigua>0?('<br><span style="color:#8b949e">M1: em '+estEsc(i.n_origens_m1_ordem_ambigua)+
   ' origem(ns) o ultimo dia tinha varias observacoes sem hora real que prove a ordem; M1 usou a media desse dia '+
   '(fallback metodologico, nao o ultimo valor literal).</span>'):'';
  const elg=o.excluidas_por_elegibilidade>0?('<br><span style="color:#8b949e">'+o.excluidas_por_elegibilidade+
   ' esforco(s) detetado(s) ficam fora por elegibilidade (ex.: ignore_parts); a regra nao foi alterada.</span>'):'';
  return '<tr><td>'+estEsc(i.modalidade)+'</td><td>P'+estEsc(i.duracao_s)+'</td>'+
   '<td>'+estEsc(SIT[o.situacao]||o.situacao||'')+'</td>'+
   '<td>'+nz(i.n_alvos)+'/'+nz(c.min_alvos)+' alvos<br>'+nz(i.n_datas_distintas)+'/'+nz(c.min_datas_distintas)+' datas<br>'+
    nz(i.dias_cobertura)+'/'+nz(c.min_dias_cobertura)+' dias</td>'+
   '<td>'+f1(m('M0').mae)+' / '+f1(m('M1').mae)+' / '+f1(m('M2').mae)+'</td>'+
   '<td>'+f1(m('M0').erro_medio)+' / '+f1(m('M1').erro_medio)+' / '+f1(m('M2').erro_medio)+'</td>'+
   '<td>'+ep+'</td>'+
   '<td style="font-size:11px">'+estEsc(o.resumo||'')+(falta?('<br><b>'+falta+'</b>'):'')+util+elg+m1+'</td></tr>';
 }).join('');
 return '<p style="font-size:13px;color:#E67E22"><b>Nao valida o IR baseado em MMP3.</b> Este painel usa as observacoes '+
  'automaticas de 60/300/1200 s do estimador anterior; o IR atual (MMP3 de 180 s, P0 = MMP3 do ultimo YES) ainda nao tem validacao preditiva. Este painel e LEGADO: nao alimenta nem valida o IR atual.</p>'+
  '<p style="font-size:13px"><b>Exploratoria; o estimador continua nao validado.</b> Compara, por modalidade e '+
  'duracao, a previsao do estimador (M2) com a media historica (M0) e o ultimo valor (M1), nos mesmos alvos e '+
  'usando so dados anteriores a cada alvo. O alvo e o <b>melhor valor da duracao em toda a atividade</b>, nao um '+
  'teste padronizado, e so inclui esforcos detetados (perto do melhor anterior): mede-se a previsao do proximo '+
  'esforco detetado, nao a capacidade. <b>Detetar</b>, ser <b>elegivel</b> e <b>validar</b> sao coisas diferentes: '+
  'ter muitas observacoes nao prova capacidade preditiva. A coluna principal e o modo retrospetivo '+
  '(exploratorio: usa observacoes detetadas depois do alvo); &ldquo;conhecido na epoca&rdquo; so usa o que ja '+
  'estava detetado e acumula dados a partir da ativacao.</p>'+
  '<p style="font-size:12px;color:#8b949e">MAE e vies em W (previsao &minus; observado; negativo = subestima), '+
  'M0 / M1 / M2. M1 e o ultimo valor; se o ultimo dia tiver varias observacoes sem ordem provada pela hora real, usa a media desse dia (fallback). Resultados de uma unidade nao se generalizam as outras. Os esforcos sugeridos sao oportunidades '+
  'de recolha de dados, nao obrigacoes; os &ldquo;Testes sugeridos&rdquo; acima respondem a outra pergunta (testes em atraso).</p>'+
  '<table style="font-size:12px;border-collapse:collapse"><thead><tr><th>Modalidade</th><th>Duracao</th>'+
  '<th>Situacao</th><th>Amostra</th><th>MAE M0 / M1 / M2</th><th>Vies M0 / M1 / M2</th>'+
  '<th>Na epoca</th><th>Leitura e dados em falta</th></tr></thead><tbody>'+lin+'</tbody></table>';
}
async function carregarValidacao(){
 const el=document.getElementById('validacaoExpCorpo');
 if(!el)return;
 el.innerHTML='A carregar...';
 try{
  const r=await fetch('/api/modelo/validacao');
  const j=await r.json();
  el.innerHTML=htmlValidacao(j);
  VAL_CARREGADO=!!(j&&j.status==='OK');      // uma resposta de erro permite nova tentativa
 }catch(e){
  el.innerHTML='<p style="font-size:13px">Erro ao carregar a validacao.</p>';
 }
}
function abrirValidacao(d){
 if(d&&d.open&&!VAL_CARREGADO)carregarValidacao();
}
// <</validacao-exploratoria>>
// <<distribuicao-carga>>
// ─── Distribuicao OBSERVADA da carga entre Bike/Row/Run/Ski (janela movel), por Training Load ou TRIMP ─────
// So le GET /api/carga/distribuicao, e so quando o painel e aberto ou um seletor muda. Nao escreve, nao
// recalcula nada, nao altera PMC/CTL/ATL/TSB, IR nem MMP. A musculacao e excluida no servidor antes de
// qualquer soma: o payload so tem as quatro modalidades. Nao e influencia fisiologica nem equilibrio ideal.
const DC_MODS=['Bike','Row','Run','Ski'];
const DC_JANELAS=[7,28,42,90], DC_HORIZ=[[90,'90 dias'],[180,'180 dias'],[365,'1 ano'],[730,'2 anos']];
const DC_METODOS=[['training_load','Training Load'],['trimp','TRIMP']];
const DC_COR_ESTADO={sem_treino:'#484f58',carga_ausente:'#d29922'};
const DC_MOTIVOS=[['sem_valor','sem valor'],['valor_invalido','valor invalido'],['fc_ignorada','FC marcada para ignorar']];
let DC_MONTADO=false, DC_J=null, DC_SEQ=0;
function dcEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function dcNum(x,d){return (x==null||!isFinite(x))?'&mdash;':Number(x).toFixed(d==null?1:d);}
function dcSinal(x,d){return (x==null||!isFinite(x))?'&mdash;':((x>0?'+':'')+Number(x).toFixed(d==null?1:d));}
function dcRotAus(j){return (j&&j.metodo==='trimp')?'sem TRIMP valido':'carga em falta';}
function dcShell(){
 const el=document.getElementById('distCargaExpCorpo'); if(!el)return;
 el.innerHTML=
  '<p style="font-size:13px"><b>Distribuicao OBSERVADA da carga registada.</b> Cada barra mostra, para o dia, a parte da carga '+
  'da janela atribuida a cada modalidade, pelo metodo escolhido. Nao mede influencia fisiologica, nao define equilibrio ideal e nao prova '+
  'efeito no desempenho. Musculacao e restantes tipos nao entram em nenhum calculo deste painel.</p>'+
  '<div class="controls">'+
  '<label class="sel">Metodo <select id="dcMet">'+DC_METODOS.map(function(m){return '<option value="'+m[0]+'"'+(m[0]==='training_load'?' selected':'')+'>'+m[1]+'</option>';}).join('')+'</select></label>'+
  '<label class="sel">Janela <select id="dcJan">'+DC_JANELAS.map(function(w){return '<option value="'+w+'"'+(w===28?' selected':'')+'>'+w+' dias</option>';}).join('')+'</select></label>'+
  '<label class="sel">Periodo <select id="dcHoriz">'+DC_HORIZ.map(function(h){return '<option value="'+h[0]+'"'+(h[0]===365?' selected':'')+'>'+h[1]+'</option>';}).join('')+'</select></label></div>'+
  '<div id="dcInfo"></div>'+
  '<div class="chartbox"><div class="legend" id="lgDc"></div><canvas id="chDc" height="260"></canvas></div>'+
  '<div id="dcNotas"></div>'+
  '<h4 style="margin:16px 0 4px">Comparacao entre metodos (mesmas janelas e datas)</h4>'+
  '<div id="dcComp"></div>'+
  '<div class="chartbox"><div class="legend" id="lgDcDif"></div><canvas id="chDcDif" height="220"></canvas></div>';
 ['dcMet','dcJan','dcHoriz'].forEach(function(id){const e=document.getElementById(id); if(e)e.onchange=function(){carregarDistCarga();};});
 DC_MONTADO=true;
}
// puro: transforma o payload nas colunas a desenhar (os testes inspecionam-no)
function dcSeries(j){
 if(!j||j.status!=='OK'||j.estado!=='ok'||!j.serie)return null;
 const S=j.serie, mods=(j.modalidades||DC_MODS).filter(function(m){return DC_MODS.indexOf(m)>=0;});
 return {datas:S.datas, mods:mods, estado:S.estado, pct:S.pct, carga:S.carga, total:S.total,
  aus:S.n_ausentes_janela, eleg:S.n_elegiveis_janela, val:S.n_validas_janela, dif:S.dif_max_pp_entre_metodos, cores:j.cores||{}};
}
function dcMediana(v){const a=(v||[]).filter(function(x){return x!=null&&isFinite(x);}).sort(function(x,y){return x-y;});
 return a.length?(a.length%2?a[(a.length-1)/2]:(a[a.length/2-1]+a[a.length/2])/2):null;}
function dcMotivosTxt(sd){
 const p=DC_MOTIVOS.filter(function(m){return sd&&sd[m[0]]>0;}).map(function(m){return m[1]+': '+sd[m[0]];});
 return p.length?p.join('; '):'nenhuma';
}
function dcCoberturaHtml(j,S,k){
 const c=j.cobertura; if(!c||!c.total)return '';
 const rot=j.rotulo_metodo||'', t=c.total, inc=t.validas<t.elegiveis;
 let h='<p style="font-size:12px;color:#8b949e">Cobertura na janela: <b>'+dcEsc(t.elegiveis)+'</b> atividades elegiveis (Bike, Row, Run, Ski), '+
  '<b>'+dcEsc(t.validas)+'</b> com '+dcEsc(rot)+' valido, <b>'+dcEsc(t.elegiveis-t.validas)+'</b> excluidas por falta de dados ('+dcEsc(dcMotivosTxt(t.sem_dados))+').</p>';
 if(inc)h+='<div style="border-left:3px solid #d29922;padding:4px 10px;margin:6px 0;font-size:13px"><b>Cobertura incompleta:</b> a distribuicao por '+dcEsc(rot)+
  ' representa apenas as '+dcEsc(t.validas)+' de '+dcEsc(t.elegiveis)+' atividades com '+dcEsc(rot)+' valido. Nao e a distribuicao completa da carga real.</div>';
 return h;
}
function dcInfoHtml(j){
 if(!j||j.status!=='OK')return '<p style="font-size:13px">Nao foi possivel calcular: '+dcEsc(j&&j.mensagem?j.mensagem:'sem resposta')+'</p>';
 if(j.estado!=='ok')return '<p style="font-size:13px">'+dcEsc(j.leitura||'sem dados')+'</p>';
 const S=dcSeries(j), n=S.datas.length, k=n-1, ok=S.estado[k]==='ok'||S.estado[k]==='ok_com_ausentes', cob=j.cobertura||{};
 let t='<p style="font-size:13px">Metodo <b>'+dcEsc(j.rotulo_metodo)+'</b>. Janela de <b>'+dcEsc(j.janela_dias)+' dias</b> que termina em <b>'+dcEsc(S.datas[k])+
  '</b> e inclui esse dia. Unidade: '+dcEsc(j.unidade)+'.</p>';
 if(!ok){
  t+='<div style="border-left:3px solid #d29922;padding:4px 10px;margin:6px 0;font-size:13px">Sem percentagens para este dia: '+
   (S.estado[k]==='carga_ausente'?'houve atividades sem '+dcEsc(j.rotulo_metodo)+' valido e nenhum valor registado na janela':'nao houve treino de Bike, Row, Run ou Ski na janela')+'.</div>';
 } else {
  t+='<table style="font-size:12px;border-collapse:collapse"><thead><tr><th style="text-align:left">Modalidade</th><th>% da janela</th><th>'+dcEsc(j.rotulo_metodo)+' na janela</th><th>Atividades validas/elegiveis</th></tr></thead><tbody>'+
   S.mods.map(function(m){const c=cob[m]||{};return '<tr><td><i style="display:inline-block;width:10px;height:10px;background:'+dcEsc(S.cores[m]||'#888')+';margin-right:6px"></i>'+dcEsc(m)+'</td>'+
    '<td style="text-align:right">'+dcNum(S.pct[m][k],1)+'%</td><td style="text-align:right">'+dcNum(S.carga[m][k],0)+'</td>'+
    '<td style="text-align:right">'+(c.elegiveis==null?'&mdash;':dcEsc(c.validas)+'/'+dcEsc(c.elegiveis))+'</td></tr>';}).join('')+
   '<tr><td><b>Total (denominador)</b></td><td style="text-align:right"><b>100%</b></td><td style="text-align:right"><b>'+dcNum(S.total[k],0)+'</b></td>'+
   '<td style="text-align:right">'+(cob.total?dcEsc(cob.total.validas)+'/'+dcEsc(cob.total.elegiveis):'&mdash;')+'</td></tr></tbody></table>';
  if(S.estado[k]==='ok_com_ausentes')t+='<p style="font-size:12px;color:#d29922">'+dcEsc(S.aus[k])+' atividade(s) desta janela nao tem '+dcEsc(j.rotulo_metodo)+' valido e ficaram fora da soma (nao foram contadas como zero nem substituidas pelo outro metodo).</p>';
 }
 return t+dcCoberturaHtml(j,S,k);
}
function dcNotasHtml(j){
 if(!j||j.estado!=='ok')return '';
 const f=j.fonte_da_carga_pct||{}, pt=(j.parametros_trimp||{}).fc_max_usada_por_modalidade||{};
 let extra='';
 if(j.metodo==='trimp'){
  extra='<table style="font-size:12px;border-collapse:collapse"><thead><tr><th style="text-align:left">FC max usada no TRIMP (n atividades)</th><th>Valores</th></tr></thead><tbody>'+
   DC_MODS.map(function(m){const q=pt[m]||{},ks=Object.keys(q);return '<tr><td>'+dcEsc(m)+'</td><td style="text-align:right">'+
    (ks.length?ks.map(function(x){return dcEsc(x)+' bpm ('+dcEsc(q[x])+')';}).join(', '):'&mdash;')+'</td></tr>';}).join('')+'</tbody></table>'+
   '<p style="font-size:12px;color:#8b949e">'+dcEsc((j.parametros_trimp||{}).nota||'')+'</p>';
 } else {
  extra='<table style="font-size:12px;border-collapse:collapse"><thead><tr><th style="text-align:left">Fonte da carga no periodo</th><th>Potencia</th><th>FC</th><th>Ritmo</th><th>Outra</th></tr></thead><tbody>'+
   DC_MODS.map(function(m){const q=f[m]||{};return '<tr><td>'+dcEsc(m)+'</td>'+['power_load','hr_load','pace_load','outra'].map(function(c){
    return '<td style="text-align:right">'+(q[c]==null?'&mdash;':dcNum(q[c],0)+'%')+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table>';
 }
 return '<p style="font-size:12px;color:#8b949e">'+dcEsc(j.formula)+'. '+dcEsc(j.semantica_janela)+'. '+dcEsc(j.exclusoes)+'</p>'+
  '<p style="font-size:12px;color:#8b949e"><b>Definicao do metodo.</b> '+dcEsc(j.definicao_metodo)+' Nao foi aplicado nenhum fator de conversao.</p>'+extra+
  '<p style="font-size:12px;color:#8b949e">'+dcEsc(j.aviso)+'</p>';
}
function dcCompHtml(j){
 if(!j||j.estado!=='ok'||!j.comparacao)return '';
 const c=j.comparacao, n=c.datas.length, k=n-1, cu=c.controlo_mesmas_atividades||{};
 const tl=c.pct.training_load, tr=c.pct.trimp, ctl=cu.dif_pp||{};
 const ok=c.dif_pp[DC_MODS[0]][k]!=null;
 let h='<p style="font-size:13px">Dia de referencia <b>'+dcEsc(c.datas[k])+'</b>, janela de <b>'+dcEsc(j.janela_dias)+' dias</b> nos dois metodos. Diferenca = TRIMP menos Training Load, em pontos percentuais.</p>';
 h+='<table style="font-size:12px;border-collapse:collapse"><thead><tr><th style="text-align:left">Modalidade</th><th>% Training Load</th><th>% TRIMP</th><th>Diferenca (pp)</th><th>Dif. mesmas atividades (pp)</th>'+
  '<th>Training Load (unid. icu)</th><th>TRIMP (unid. TRIMP)</th></tr></thead><tbody>'+
  DC_MODS.map(function(m){return '<tr><td><i style="display:inline-block;width:10px;height:10px;background:'+dcEsc((j.cores||{})[m]||'#888')+';margin-right:6px"></i>'+dcEsc(m)+'</td>'+
   '<td style="text-align:right">'+dcNum(tl[m][k],1)+'%</td><td style="text-align:right">'+dcNum(tr[m][k],1)+'%</td>'+
   '<td style="text-align:right">'+dcSinal(c.dif_pp[m][k],1)+'</td><td style="text-align:right">'+dcSinal((ctl[m]||[])[k],1)+'</td>'+
   '<td style="text-align:right">'+dcNum(c.carga_absoluta.training_load[m][k],0)+'</td><td style="text-align:right">'+dcNum(c.carga_absoluta.trimp[m][k],0)+'</td></tr>';}).join('')+
  '<tr><td><b>Total</b></td><td style="text-align:right">'+(tl[DC_MODS[0]][k]==null?'&mdash;':'100%')+'</td><td style="text-align:right">'+(tr[DC_MODS[0]][k]==null?'&mdash;':'100%')+'</td>'+
  '<td style="text-align:right">&mdash;</td><td style="text-align:right">&mdash;</td>'+
  '<td style="text-align:right"><b>'+dcNum(c.total_absoluto.training_load[k],0)+'</b></td><td style="text-align:right"><b>'+dcNum(c.total_absoluto.trimp[k],0)+'</b></td></tr></tbody></table>';
 const ct=c.cobertura_ultima_janela||{}, a=(ct.training_load||{}).total||{}, b=(ct.trimp||{}).total||{};
 h+='<p style="font-size:12px;color:#8b949e">Cobertura na janela: Training Load '+dcEsc(a.validas)+'/'+dcEsc(a.elegiveis)+' atividades validas; TRIMP '+dcEsc(b.validas)+'/'+dcEsc(b.elegiveis)+
  ' (excluidas por falta de dados: '+dcEsc(dcMotivosTxt(b.sem_dados))+'); com os dois valores validos: '+dcEsc(c.n_validas_ambos_janela[k])+'.</p>';
 if(!ok)h+='<div style="border-left:3px solid #d29922;padding:4px 10px;margin:6px 0;font-size:13px">Sem diferenca neste dia: um dos metodos nao tem denominador valido e positivo.</div>';
 const med=dcMediana(c.dif_max_pp), mx=(c.dif_max_pp||[]).reduce(function(m,x){return x!=null&&x>m?x:m;},0), medc=dcMediana(cu.dif_max_pp);
 h+='<p style="font-size:12px;color:#8b949e">Diferenca maxima entre modalidades: '+dcNum(c.dif_max_pp[k],1)+' pp no dia de referencia (mediana '+dcNum(med,1)+', maximo '+dcNum(mx,1)+
  ' no periodo); so com as atividades que tem os dois valores validos: mediana '+dcNum(medc,1)+' pp. Uma diferenca grande indica que a distribuicao depende da metrica escolhida; uma diferenca pequena nao prova que as metricas sejam equivalentes. '+
  dcEsc(c.aviso)+'</p>';
 return h;
}
function dcDesenhar(j){
 const lg=document.getElementById('lgDc');
 const S=dcSeries(j);
 if(lg)lg.innerHTML=(S?S.mods:DC_MODS).map(function(m){return '<span><i style="background:'+dcEsc((S&&S.cores[m])||'#888')+'"></i>'+dcEsc(m)+'</span>';}).join('')+
  '<span><i style="background:'+DC_COR_ESTADO.sem_treino+'"></i>sem treino na janela</span>'+
  '<span><i style="background:'+DC_COR_ESTADO.carga_ausente+'"></i>'+dcEsc(dcRotAus(j))+'</span>';
 const o=ctx('chDc',260); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 if(!S){noData(g,W,H,(j&&(j.leitura||j.mensagem))||'Sem dados para esta selecao');return;}
 const n=S.datas.length, PL=40,PR=10,PT=10,PB=34,w=W-PL-PR,h=H-PT-PB, bw=Math.max(1,w/n);
 const X=function(i){return PL+w*i/n;};
 g.font='10px sans-serif';g.fillStyle='#8b949e';g.strokeStyle='#21262d';g.lineWidth=1;g.textAlign='right';
 for(let k=0;k<=4;k++){const v=25*k,y=PT+h-h*v/100;g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();g.fillText(v+'%',PL-5,y+3);}
 for(let i=0;i<n;i++){
  const x=X(i), st=S.estado[i];
  if(st==='ok'||st==='ok_com_ausentes'){
   let acc=0;
   S.mods.forEach(function(m){const p=S.pct[m][i]; if(p==null)return;
    g.fillStyle=S.cores[m]||'#888'; const y1=PT+h-h*(acc+p)/100, y0=PT+h-h*acc/100;
    g.fillRect(x,y1,Math.ceil(bw),Math.max(0,y0-y1)); acc+=p;});
   if(st==='ok_com_ausentes'){g.fillStyle=DC_COR_ESTADO.carga_ausente;g.fillRect(x,PT+h+3,Math.ceil(bw),4);}
  } else {
   g.fillStyle=DC_COR_ESTADO[st]||'#484f58'; g.globalAlpha=0.5; g.fillRect(x,PT,Math.ceil(bw),h); g.globalAlpha=1;
  }
 }
 g.fillStyle='#8b949e';
 for(let k=0;k<=4;k++){const i=Math.min(n-1,Math.round((n-1)*k/4));g.textAlign=k===0?'left':(k===4?'right':'center');g.fillText(S.datas[i].slice(0,7),PL+w*(i+0.5)/n,H-8);}
 registarTip('chDc',function(x){
  if(x<PL||x>PL+w)return '';
  const i=Math.max(0,Math.min(n-1,Math.floor((x-PL)/w*n)));
  const st=S.estado[i];
  let hh='<div class="th">'+dcEsc(S.datas[i])+' &middot; '+dcEsc(j.rotulo_metodo)+' &middot; janela '+dcEsc(j.janela_dias)+' dias</div>';
  if(st==='sem_treino'||st==='carga_ausente'){
   return hh+linhaTip(DC_COR_ESTADO[st],st==='sem_treino'?'Sem treino na janela':dcRotAus(j)[0].toUpperCase()+dcRotAus(j).slice(1)+' (sem percentagens)',S.aus[i]?S.aus[i]+' atividade(s) sem valor valido':'');}
  S.mods.forEach(function(m){hh+=linhaTip(S.cores[m]||'#888',dcEsc(m),dcNum(S.pct[m][i],1)+'% &middot; '+dcNum(S.carga[m][i],0));});
  hh+=linhaTip('#8b949e','Total da janela',dcNum(S.total[i],0));
  hh+=linhaTip('#8b949e','Atividades validas/elegiveis',S.val[i]+'/'+S.eleg[i]);
  if(S.aus[i])hh+=linhaTip(DC_COR_ESTADO.carga_ausente,'Atividades sem valor valido (fora da soma)',String(S.aus[i]));
  return hh;});
}
// diferenca em pontos percentuais entre metodos, por modalidade (TRIMP menos Training Load)
function dcDesenharDif(j){
 const lg=document.getElementById('lgDcDif');
 const c=(j&&j.estado==='ok')?j.comparacao:null;
 if(lg)lg.innerHTML=DC_MODS.map(function(m){return '<span><i style="background:'+dcEsc(((j&&j.cores)||{})[m]||'#888')+'"></i>'+dcEsc(m)+'</span>';}).join('')+'<span>diferenca = TRIMP &minus; Training Load (pp)</span>';
 const o=ctx('chDcDif',220); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 if(!c){noData(g,W,H,(j&&(j.leitura||j.mensagem))||'Sem dados para esta selecao');return;}
 const n=c.datas.length, PL=40,PR=10,PT=10,PB=26,w=W-PL-PR,h=H-PT-PB;
 let mx=1; DC_MODS.forEach(function(m){c.dif_pp[m].forEach(function(v){if(v!=null&&Math.abs(v)>mx)mx=Math.abs(v);});});
 mx=Math.ceil(mx/5)*5;
 const X=function(i){return PL+w*(n>1?i/(n-1):0.5);}, Y=function(v){return PT+h/2-(v/mx)*(h/2);};
 g.font='10px sans-serif';g.strokeStyle='#21262d';g.lineWidth=1;g.fillStyle='#8b949e';g.textAlign='right';
 [-mx,-mx/2,0,mx/2,mx].forEach(function(v){const y=Y(v);g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();g.fillText(dcSinal(v,0),PL-5,y+3);});
 g.strokeStyle='#484f58';g.beginPath();g.moveTo(PL,Y(0));g.lineTo(PL+w,Y(0));g.stroke();
 g.fillStyle='#8b949e';
 for(let k=0;k<=4;k++){const i=Math.round((n-1)*k/4);g.textAlign=k===0?'left':(k===4?'right':'center');g.fillText(c.datas[i].slice(0,7),X(i),H-8);}
 DC_MODS.forEach(function(m){
  g.strokeStyle=(j.cores||{})[m]||'#888';g.lineWidth=1.6;g.beginPath();let ini=false;
  c.dif_pp[m].forEach(function(v,i){if(v==null){ini=false;return;}const x=X(i),y=Y(v);if(!ini){g.moveTo(x,y);ini=true;}else g.lineTo(x,y);});
  g.stroke();});
 registarTip('chDcDif',function(x){
  if(x<PL||x>PL+w)return '';
  const i=Math.max(0,Math.min(n-1,Math.round((x-PL)/w*(n-1))));
  let hh='<div class="th">'+dcEsc(c.datas[i])+' &middot; janela '+dcEsc(j.janela_dias)+' dias</div>';
  if(c.dif_pp[DC_MODS[0]][i]==null)return hh+linhaTip('#d29922','Sem diferenca','um dos metodos sem denominador valido');
  DC_MODS.forEach(function(m){hh+=linhaTip((j.cores||{})[m]||'#888',dcEsc(m),dcSinal(c.dif_pp[m][i],1)+' pp &middot; TL '+dcNum(c.pct.training_load[m][i],1)+'% / TRIMP '+dcNum(c.pct.trimp[m][i],1)+'%');});
  hh+=linhaTip('#8b949e','Validas TL / TRIMP / ambos',c.n_validas_janela.training_load[i]+' / '+c.n_validas_janela.trimp[i]+' / '+c.n_validas_ambos_janela[i]);
  return hh;});
}
// tabelas largas (comparacao com 7 colunas) rolam na horizontal em vez de alargar a pagina em ecras estreitos
function dcRolavel(h){return String(h).replace(/<table/g,'<div style="overflow-x:auto"><table').replace(/<\/table>/g,'</table></div>');}
function dcRedesenhar(){
 const j=DC_J, info=document.getElementById('dcInfo'), nt=document.getElementById('dcNotas'), cp=document.getElementById('dcComp');
 const tp=document.getElementById('__tip'); if(tp&&tp.style)tp.style.display='none';   // nao deixar uma tooltip da selecao anterior
 if(info)info.innerHTML=dcRolavel(dcInfoHtml(j)); if(nt)nt.innerHTML=dcRolavel(dcNotasHtml(j)); if(cp)cp.innerHTML=dcRolavel(dcCompHtml(j));
 dcDesenhar(j); dcDesenharDif(j);
}
async function carregarDistCarga(){
 if(!DC_MONTADO)dcShell();
 const met=document.getElementById('dcMet').value, jan=document.getElementById('dcJan').value, hz=document.getElementById('dcHoriz').value;
 const seq=++DC_SEQ;
 let j=null;
 try{ j=await fetch('/api/carga/distribuicao?metodo='+encodeURIComponent(met)+'&janela='+encodeURIComponent(jan)+'&dias='+encodeURIComponent(hz)).then(function(r){return r.json();}); }
 catch(e){ j=null; }
 if(seq!==DC_SEQ)return;                       // resposta antiga de uma selecao anterior
 DC_J=j; dcRedesenhar();
}
function abrirDistCarga(d){
 if(d&&d.open&&!DC_MONTADO)carregarDistCarga();
}
// <</distribuicao-carga>>

// <<modelo-ir>>
// ─── Modelo IR: curvas de influencia de TODAS as modalidades num so grafico ─────────────
// So le /api/modelo/ir/conjunto (ULTIMO ajuste guardado de cada modalidade), uma vez ao abrir o painel e
// quando a duracao muda. Os botoes da legenda so mostram/escondem series ja carregadas: nao recalculam,
// nao escrevem, nao recalibram e nao alteram PMC/CTL/ATL/TSB/FTLM/MMP.
// Escala comum = % do P0 do PROPRIO ajuste (apenas apresentacao). Descreve um ajuste dentro da amostra,
// nao uma previsao validada. 'Overall' so existe quando o servidor o declarar disponivel.
// um ajuste por modalidade, sobre o MMP3 (180 s); o seletor de duracao foi removido
const IR_COMPS=[{k:'liquida',nome:'Líquida (positiva − negativa)',curto:'líquida'},
 {k:'positiva',nome:'Positiva K1·E',curto:'positiva'},{k:'negativa',nome:'Negativa −K2·G',curto:'negativa'}];
// por omissao so a linha liquida (espessa); positiva e negativa ligam-se na legenda (12 linhas diarias sao ilegiveis)
const IR_OFF={mod:{},comp:{positiva:true,negativa:true}};
let IR_MONTADO=false, IR_J=null, IR_SEQ=0;
function irEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function irNum(x,d){return (x==null||!isFinite(x))?'&mdash;':Number(x).toFixed(d==null?2:d);}
function irShell(){
 const el=document.getElementById('irExpCorpo'); if(!el)return;
 el.innerHTML=
  '<p style="font-size:13px"><b>Experimental, ajuste historico (dentro da amostra), nao validado preditivamente.</b> '+
  'Cada modalidade usa o seu ultimo ajuste guardado: resposta completa = P0 + positiva (K1&middot;E) &minus; negativa (K2&middot;G). '+
  'Um ajuste por modalidade, sobre o MMP3 (180 s). O P0 é global: o MMP3 do último registo YES (o mesmo valor nas quatro modalidades; não vem do CP Model); só K1, K2, T1 e T2 são estimados. '+
  'Nao altera o PMC e nada e recalibrado ao abrir o painel.</p>'+
  '<div id="irInfo"></div><div id="irMmp3"></div>'+
  '<div class="chartbox"><div class="legend" id="lgIrConj"></div><canvas id="chIrConj" height="300"></canvas></div>'+
  '<div id="irEscala" style="font-size:12px;color:#8b949e"></div>'+
  '<div id="irAvisos"></div><div id="irTabela"></div><div id="irDiag"></div><div id="irOverall"></div>';
 IR_MONTADO=true;
}
function irEstado(e){return {valido:['válido','#3fb950'],desatualizado:['DESATUALIZADO','#f85149'],erro_solver:['erro do Solver','#f85149'],
 sem_referencia_mmp3:['sem referência MMP3','#d29922'],identificavel:['identificável','#3fb950'],nao_identificavel:['NÃO identificável','#d29922'],
 sem_ajuste:['sem ajuste','#8b949e'],dados_insuficientes:['dados insuficientes','#8b949e'],sem_convergencia:['não convergiu','#8b949e'],
 sem_atividades:['sem atividades','#8b949e'],sem_calibracao_valida:['sem calibração válida','#8b949e']}[e]||[e||'—','#8b949e'];}
// estilo por componente; ajuste NAO identificavel => traco distinto (mais aberto) alem do aviso
function irEstilo(comp,nid){
 if(comp==='liquida')return {lw:2.6,dash:nid?[9,4]:[]};
 if(comp==='positiva')return {lw:1.2,dash:nid?[9,3,2,3]:[6,3]};
 return {lw:1.3,dash:nid?[1,5]:[2,3]};
}
// series a desenhar (puro: so transforma o payload; os testes inspecionam-no). Valores em % de P0, watts guardados.
function irSeries(j){
 if(!j||j.status!=='OK'||!j.modalidades)return [];
 const out=[];
 (j.modalidades_ordem||[]).forEach(function(m){
  const it=j.modalidades[m]; if(!it||!it.tem_curva||!it.pct)return;
  const nid=!it.parametros_identificados, fi=it.fora_do_ajuste_desde?j.datas.indexOf(it.fora_do_ajuste_desde):-1;
  IR_COMPS.forEach(function(c){
   out.push({mod:m,comp:c.k,nome:m+' — '+c.nome,cor:(j.cores||{})[m]||it.cor||'#8b949e',nid:nid,
    fora:fi,vals:it.pct[c.k],w:it.w[c.k],estilo:irEstilo(c.k,nid)});});});
 return out;
}
function irVisivel(s){return !IR_OFF.mod[s.mod]&&!IR_OFF.comp[s.comp];}
function irAlternar(tipo,k){
 IR_OFF[tipo][k]=!IR_OFF[tipo][k]; irRedesenhar(true);      // so redesenha: nao ha pedido ao servidor
}
function irLegendaHtml(j){
 if(!j||j.status!=='OK'||!j.modalidades)return '';
 let h='';
 (j.modalidades_ordem||[]).forEach(function(m){
  const it=j.modalidades[m]||{}, cor=(j.cores||{})[m]||'#8b949e', sem=!it.tem_curva||!it.pct, off=IR_OFF.mod[m];
  const aviso=(it.tem_curva&&!it.parametros_identificados)?' ⚠':'';
  h+='<span class="tog'+((off||sem)?' off':'')+'" data-mod="'+irEsc(m)+'" '+(sem?'title="'+irEsc(it.leitura||'sem curva')+'" aria-disabled="true"':'onclick="irAlternar(\'mod\',\''+irEsc(m)+'\')" role="button"')+'>'+
   '<i style="background:'+irEsc(cor)+'"></i>'+irEsc(m)+aviso+(sem?' (sem curva)':'')+'</span>';});
 const ov=j.overall||{};
 h+='<span class="tog off" data-mod="Overall" aria-disabled="true" title="'+irEsc(ov.motivo||'Overall indisponivel')+'"><i style="background:#484f58"></i>Overall (indisponível)</span>';
 h+='<span style="margin-left:10px"></span>';
 IR_COMPS.forEach(function(c){
  const st=irEstilo(c.k,false);
  h+='<span class="tog'+(IR_OFF.comp[c.k]?' off':'')+'" data-comp="'+c.k+'" role="button" onclick="irAlternar(\'comp\',\''+c.k+'\')">'+
   '<svg width="26" height="8" style="vertical-align:middle;margin-right:4px"><line x1="0" y1="4" x2="26" y2="4" stroke="#c9d1d9" stroke-width="'+st.lw+'" stroke-dasharray="'+st.dash.join(',')+'"/></svg>'+irEsc(c.nome)+'</span>';});
 return h;
}
function irEscalaHtml(j){
 if(!j||j.status!=='OK'||!j.modalidades)return '';
 const sem=(j.modalidades_ordem||[]).filter(function(m){const it=j.modalidades[m];return it&&it.tem_curva&&!it.pct;});
 return 'Eixo: % do P0 do ajuste de cada modalidade (positiva/P0, −negativa/P0, líquida/P0). A normalização é só de apresentação: '+
  'não altera o modelo nem prova que as modalidades sejam fisiologicamente comparáveis. Os watts originais estão na dica (tooltip). '+
  'Trecho esmaecido = depois da última observação do ajuste (fora da amostra). Linhas a tracejado largo e ⚠ = parâmetros não identificáveis. '+
  'Guias a ±100 % de P0 apenas marcam a escala: nenhum valor é cortado.'+
  (sem.length?' <b style="color:#d29922">Normalização impossível (P0 inválido ou não positivo): sem curva em % para '+sem.map(irEsc).join(', ')+'.</b>':'');
}
function irP0Origem(it){
 const p=it&&it.p0; if(!p)return '';
 const u=p.usado_no_ajuste;
 if(u&&u.formulacao==='p0_mmp3_ultimo_yes'){const g=u.registo||{};
  return 'P0 = MMP3 do último YES ('+irNum(u.valor_p0,1)+' W) · '+irEsc(g.test_id||'—')+' · '+irEsc(String(g.inicio||g.data||'').replace('T',' ').slice(0,16))+' · v'+irEsc(u.versao_referencia==null?'—':u.versao_referencia);}
 if(u)return 'P0 de outra regra (anterior ao MMP3 YES): ajuste incompatível';
 return (it.ajuste&&it.ajuste.versao_ajuste!=null)?'P0 livre (ajuste anterior)':'—';
}
function irAvisosHtml(j){
 if(!j||j.status!=='OK'||!j.modalidades)return '';
 const L=[];
 (j.modalidades_ordem||[]).forEach(function(m){
  const it=j.modalidades[m]||{};
  const pa=it.p0&&it.p0.atual;
  if(pa&&pa.valida===false)L.push(['#d29922','<b>'+irEsc(m)+'</b>: sem referência MMP3 válida (nenhum registo YES; não se usa CP, FTP/eFTP nem outra duração) — '+irEsc(pa.detalhe||pa.motivo||'')+
   (it.ajuste&&it.ajuste.versao_ajuste!=null?'. Mantém-se o último ajuste guardado; não foi criado ajuste novo.':'. Não há ajuste.')]);
  if(it.p0&&it.p0.desatualizado)L.push(['#f85149','<b>'+irEsc(m)+'</b>: o ajuste mostrado usa uma referência de P0 mais antiga (v'+irEsc(it.ajuste_valido_anterior&&it.ajuste_valido_anterior.versao_referencia_p0!=null?it.ajuste_valido_anterior.versao_referencia_p0:(((it.p0.usado_no_ajuste||{}).versao_referencia)==null?'—':it.p0.usado_no_ajuste.versao_referencia))+
   ') do que a atual (v'+irEsc(((it.p0.ultima_referencia||{}).versao_referencia)==null?'—':it.p0.ultima_referencia.versao_referencia)+'): K1, K2, T1 e T2 NÃO foram reestimados com o P0 mais recente.']);
  if(it.excecao_solver)L.push(['#f85149','<b>'+irEsc(m)+'</b>: o Solver falhou ao reestimar com o P0 novo ('+irEsc(it.excecao_solver)+'). '+
   (it.ajuste_valido_anterior?'O último ajuste válido (v'+irEsc(it.ajuste_valido_anterior.versao_ajuste)+', P0 '+irNum(it.ajuste_valido_anterior.p0,1)+' W) fica no histórico mas não é apresentado como atual.':'Não há ajuste válido anterior.')]);
  if(!it.tem_curva){L.push(['#8b949e','<b>'+irEsc(m)+'</b>: '+irEsc(it.leitura||'sem curva')]);return;}
  if(it.extrapolacao_antes_das_observacoes)L.push(['#d29922','<b>'+irEsc(m)+'</b>: a curva começa '+irEsc(it.extrapolacao_antes_das_observacoes.dias)+' dias antes da primeira observação do ajuste ('+irEsc(it.extrapolacao_antes_das_observacoes.ate)+'): esse trecho é extrapolação.']);
  if(it.compatibilidade_p0_alvo&&it.compatibilidade_p0_alvo.mediana_alvos_sobre_p0!=null)L.push(['#8b949e','<b>'+irEsc(m)+'</b>: mediana dos alvos = '+irNum(100*it.compatibilidade_p0_alvo.mediana_alvos_sobre_p0,0)+' % de P0; o alvo é o melhor valor da duração na atividade, não um teste máximo.']);
  if(!it.parametros_identificados)L.push(['#d29922','<b>'+irEsc(m)+'</b>: parâmetros NÃO identificáveis — a curva descreve o ajuste, não constantes confirmadas'+
   ((it.no_limite||[]).length?' (no limite: '+it.no_limite.map(function(x){return irEsc(x.parametro)+' '+irEsc(x.lado);}).join(', ')+')':'')+'.']);
  const pl=it.plausibilidade;
  if(pl&&pl.excede){
   const t=['positiva','negativa','liquida'].filter(function(k){return pl.dias_acima[k]>0;}).map(function(k){return k+': máx. '+irNum(pl.max_abs_pct[k],0)+' % em '+pl.dias_acima[k]+' dias';});
   L.push(['#f85149','<b>'+irEsc(m)+'</b>: magnitude acima de '+irNum(pl.limite_pct,0)+' % de P0 (sem corte) — '+t.join('; ')+'. Tratar como artefacto de ajuste até prova em contrário.']);}
  if(it.ajuste_desatualizado)L.push(['#d29922','<b>'+irEsc(m)+'</b>: a carga mudou desde o ajuste; só entra na próxima recalibração (nada foi recalculado agora).']);
 });
 const ct=j.convencao_temporal||{};
 if(ct.lags_diferentes)L.push(['#d29922','As modalidades têm desfasamentos (lag) diferentes ('+irEsc((ct.lag_dias||[]).join(', '))+' dias): não são directamente alinháveis dia a dia.']);
 if(!L.length)return '';
 return '<div style="margin:6px 0">'+L.map(function(x){return '<div style="border-left:3px solid '+x[0]+';padding:2px 10px;margin:3px 0;font-size:12.5px">'+x[1]+'</div>';}).join('')+'</div>';
}
function irTabelaHtml(j){
 if(!j||j.status!=='OK'||!j.modalidades)return '';
 let r='',d='';
 (j.modalidades_ordem||[]).forEach(function(m){
  const it=j.modalidades[m]||{}, e=irEstado(it.estado_ir||it.estado), P=it.parametros, aj=it.ajuste||{}, ok=it.parametros_identificados===true;
  const cel=function(k,dd){return '<td style="text-align:right">'+(P?irNum(P[k],dd)+(ok?'':'<span title="parametro nao identificavel: nao confirmado" style="color:#d29922"> *</span>'):'&mdash;')+'</td>';};
  const rot=(it.sem_calibracao_valida&&e[0]==='NÃO identificável')?['sem calibração válida · tentativa NÃO identificável',e[1]]:e;
  r+='<tr><td><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+irEsc((j.cores||{})[m]||'#888')+';margin-right:5px"></i><b>'+irEsc(m)+'</b></td>'+
   '<td style="color:'+rot[1]+'">'+irEsc(rot[0])+'</td>'+
   '<td style="font-size:11.5px">'+(aj.versao_ajuste!=null?'v'+irEsc(aj.versao_ajuste)+' · '+irEsc(String(aj.ajustado_em||'').slice(0,10))+(it.ajuste_apresentado_e_antigo?' (antigo)':''):'&mdash;')+'</td>'+cel('p0',1)+cel('k1',4)+cel('k2',4)+cel('t1',1)+cel('t2',1)+
   '<td style="text-align:right">'+irEsc(aj.n_obs==null?'—':aj.n_obs)+'</td>'+
   '<td>'+((it.no_limite||[]).length?it.no_limite.map(function(x){return irEsc(x.parametro)+' ('+irEsc(x.lado)+')';}).join(', '):'&mdash;')+'</td>'+
   '<td style="font-size:11.5px">'+(irP0Origem(it)||'&mdash;')+'</td></tr>';
  d+='<p style="font-size:12px;margin:4px 0"><b>'+irEsc(m)+'</b>: '+(it.ajuste&&it.ajuste.versao_ajuste!=null?
   'ajuste v'+irEsc(aj.versao_ajuste)+' ('+irEsc(String(aj.ajustado_em||'').slice(0,16))+'), observações de '+irEsc(aj.data_ini||'—')+' a '+irEsc(aj.data_fim||'—')+
   '; erro dentro da amostra: SSE '+irNum(aj.funcao_objetivo,1)+', RMSE '+irNum(aj.rmse,1)+' W, R² '+irNum(aj.r2,3)+
   '; convergência: '+(aj.convergiu?'sim':(aj.convergiu===false?'não':'—'))+(aj.p0_origem?'; P0 fixo, origem: '+irEsc(aj.p0_origem):'')+
   '; lag '+irEsc(it.lag_dias==null?'—':it.lag_dias)+' dia(s)'+
   ((it.avisos_identificabilidade||[]).length?'; avisos: '+it.avisos_identificabilidade.map(irEsc).join(', '):'')
   :irEsc(it.leitura||'sem ajuste'))+'</p>';});
 return '<div style="overflow-x:auto"><table style="font-size:12px;border-collapse:collapse;min-width:640px"><thead><tr><th style="text-align:left">Modalidade</th><th style="text-align:left">Ajuste</th>'+
  '<th>Versão</th><th>P0 (W)</th><th>K1</th><th>K2</th><th>T1 (d)</th><th>T2 (d)</th><th>N obs.</th><th style="text-align:left">No limite</th><th style="text-align:left">Origem do P0 (fixo)</th></tr></thead><tbody>'+r+'</tbody></table></div>'+
  '<p style="font-size:11.5px;color:#8b949e;margin:3px 0"><span style="color:#d29922">*</span> parâmetro não identificável: não confirmado. Valores do último ajuste guardado, nada recalculado.</p>'+
  '<details><summary style="font-size:12px;cursor:pointer">Detalhes dos ajustes (erro, convergência, lag, avisos)</summary>'+d+'</details>';
}
function irP0DiagHtml(g){
 const d=g&&g.diagnostico; if(!d||!d.total)return '';
 const lim=irNum(100*(d.limiar||0.92),0);
 const mot={sem_esforcos_180s:'não há nenhuma atividade com potência válida de 180 s',
  nenhuma_atividade_com_tag_3min:'nenhuma atividade tem tag de 3 min nem um intervalo de 3 min com potência próxima do MMP3',
  nenhum_esforco_cumpre_todos_os_criterios:'há sinais de teste (tag ou intervalo), mas nenhum esforço cumpre todos os critérios (potência ≥ '+lim+' % do melhor MMP3 anterior, dados válidos)'}[d.motivo_sem_yes]||'';
 const linhas=function(t){return [['Intervalos (grupos com potência) analisados',t.n_intervalos_analisados],['Atividades com intervalos',t.n_atividades_com_intervalos],
  ['Candidatos por duração (175–185 s)',t.n_candidatos_duracao_3min],['…que cumprem o limiar de potência ('+lim+' %)',t.n_candidatos_potencia_3min],
  ['Candidatos mais longos (186–360 s) com potência próxima — só para verificar',t.n_candidatos_longos_potencia],
  ['A verificar (sem tag/intervalo, repetições, pico noutro trecho ou intervalo longo)',t.n_a_verificar],
  ['Testes por tag manual (YES)',t.n_confirmados_tag_manual],['Elegíveis automaticamente por intervalo (YES)',t.n_elegiveis_automaticos],
  ['Atividades com potência válida de 180 s',t.n_180],['Com tag 3min reconhecida',t.n_tag_3min],['Excluídas por ausência de sinal de teste (sem tag)',t.n_sem_tag],
  ['Excluídas por potência ignorada',t.n_potencia_ignorada],['Elegibilidade desconhecida',t.n_elegibilidade_desconhecida],
  ['Sem amostra/referência anterior suficiente',t.n_amostra_ou_referencia_insuficiente],
  ['Cumprem a proximidade ('+lim+' %) mas não os demais critérios',t.n_proximidade_ok_sem_outros],['Com sinal e dados válidos, abaixo do limiar',t.n_abaixo_limiar]];};
 const t=d.total, li=linhas(t);
 const pm=Object.keys(d.por_modalidade||{}).map(function(m){const x=d.por_modalidade[m];return '<tr><td><b>'+irEsc(m)+'</b></td><td>'+irEsc(x.n_180)+'</td><td>'+irEsc(x.n_intervalos_analisados)+'</td><td>'+
  irEsc(x.n_candidatos_duracao_3min)+'</td><td>'+irEsc(x.n_candidatos_potencia_3min)+'</td><td>'+irEsc(x.n_confirmados_tag_manual)+'</td><td>'+irEsc(x.n_elegiveis_automaticos)+'</td><td>'+irEsc(x.n_a_verificar)+'</td></tr>';}).join('');
 return '<details style="margin:2px 0 6px"'+(g.registo?'':' open')+'><summary style="font-size:12px;cursor:pointer">'+(g.registo?'Diagnóstico da deteção de testes':'Porque não existe YES'+(mot?' — '+irEsc(mot):''))+'</summary>'+
  '<ul style="font-size:12px;margin:2px 0 4px 18px">'+li.map(function(x){return '<li>'+irEsc(x[0])+': <b>'+irEsc(x[1])+'</b></li>';}).join('')+'</ul>'+
  '<div style="overflow-x:auto"><table style="font-size:11.5px;border-collapse:collapse"><thead><tr><th style="text-align:left">Modalidade</th><th>180 s</th><th>Intervalos</th><th>3 min (duração)</th><th>3 min (potência)</th><th>Tag manual</th><th>Auto</th><th>A verificar</th></tr></thead><tbody>'+pm+'</tbody></table></div>'+
  '<p style="font-size:11.5px;color:#8b949e;margin:2px 0">Sinal de teste = tag 3min <i>ou</i> um intervalo isolado de 175–185 s com potência ≥ '+lim+' % do melhor MMP3 anterior (origem <code>intervalo_detectado</code>). Repetições, intervalos de 5 min e picos noutro trecho ficam <b>a verificar</b>: nunca YES automático. O limiar não está validado fisiologicamente para intervalos.</p></details>';
}
function irSolverTxt(s){
 if(!s)return '<span style="color:#8b949e">—</span>';
 const t={nao_executado:['não executado','#8b949e'],dados_insuficientes:['dados insuficientes','#d29922'],falha:['falhou','#f85149'],
  executado_nao_identificavel:['executado — parâmetros não identificáveis','#d29922'],executado_ok:['executado — ajuste válido','#3fb950']}[s.estado]||[s.estado,'#8b949e'];
 const mt={sem_p0:'sem P0 (nenhum YES)',sem_observacoes:'sem observações YES',sem_ajuste_gravado:'ainda sem recalibração'}[s.motivo]||s.motivo||'';
 return '<span style="color:'+t[1]+'">'+irEsc(t[0])+'</span>'+(mt?' <span style="color:#8b949e">('+irEsc(mt)+')</span>':'')+
  '<br><span style="color:#8b949e">YES: '+irEsc(s.n_observacoes_yes)+' / mín. '+irEsc(s.min_obs)+(s.faltam_observacoes?' (faltam '+irEsc(s.faltam_observacoes)+')':'')+'</span>';
}
function irP0GlobalHtml(j){
 const g=j&&j.p0_global; if(!g)return '';
 const r=g.registo;
 const est={valida:['válida','#3fb950'],ausente:['AUSENTE — não existe registo MMP3 com estado YES','#d29922'],dados_insuficientes:['DADOS INSUFICIENTES — nenhum registo YES','#d29922']}[g.estado]||[g.estado||'—','#8b949e'];
 return '<div style="border-left:3px solid '+est[1]+';padding:3px 10px;margin:6px 0;font-size:12.5px"><b>P0 global (as 4 modalidades): '+(r?irNum(r.watts,1)+' W':'&mdash;')+'</b> · '+
  'origem: MMP3 do último registo YES · referência <b style="color:'+est[1]+'">'+irEsc(est[0])+'</b>'+
  (r?'<br><span style="color:#8b949e">registo '+irEsc(r.test_id)+' ('+irEsc(r.modalidade)+') · '+irEsc(String(r.inicio||r.data||'').replace('T',' ').slice(0,16))+
  ' · '+irEsc(g.n_yes)+' registo(s) YES no total · classificado por <b>'+irEsc(r.origem_classificacao||'—')+'</b>'+(r.motivo_decisao?' ('+irEsc(r.motivo_decisao)+')':'')+'. Não é o melhor valor histórico nem o PR; o CP Model não é usado.</span>'
  :'<br><span style="color:#8b949e">Sem P0 não há recalibração nem curva nova; não há fallback para CP, FTP/eFTP nem outra duração.</span>')+irP0DiagHtml(g)+'</div>';
}
function irMmp3Html(j){
 if(!j||j.status!=='OK'||!j.modalidades)return '';
 let r='';
 (j.modalidades_ordem||[]).forEach(function(m){
  const it=j.modalidades[m]||{}, x=it.mmp3; if(!x)return;
  const ini=x.mmp3_inicial_temporada, pr=x.mmp3_pr_atual, ue=x.ultimo_esforco||{};
  const w=function(o){return o?irNum(o.watts,0)+' W <span style="color:#8b949e">('+irEsc(o.data)+')</span>':'&mdash;';};
  const aj=it.ajuste||{}, p=it.p0||{};
  const rec=aj.versao_ajuste==null?'<span style="color:#8b949e">sem ajuste</span>':
   (p.desatualizado||it.estado_ir==='erro_solver'?'<span style="color:#f85149">NÃO (ajuste v'+irEsc(aj.versao_ajuste)+' com P0 anterior)</span>':'<span style="color:#3fb950">sim (ajuste v'+irEsc(aj.versao_ajuste)+')</span>');
  r+='<tr><td><b>'+irEsc(m)+'</b></td>'+
   '<td>'+(ini?w(ini):'<span title="'+irEsc(x.mmp3_inicial_indisponivel||'')+'" style="color:#8b949e">indisponível</span>')+'</td>'+
   '<td>'+w(pr)+'</td>'+
   '<td>'+irEsc(ue.texto||'Sem teste elegível')+(ue.data?' <span style="color:#8b949e">('+irEsc(ue.data)+(ue.pr?' · PR':'')+')</span>':'')+'</td>'+
   '<td>'+rec+'</td><td>'+irSolverTxt(it.solver)+'</td></tr>';});
 return irP0GlobalHtml(j)+'<details open><summary style="font-size:12.5px;cursor:pointer"><b>Referência MMP3 (180 s) por modalidade</b> — contexto</summary>'+
  '<div style="overflow-x:auto"><table style="font-size:12px;border-collapse:collapse;min-width:620px"><thead><tr><th style="text-align:left">Modalidade</th>'+
  '<th style="text-align:left">MMP3 inicial da temporada</th><th style="text-align:left">PR atual</th><th style="text-align:left">Último esforço de 3 min</th>'+
  '<th style="text-align:left">Recalibrada com o P0 atual?</th><th style="text-align:left">Solver</th></tr></thead><tbody>'+r+'</tbody></table></div>'+
  '<p style="font-size:11.5px;color:#8b949e;margin:3px 0">Elegibilidade do esforço (Yes/No) é separada do recorde (PR): um teste pode ser elegível abaixo do melhor. '+
  'O MMP3 só alimenta os modelos de CP de 3 parâmetros; M1/M2/M3 não mudam por causa dele e o CP Model não define o P0 do IR.</p></details>'+irAVerificarTodosHtml(j);
}
// candidatos a_verificar (180 s): SO LEITURA para revisao manual. Nao sao YES, nao entram no P0 nem nas observacoes.
const IR_CLASSE_INTERVALO={intervalo_3min_automatico:'intervalo de 3 min com potência suficiente (sinal)',
 a_verificar_repeticoes:'repetições de 3 min (vários grupos)',a_verificar_pico_fora:'pico de 3 min noutro trecho',
 a_verificar_intervalo_longo:'intervalo longo (186–360 s), não são 3 min',intervalo_3min_abaixo_limiar:'intervalo de 3 min abaixo do limiar',
 sem_intervalo_3min:'sem intervalo de 3 min na atividade',sem_dados_de_intervalos:'sem dados de intervalos na atividade'};
function irAVerificarHtml(mod,lst){
 const linhas=lst.map(function(c){
  const ref=c.melhor_anterior_watts==null?'&mdash;':irNum(c.melhor_anterior_watts,1)+' W <span style="color:#8b949e">('+irEsc(c.melhor_anterior_data||'')+')</span>';
  const iv=c.intervalo?irNum(c.intervalo.duracao_s,0)+' s a '+irNum(c.intervalo.watts,1)+' W'+(c.intervalo.n?' (n='+irEsc(c.intervalo.n)+')':''):'&mdash;';
  return '<tr><td>'+irEsc(c.data)+'</td><td style="font-size:11.5px">'+irEsc(c.activity_id)+'</td>'+
   '<td style="text-align:right">'+irNum(c.watts,1)+' W</td><td>'+ref+'</td>'+
   '<td style="text-align:right">'+irNum(c.pct_do_melhor,1)+' %</td>'+
   '<td style="font-size:11.5px">'+(c.tags&&c.tags.length?c.tags.map(function(t){return irEsc(t);}).join(', '):'&mdash;')+'</td>'+
   '<td style="font-size:11.5px">'+irEsc(IR_CLASSE_INTERVALO[c.classe_intervalo]||c.classe_intervalo||'—')+'</td>'+
   '<td style="font-size:11.5px">'+iv+'</td></tr>';}).join('');
 return '<details style="margin:4px 0"><summary style="font-size:12px;cursor:pointer"><b>'+irEsc(mod)+'</b>: '+irEsc(lst.length)+
  ' candidato(s) a verificar — decisão manual, não são YES</summary>'+
  '<div style="overflow-x:auto"><table style="font-size:12px;border-collapse:collapse;min-width:700px"><thead><tr>'+
  '<th style="text-align:left">Data</th><th style="text-align:left">Atividade</th><th style="text-align:right">Potência 180 s</th>'+
  '<th style="text-align:left">Referência anterior</th><th style="text-align:right">% do melhor</th><th style="text-align:left">Tags</th>'+
  '<th style="text-align:left">Classificação do intervalo</th><th style="text-align:left">Intervalo detetado</th></tr></thead><tbody>'+linhas+'</tbody></table></div></details>';
}
function irAVerificarTodosHtml(j){
 const ordem=(j&&j.modalidades_ordem)||[];
 const blocos=ordem.map(function(m){const x=((j.modalidades||{})[m]||{}).mmp3||{}; const lst=x.a_verificar||[];
  return lst.length?irAVerificarHtml(m,lst):'';}).join('');
 if(!blocos)return '';
 const lim0=ordem.map(function(m){return ((((j.modalidades||{})[m]||{}).mmp3)||{}).limiar;}).find(function(v){return v!=null;});
 return '<div style="margin-top:6px"><b style="font-size:12.5px">Candidatos a verificar (180 s)</b>'+
  '<p style="font-size:11.5px;color:#8b949e;margin:2px 0">Sinal de teste em falta: qualificam pela potência'+(lim0==null?'':' (≥ '+irNum(100*lim0,0)+' % do melhor anterior)')+
  ' mas sem tag de 3 min nem intervalo isolado. Só mostram os dados; a decisão de YES continua manual.</p>'+blocos+'</div>';
}
function irOverallHtml(j){
 const o=j&&j.overall; if(!o)return '';
 return '<details><summary style="font-size:12px;cursor:pointer">Overall: indisponível — porquê e o que falta</summary><p style="font-size:12px;margin:4px 0">'+irEsc(o.motivo)+'</p>'+
  '<ul style="font-size:12px;margin:2px 0 6px 18px">'+(o.falta||[]).map(function(x){return '<li>'+irEsc(x)+'</li>';}).join('')+'</ul></details>';
}
function irInfoHtml(j){
 if(!j||j.status!=='OK')return '<p style="font-size:13px">Nao foi possivel calcular: '+irEsc(j&&j.mensagem?j.mensagem:'sem resposta')+'</p>';
 if(j.estado==='sem_curvas')return '<p style="font-size:13px"><b>Nenhuma modalidade tem curva (sem ajuste válido).</b> Nada foi calculado nem desenhado (sem zeros artificiais); ver o estado de cada modalidade abaixo.</p>';
 return '';
}
// grafico unico, eixo unico (% de P0), eixo diario comum; valores ausentes (antes da origem) interrompem a linha
function irDesenhar(j){
 const lg=document.getElementById('lgIrConj'); if(lg)lg.innerHTML=irLegendaHtml(j);
 const o=ctx('chIrConj',300); if(!o)return;
 const g=o.g,W=o.W,H=o.H;
 if(!j||j.status!=='OK'||j.estado==='sem_curvas'||!j.datas||!j.datas.length){noData(g,W,H,(j&&j.status==='OK')?'Sem ajuste válido: nenhuma curva desenhada':'Sem dados');return;}
 const S=irSeries(j).filter(irVisivel), n=j.datas.length, PL=54,PR=14,PT=12,PB=26,w=W-PL-PR,h=H-PT-PB;
 if(!S.length){noData(g,W,H,'Todas as séries estão desligadas');return;}
 let mn=0,mx=0;
 S.forEach(function(s){s.vals.forEach(function(v){if(v==null)return;if(v<mn)mn=v;if(v>mx)mx=v;});});
 if(mx===mn)mx=mn+1;
 const m=(mx-mn)*0.04; mn-=m; mx+=m;
 // graduacao "redonda" (1, 2, 5 x 10^k) em vez de 5 divisoes arbitrarias
 const bruto=(mx-mn)/4, pot=Math.pow(10,Math.floor(Math.log(bruto)/Math.LN10)), mult=[1,2,5,10].filter(function(q){return q*pot>=bruto;})[0], passo=mult*pot;
 mn=Math.floor(mn/passo)*passo; mx=Math.ceil(mx/passo)*passo;
 const X=function(i){return PL+w*(n>1?i/(n-1):0.5);}, Y=function(v){return PT+h-(v-mn)/(mx-mn)*h;};
 g.strokeStyle='#21262d';g.lineWidth=1;g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';g.setLineDash([]);
 for(let v=mn;v<=mx+passo/1e6;v+=passo){const y=Y(v);g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();g.fillText(Math.round(v)+' %',PL-6,y+3);}
 g.strokeStyle='#484f58';g.beginPath();g.moveTo(PL,Y(0));g.lineTo(PL+w,Y(0));g.stroke();
 [100,-100].forEach(function(v){if(v>=mn&&v<=mx){g.strokeStyle='#30363d';g.setLineDash([2,4]);g.beginPath();g.moveTo(PL,Y(v));g.lineTo(PL+w,Y(v));g.stroke();g.setLineDash([]);
  g.fillStyle='#6e7681';g.textAlign='left';g.fillText((v>0?'+':'−')+'100 % de P0',PL+4,Y(v)+(v>0?-3:10));}});
 g.fillStyle='#8b949e';
 for(let k=0;k<=4;k++){const i=Math.round((n-1)*k/4);g.textAlign=k===0?'left':(k===4?'right':'center');g.fillText(j.datas[i].slice(0,7),X(i),H-8);}
 // finas por baixo, liquida (espessa) por cima
 S.slice().sort(function(a,b){return (a.comp==='liquida'?1:0)-(b.comp==='liquida'?1:0);}).forEach(function(s){
  g.strokeStyle=s.cor;g.lineWidth=s.estilo.lw;g.setLineDash(s.estilo.dash);
  [[0,s.fora>=0?s.fora:n,1],[s.fora>=0?Math.max(0,s.fora-1):n,n,0.4]].forEach(function(seg){
   if(seg[0]>=seg[1])return;
   g.globalAlpha=seg[2];g.beginPath();let ini=false;
   for(let i=seg[0];i<seg[1];i++){const v=s.vals[i];if(v==null){ini=false;continue;}const x=X(i),y=Y(v);if(!ini){g.moveTo(x,y);ini=true;}else g.lineTo(x,y);}
   g.stroke();});
  g.globalAlpha=1;g.setLineDash([]);});
 registarTip('chIrConj',function(x){
  if(x<PL||x>PL+w)return '';
  const i=Math.max(0,Math.min(n-1,Math.round((x-PL)/w*(n-1))));
  let hh='<div class="th">'+irEsc(j.datas[i])+'</div>';
  const vistos={};
  S.forEach(function(s){
   const v=s.vals[i], wv=s.w[i];
   const f=(s.fora>=0&&i>=s.fora)?' (fora do ajuste)':'', big=(v!=null&&Math.abs(v)>100)?' ⚠ &gt;100 % de P0':'';
   hh+=linhaTip(s.cor,irEsc(s.mod)+' · '+irEsc(s.comp)+(s.nid?' (não identific.)':''),
    v==null?'ainda sem série (antes da origem)':irNum(v,1)+' % de P0 &middot; '+irNum(s.comp==='negativa'?-wv:wv,1)+' W'+f+big);});
  return hh;});
}
// Diagnostico compacto por modalidade (so leitura): a calibracao VALIDA e a TENTATIVA mais recente aparecem
// separadas; os parametros da tentativa nunca se mostram como calibracao. Campos ausentes -> '—' (resposta antiga).
function irDiagEst(e){return {ajustado_identificavel:'identificável',ajustado_nao_identificavel:'não identificável',
 dados_insuficientes:'dados insuficientes',sem_convergencia:'sem convergência',sem_calibracao_valida:'sem calibração válida'}[e]||(e==null?'—':irEsc(e));}
function irDiagFalta(f){if(!f)return '—';var L=[],N={observacoes:'obs.',datas_distintas:'datas',dias_cobertura:'dias'};
 Object.keys(f).forEach(function(k){if(f[k]>0)L.push(irEsc(N[k]||k)+' +'+irEsc(f[k]));});return L.length?L.join(', '):'nenhuma';}
function irDiagHtml(j){
 if(!j||j.status!=='OK'||!j.modalidades)return '';
 let r='';
 (j.modalidades_ordem||[]).forEach(function(m){
  const it=j.modalidades[m]||{}, t=it.tentativa_mais_recente||null, rq=(t&&t.requisitos)||it.requisitos||null;
  const a=rq&&rq.atuais, mn=rq&&rq.minimo, cv=(t&&t.convergencia)||it.convergencia||null;
  const ajEst=it.estado_ajuste||it.estado;
  // resposta antiga (sem os campos novos): so se afirma o que a resposta mostra; nunca 'nenhuma' por omissao
  const valida=(it.sem_calibracao_valida||ajEst==='sem_calibracao_valida')?'nenhuma'
    :(ajEst==='ajustado_identificavel'&&it.ajuste&&it.ajuste.versao_ajuste!=null)?
      'v'+irEsc(it.ajuste.versao_ajuste)+(it.p0&&it.p0.desatualizado?' (P0 desatualizado)':'')
    :'—';
  const tent=t?'v'+irEsc(t.versao_ajuste)+' · '+irDiagEst(t.estado)
    :(Object.prototype.hasOwnProperty.call(it,'tentativa_mais_recente')?'nenhuma':'—');
  const conv=cv&&cv.convergiu!=null?(cv.convergiu?'sim':'não')+(cv.status?' ('+irEsc(cv.status)+')':''):'—';
  const par=function(k){return a&&mn?irEsc(a[k])+' / '+irEsc(mn[k]):'—';};
  const motivo=t?(t.motivo_insuficiente||t.motivo||''):'';
  const avisos=((t?t.avisos_identificabilidade:it.avisos_identificabilidade)||[]).map(irEsc).join('; ');
  r+='<tr><td><b>'+irEsc(m)+'</b></td><td style="font-size:11.5px">'+irDiagEst(ajEst)+'</td>'+
   '<td style="font-size:11.5px">'+valida+'</td><td style="font-size:11.5px">'+tent+'</td>'+
   '<td style="font-size:11.5px">'+conv+'</td><td style="text-align:right">'+par('observacoes')+'</td>'+
   '<td style="text-align:right">'+par('datas_distintas')+'</td><td style="text-align:right">'+par('dias_cobertura')+'</td>'+
   '<td style="font-size:11.5px">'+irDiagFalta(rq&&rq.em_falta)+'</td><td style="font-size:11.5px">'+irEsc(motivo||'—')+'</td>'+
   '<td style="font-size:11.5px">'+(avisos||'—')+'</td></tr>';
 });
 return '<div style="overflow-x:auto;margin-top:6px"><table style="font-size:12px;border-collapse:collapse;min-width:900px"><thead><tr>'+
   '<th style="text-align:left">Modalidade</th><th style="text-align:left">Ajuste mostrado</th><th style="text-align:left">Calibração válida</th>'+
   '<th style="text-align:left">Tentativa mais recente</th><th style="text-align:left">Convergência</th><th>Obs. (atual / mín.)</th>'+
   '<th>Datas (atual / mín.)</th><th>Cobertura dias (atual / mín.)</th><th style="text-align:left">Em falta</th>'+
   '<th style="text-align:left">Motivo</th><th style="text-align:left">Avisos do solver</th></tr></thead><tbody>'+r+'</tbody></table></div>';
}
function irRedesenhar(soDesenho){
 const j=IR_J;
 if(!soDesenho){
  const set=function(id,h){const e=document.getElementById(id); if(e)e.innerHTML=h;};
  set('irInfo',irInfoHtml(j)); set('irEscala',irEscalaHtml(j)); set('irAvisos',irAvisosHtml(j)); set('irTabela',irTabelaHtml(j)); set('irDiag',irDiagHtml(j)); set('irMmp3',irMmp3Html(j)); set('irOverall',irOverallHtml(j));
 }
 irDesenhar(j);
}
async function carregarIR(){
 if(!IR_MONTADO)irShell();
 const seq=++IR_SEQ;
 let j=null;
 try{ j=await fetch('/api/modelo/ir/conjunto').then(function(r){return r.json();}); }
 catch(e){ j=null; }
 if(seq!==IR_SEQ)return;                       // resposta antiga de uma selecao anterior
 IR_J=j; irRedesenhar();
}
function abrirIR(d){
 if(d&&d.open&&!IR_MONTADO)carregarIR();
}
// <</modelo-ir>>
// <<ir-exploratorio>>
// ─── IR EXPLORATORIO (historico, nao validado) ─────────────────────────────────────────────
// So LE /api/modelo/ir/exploratorio: P0 livre por unidade, duracoes historicas 60/300/1200 s. Nada e gravado,
// nada substitui o IR calibrado (180 s, P0 fixo) e nao se misturam parametros, observacoes ou estados.
// Sem ajuste (ou com dados insuficientes) nao ha curva: o motivo e mostrado, nunca uma curva artificial.
var IRX_J=null, IRX_SEQ=0, IRX_MONTADO=false;
function irxEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function irxNum(x,d){return (x==null||!isFinite(x))?'&mdash;':Number(x).toFixed(d==null?2:d);}
function irxRotulo(){return '<div style="border-left:3px solid #d29922;padding:3px 10px;margin:6px 0;font-size:12.5px">'+
  '<b style="color:#d29922">Exploratório — não validado.</b> P0 livre estimado por unidade, ajuste dentro da amostra, '+
  'observações do detetor histórico (sem exigir YES). Não é guardado, não é uma calibração e não se relaciona com o IR calibrado.</div>';}
function irxDiagCel(it){
  var c=it.convergencia, r=it.requisitos, a=r&&r.atuais, mn=r&&r.minimo;
  var conv=c&&c.convergiu!=null?(c.convergiu?'sim':'não')+(c.status?' ('+irxEsc(c.status)+')':''):'&mdash;';
  var cob=a&&mn?irxEsc(a.dias_cobertura)+' / '+irxEsc(mn.dias_cobertura):'&mdash;';
  var N={observacoes:'obs.',datas_distintas:'datas',dias_cobertura:'dias'}, f=r&&r.em_falta, L=[];
  if(f)Object.keys(f).forEach(function(k){if(f[k]>0)L.push(irxEsc(N[k]||k)+' +'+irxEsc(f[k]));});
  var falta=f?(L.length?L.join(', '):'nenhuma'):'&mdash;';
  return '<td style="font-size:11.5px">'+conv+'</td><td style="text-align:right">'+cob+'</td><td style="font-size:11.5px">'+falta+'</td>';
}
function irxTabela(j){
  var L='';
  (j.itens||[]).forEach(function(it){
    var p=it.parametros, m=it.metricas||{};
    var c=function(v,d){return '<td style="text-align:right">'+(p?irxNum(v,d):'&mdash;')+'</td>';};
    L+='<tr><td><b>'+irxEsc(it.modalidade)+'</b></td><td style="text-align:right">'+irxEsc(it.duracao_s)+' s</td>'+
      '<td style="text-align:right">'+irxEsc(it.n_obs)+'</td><td style="text-align:right">'+irxEsc(it.n_datas_distintas)+'</td>'+
      '<td style="font-size:11.5px">'+irxEsc(it.data_ini||'&mdash;')+' a '+irxEsc(it.data_fim||'&mdash;')+'</td>'+
      c(p&&p.p0,1)+c(p&&p.k1,4)+c(p&&p.k2,4)+c(p&&p.t1,1)+c(p&&p.t2,1)+
      '<td style="text-align:right">'+irxNum(m.r2,3)+'</td><td style="text-align:right">'+irxNum(m.rmse,1)+'</td>'+
      '<td style="color:'+(it.curva?'#3fb950':'#8b949e')+'">'+irxEsc(it.estado)+(it.motivo?' ('+irxEsc(it.motivo)+')':'')+'</td>'+irxDiagCel(it)+'</tr>';
  });
  return '<div style="overflow-x:auto"><table style="font-size:12px;border-collapse:collapse;min-width:760px"><thead><tr>'+
    '<th style="text-align:left">Modalidade</th><th>Duração</th><th>N obs.</th><th>Datas</th><th style="text-align:left">Período</th>'+
    '<th>P0 livre (W)</th><th>K1</th><th>K2</th><th>T1 (d)</th><th>T2 (d)</th><th>R²</th><th>RMSE (W)</th>'+
    '<th style="text-align:left">Estado</th><th style="text-align:left">Convergência</th><th>Cobertura dias (atual / mín.)</th>'+
    '<th style="text-align:left">Em falta</th></tr></thead><tbody>'+L+'</tbody></table></div>';
}
function irxExclHtml(j){
  var e=j&&j.exclusoes; if(!e)return '';
  var pm=e.por_motivo||{}, pu=e.por_unidade||{}, lista=e.lista||[];
  var total=(e.total!=null)?e.total:lista.length;
  if(!total)return '<p style="font-size:12px;color:#8b949e;margin:4px 0">Nenhuma observação excluída nesta análise.</p>';
  var mots=Object.keys(pm);
  var po=e.por_origem;
  var h='<div style="font-size:12px;margin:6px 0"><b>Exclusões do Exploratório: '+irxEsc(total)+' observações</b>'+
    (po?' ('+irxEsc(po.filtro_all_time||0)+' pelo filtro all-time desta fase; '+irxEsc(po.selecao_anterior||0)+' já excluídas pela seleção anterior)':'')+
    ' — por modalidade:duração e motivo; a lista completa, com a referência e a proveniência, está abaixo</div>';
  h+='<div style="overflow-x:auto"><table style="font-size:12px;border-collapse:collapse"><thead><tr><th style="text-align:left">Modalidade:duração</th>'+
    mots.map(function(m){return '<th>'+irxEsc(m)+'</th>';}).join('')+'<th>Total</th></tr></thead><tbody>';
  Object.keys(pu).forEach(function(u){
    var row=pu[u]||{}, tot=0; Object.keys(row).forEach(function(k){tot+=row[k];});
    h+='<tr><td>'+irxEsc(u)+'</td>'+mots.map(function(m){return '<td style="text-align:right">'+(row[m]||0)+'</td>';}).join('')+
      '<td style="text-align:right"><b>'+tot+'</b></td></tr>';});
  h+='</tbody></table></div>';
  h+='<details style="margin-top:6px"><summary style="font-size:12px;cursor:pointer">Lista completa de exclusões ('+irxEsc(total)+')</summary>'+
    '<div style="overflow-x:auto"><table style="font-size:11.5px;border-collapse:collapse"><thead><tr><th style="text-align:left">Modalidade</th>'+
    '<th>Duração</th><th style="text-align:left">Data</th><th style="text-align:left">Atividade do esforço</th><th>W</th><th style="text-align:left">Motivo</th><th style="text-align:left">Referência</th>'+
    '<th>% da ref.</th><th style="text-align:left">Proveniência da referência (atividade · data · W)</th></tr></thead><tbody>';
  lista.forEach(function(x){
    h+='<tr><td>'+irxEsc(x.modalidade)+'</td><td style="text-align:right">'+irxEsc(x.duracao_s)+' s</td><td>'+irxEsc(x.data||'—')+'</td>'+
      '<td>'+irxEsc(x.atividade_esforco_id||'—')+'</td>'+
      '<td style="text-align:right">'+irxNum(x.watts,1)+'</td><td>'+irxEsc(x.motivo)+'</td><td>'+irxEsc(x.referencia_tipo||'—')+'</td>'+
      '<td style="text-align:right">'+irxNum(x.pct_da_referencia,1)+'</td><td>'+irxEsc(x.referencia_activity_id||'—')+' · '+
      irxEsc(x.referencia_data||'—')+' · '+irxNum(x.referencia_watts,1)+' W</td></tr>';});
  return h+'</tbody></table></div></details>';
}
function irxAvisos(j){
  var L=[];
  (j.itens||[]).forEach(function(it){
    var u='<b>'+irxEsc(it.modalidade)+' '+irxEsc(it.duracao_s)+' s</b>: ';
    (it.avisos||[]).forEach(function(a){L.push(u+'identificabilidade — '+irxEsc(a));});
    if(it.estado==='ajustado_nao_identificavel')L.push(u+'parâmetros não identificáveis: a curva descreve o ajuste, não a fisiologia.');
    if(!it.curva)L.push(u+'sem curva — '+irxEsc(it.motivo||it.estado)+' (observações: '+irxEsc(it.n_obs)+').');
  });
  return L.length?'<ul style="font-size:12px;margin:4px 0 4px 18px">'+L.map(function(x){return '<li>'+x+'</li>';}).join('')+'</ul>':'';
}
function irxPintar(cvId,its,legId,vazio){
  var j=IRX_J; if(!j||j.status!=='OK')return;
  var o=ctx(cvId,300); if(!o)return; var g=o.g,W=o.W,H=o.H;
  var leg=document.getElementById(legId);
  if(!its.length){noData(g,W,H,vazio||'Sem curva nesta duração');
    if(leg)leg.innerHTML='<span style="color:#d29922">Exploratório — não validado</span>';return;}
  var set={}; its.forEach(function(it){it.curva.datas.forEach(function(d){set[d]=1;});});
  var datas=Object.keys(set).sort(), pos={}; datas.forEach(function(d,i){pos[d]=i;});
  var mn=Infinity,mx=-Infinity;
  its.forEach(function(it){it.curva.liquida.forEach(function(v){if(v<mn)mn=v;if(v>mx)mx=v;});});
  if(!isFinite(mn)){noData(g,W,H,'Sem valores');return;}
  if(mx===mn)mx=mn+1;
  var PL=54,PR=14,PT=12,PB=26,w=W-PL-PR,h=H-PT-PB;
  var X=function(i){return PL+w*(datas.length>1?i/(datas.length-1):0.5);}, Y=function(v){return PT+h-(v-mn)/(mx-mn)*h;};
  var COR=(j.cores||{});
  g.strokeStyle='#21262d';g.fillStyle='#8b949e';g.font='10px sans-serif';g.textAlign='right';
  for(var k=0;k<=4;k++){var v=mn+(mx-mn)*k/4,y=Y(v);g.beginPath();g.moveTo(PL,y);g.lineTo(PL+w,y);g.stroke();g.fillText(Math.round(v)+' W',PL-6,y+3);}
  its.forEach(function(it){
    g.strokeStyle=COR[it.modalidade]||'#8b949e';g.lineWidth=1.6;g.beginPath();var ini=false;
    it.curva.datas.forEach(function(d,i){var x=X(pos[d]),y=Y(it.curva.liquida[i]);if(!ini){g.moveTo(x,y);ini=true;}else g.lineTo(x,y);});
    g.stroke();
  });
  g.fillStyle='#8b949e';g.textAlign='left';g.fillText(datas[0].slice(0,7),PL,H-8);
  g.textAlign='right';g.fillText(datas[datas.length-1].slice(0,7),PL+w,H-8);
  if(leg)leg.innerHTML=its.map(function(it){return '<span style="margin-right:12px"><i style="display:inline-block;width:10px;height:10px;background:'+irxEsc(COR[it.modalidade]||'#8b949e')+';margin-right:4px"></i>'+irxEsc(it.modalidade)+' · '+irxEsc(it.duracao_s)+' s</span>';}).join('')+
    ' <span style="color:#d29922">Exploratório — não validado (linha líquida = P0 livre + K1·E − K2·G)</span>';
}
// Season (experimental): bloco SEPARADO do all-time. Parametros so aparecem quando a classe e candidata.
var IRX_CLASSE={candidata_calibracao:'candidata a calibração (experimental)',apenas_validacao:'apenas validação',
  insuficiente:'insuficiente',nao_identificavel:'não identificável — valores não válidos',nao_calculado:'não calculado'};
function irxSeasonHtml(j){
  var s=j&&j.season; if(!s)return '';
  var h='<div style="margin-top:12px;border-top:1px solid #30363d;padding-top:8px"><b>Season — experimental, separada do all-time (não combinada)</b>'+
    '<div style="font-size:11.5px;color:#8b949e">'+irxEsc(s.aviso||'')+'</div>';
  if(s.status!=='OK'){
    return h+'<p style="font-size:12px;color:#d29922">Fronteiras de season indisponíveis ('+irxEsc(s.status)+'): '+
      'sem marcos SEASON_START não se usam anos civis e nenhum ajuste de season foi feito.</p></div>';
  }
  var fr=(s.fronteiras||[]).map(function(f){return irxEsc(f.season)+': '+irxEsc(f.inicio)+' a '+irxEsc(f.fim)+(f.aberta?' (aberta)':'');}).join(' · ');
  h+='<div style="font-size:12px;margin:4px 0">Fronteiras reais ('+irxEsc(s.fonte_fronteiras)+'): '+(fr||'&mdash;')+'</div>';
  h+='<table style="font-size:12px;width:100%"><thead><tr><th>Mod.</th><th>Dur.</th><th>Season</th><th>Datas</th>'+
    '<th>n obs</th><th>Classificação</th><th>Parâmetros</th><th>Referência</th><th>Exclusões</th></tr></thead><tbody>';
  (s.itens||[]).forEach(function(it){
    var pr=it.parametros, v=it.valido_para_calibracao===true;
    var par=(v&&pr)?['p0','k1','k2','t1','t2'].map(function(k){return k+'='+irxNum(pr[k],3);}).join(' '):'&mdash;';
    var tipos=(it.tipos_referencia||[]).join(',')+(it.n_tambem_all_time?' ('+irxEsc(it.n_tambem_all_time)+' também all-time)':'');
    h+='<tr><td>'+irxEsc(it.modalidade)+'</td><td style="text-align:right">'+irxEsc(it.duracao_s)+' s</td>'+
      '<td>'+irxEsc(it.season||'&mdash;')+'</td>'+
      '<td style="font-size:11.5px">'+irxEsc(it.data_ini||'&mdash;')+' a '+irxEsc(it.data_fim||'&mdash;')+'</td>'+
      '<td style="text-align:right">'+irxEsc(it.n_obs==null?'&mdash;':it.n_obs)+'</td>'+
      '<td>'+irxEsc(IRX_CLASSE[it.classificacao]||it.classificacao||'&mdash;')+
        (it.motivo_classificacao?'<div style="font-size:11px;color:#8b949e">'+irxEsc(it.motivo_classificacao)+'</div>':'')+'</td>'+
      '<td style="font-size:11.5px">'+par+'</td>'+
      '<td style="font-size:11.5px">'+(tipos||'&mdash;')+'</td>'+
      '<td style="text-align:right">'+irxEsc((it.exclusoes||[]).length)+'</td></tr>';
  });
  h+='</tbody></table>';
  var ids=(s.itens||[]).filter(function(it){return it.season;}).map(function(it){
    return '<div style="font-size:11px;margin-top:3px">'+irxEsc(it.modalidade)+' '+irxEsc(it.duracao_s)+' s · '+irxEsc(it.season)+': '+
      irxEsc((it.ids_esforcos||[]).join(', ')||'nenhum')+'</div>';}).join('');
  if(ids)h+='<details style="margin-top:6px"><summary style="font-size:12px;cursor:pointer">Esforços usados por season</summary>'+ids+'</details>';
  return h+'</div>';
}
function irxGlobalHtml(g){
  var cab='<b>Modelo global (referência, não validado)</b>: ';
  if(!g)return cab+'não calculado.';
  if(g.estado==='global_ajustado_identificavel')
    return cab+'duração comum '+irxEsc(g.duracao_s)+' s, '+irxEsc((g.unidades||[]).length)+' unidades, homogeneidade não rejeitada. '+
      'Curva global não desenhada: as unidades estão em fração do nível P0 de cada modalidade (não há unidade comum em watts).';
  var av=(g.identificabilidade&&g.identificabilidade.avisos)||[];
  return cab+'indisponível: '+irxEsc(g.motivo||g.estado||'sem motivo')+
    (av.length?' — '+irxEsc(av.join(', ')):'')+(g.leitura?' — '+irxEsc(g.leitura):'');
}
// Mensagem curta e especifica para uma modalidade sem curva. So traduz estado/motivo ja calculados no servidor.
function irxMensagemCurta(s){
  var st=s.estado||'', mo=s.motivo||'';
  if(st==='dados_insuficientes')return 'sem curva: observações insuficientes (mínimo de 15 por duração)';
  if(st==='sem_observacoes')return 'sem esforços elegíveis nesta modalidade';
  if(st==='sem_convergencia')return 'sem curva: o ajuste não convergiu';
  if(mo.indexOf('parametro_no_limite')>=0||mo.indexOf('ic_perfil_toca_limite')>=0)return 'sem curva: ajuste não identificável (parâmetros nos limites)';
  if(mo.indexOf('mal_condicionado')>=0)return 'sem curva: ajuste não identificável (mal condicionado)';
  if(mo.indexOf('solucoes_equivalentes_divergentes')>=0)return 'sem curva: ajuste não identificável (soluções divergentes)';
  return 'sem curva identificável nesta amostra';
}
function irxRenderMain(){
  var j=IRX_J, aviso=document.getElementById('irPrincipalAviso'), est=document.getElementById('irPrincipalEstados');
  var glob=document.getElementById('irPrincipalGlobal'), lg=document.getElementById('irxLegMain');
  if(!j||j.status!=='OK'){
    if(aviso)aviso.textContent='Modo exploratório indisponível'+(j&&j.mensagem?': '+j.mensagem:' (sem resposta).');
    if(est)est.innerHTML=''; if(glob)glob.innerHTML=''; if(lg)lg.innerHTML=''; return;}
  if(aviso)aviso.textContent='Exploratório, não validado. Só se mostram curvas identificáveis; sem curva, a modalidade indica o motivo.';
  var sa=j.selecao_automatica||{}, mods=Object.keys(sa);
  var its=mods.map(function(m){return sa[m]&&sa[m].selecionada;}).filter(Boolean);
  if(est)est.innerHTML=mods.map(function(m){
    var s=sa[m]||{}, it=s.selecionada, cor=(j.cores||{})[m]||'#8b949e';
    if(it)return '<div><b style="color:'+irxEsc(cor)+'">'+irxEsc(m)+'</b> &middot; '+irxEsc(it.duracao_s)+' s &middot; '+
      irxEsc(it.n_obs)+' observações ('+irxEsc(it.n_datas_distintas)+' datas) &middot; '+irxEsc(it.data_ini)+' a '+irxEsc(it.data_fim)+
      ' &middot; <span style="color:#3fb950">identificável</span></div>';
    return '<div><b style="color:'+irxEsc(cor)+'">'+irxEsc(m)+'</b> &middot; <span style="color:#d29922">'+irxEsc(irxMensagemCurta(s))+'</span></div>';
  }).join('');
  if(glob)glob.innerHTML=irxGlobalHtml(j.global);
  irxPintar('chIrxMain',its,'irxLegMain','Nenhuma modalidade com curva identificável nesta amostra (ver estados abaixo)');
}
function irxRender(erro){
  irxRenderMain();
  var el=document.getElementById('irExploratorioCorpo'); if(!el)return;
  var j=IRX_J;
  if(erro||!j){el.innerHTML=irxRotulo()+'<p style="color:#f85149;font-size:12.5px">Não foi possível obter o modo exploratório: '+irxEsc(erro||'sem resposta')+'</p>';return;}
  if(j.status!=='OK'){el.innerHTML=irxRotulo()+'<p style="color:#f85149;font-size:12.5px">Erro no modo exploratório: '+irxEsc(j.mensagem||'desconhecido')+'</p>';return;}
  el.innerHTML=irxRotulo()+'<div id="irxAv"></div><div id="irxTab"></div><div id="irxExcl"></div><div id="irxSea"></div>';
  document.getElementById('irxAv').innerHTML=irxAvisos(j);
  document.getElementById('irxTab').innerHTML=irxTabela(j);
  var irxEx=document.getElementById('irxExcl'); if(irxEx)irxEx.innerHTML=irxExclHtml(j);
  var irxSe=document.getElementById('irxSea'); if(irxSe)irxSe.innerHTML=irxSeasonHtml(j);
}
async function carregarIRExploratorio(){
  IRX_MONTADO=true; var seq=++IRX_SEQ, j=null, erro=null;
  try{ j=await fetch('/api/modelo/ir/exploratorio').then(function(r){return r.json();}); }
  catch(e){ erro='falha de rede ('+(e&&e.message?e.message:e)+')'; }
  if(seq!==IRX_SEQ)return;                      // resposta antiga
  IRX_J=j; irxRender(erro);
}
function abrirIRExploratorio(d){ if(d&&d.open&&!IRX_MONTADO)carregarIRExploratorio(); }
// <</ir-exploratorio>>
// <<ir-sombra>>
// ─── IR SOMBRA: previsao experimental vs valor observado (Etapa 15) ────────────────────────────
// SO LE /api/modelo/ir/sombra, UMA seleccao por pedido, e so quando o utilizador clica em "Carregar".
// Nada e gravado, nada e recalibrado e nenhum candidato e promovido. O estado e calculado no servidor
// (modelo_ir_painel); aqui so se desenha. A curva operacional (% do P0, IR_J ja carregado) nunca entra
// na mesma serie que a sombra (W): mostra-se apenas o resumo do ajuste ativo.
const SOM_MOD=['Bike','Row','Ski','Run'], SOM_DUR=[60,300,1200];
const SOM_MODO=[['conhecido_na_epoca','Temporal (so com informacao conhecida na epoca)'],['retrospectivo','Retrospectivo (valores atuais, nao e validacao)']];
const SOM_COR={disponivel:'#3fb950',incompleto:'#d29922',vazio:'#8b949e',indisponivel:'#f85149'};
const SOM_ROT={disponivel:'disponivel',incompleto:'incompleto',vazio:'vazio',indisponivel:'indisponivel'};   // estados canonicos da API
let SOM_MONTADO=false, SOM_SEQ=0;
function somEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function somNum(x,d){return (x==null||!isFinite(x))?'&mdash;':Number(x).toFixed(d==null?1:d);}
function somBadge(e,rot){const c=SOM_COR[e]||'#8b949e';return '<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;border:1px solid '+c+';color:'+c+'">'+somEsc(rot||SOM_ROT[e]||e)+'</span>';}
function somSel(id,opts,val,onch){return '<select id="'+id+'" style="font-size:12px" onchange="somMudou()">'+opts.map(function(o){return '<option value="'+somEsc(o[0])+'"'+(String(o[0])===String(val)?' selected':'')+'>'+somEsc(o[1])+'</option>';}).join('')+'</select>';}
function somShell(){
 document.getElementById('irSombraCorpo').innerHTML=
  '<p style="font-size:12.5px;margin:4px 0 8px">Previsão <b>experimental</b> do candidato IR sombra, comparada com o valor observado que a avalia. '+
  'Não altera o IR operacional nem as recalibrações e não promove nenhum candidato.</p>'+
  '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0">'+
  somSel('somMod',SOM_MOD.map(function(m){return [m,m];}),'Bike')+
  somSel('somDur',SOM_DUR.map(function(d){return [d,d+' s'];}),1200)+
  somSel('somModo',SOM_MODO,'conhecido_na_epoca')+
  '<button type="button" id="somCarregar" onclick="somCarregar()" style="font-size:12px">Carregar seleção</button></div>'+
  '<div id="somAviso" style="font-size:12px;color:#8b949e;margin:2px 0">Escolha a seleção e carregue. Cada carregamento lê o estado atual.</div>'+
  '<div id="somSaida"></div>'+
  '<div style="margin:10px 0 4px"><b style="font-size:12.5px">Registo prospetivo (D+1, 180 s)</b> · <button type="button" onclick="prospCarregar()" style="font-size:12px">Ver registo prospetivo</button></div>'+
  '<div id="somProsp" style="font-size:12px"></div>';
 SOM_MONTADO=true;
}
function somMudou(){
 SOM_SEQ++;                                  // uma resposta antiga nao volta a desenhar-se
 const s=document.getElementById('somSaida'), a=document.getElementById('somAviso');
 if(s)s.innerHTML='';
 if(a)a.textContent='Seleção alterada: carregue para ver a previsão desta seleção.';
}
function abrirIRSombra(d){ if(d&&d.open&&!SOM_MONTADO)somShell(); }
async function somCarregar(){
 const seq=++SOM_SEQ;
 const mod=document.getElementById('somMod').value, dur=document.getElementById('somDur').value, modo=document.getElementById('somModo').value;
 const saida=document.getElementById('somSaida');
 if(saida)saida.innerHTML='<p style="font-size:12px">A carregar...</p>';
 let j=null;
 try{ j=await fetch('/api/modelo/ir/sombra?tipo='+encodeURIComponent(mod)+'&duracao='+encodeURIComponent(dur)+'&modo='+encodeURIComponent(modo)).then(function(r){return r.json();}); }
 catch(e){ j=null; }
 if(seq!==SOM_SEQ)return;                    // resposta de uma seleccao anterior: descartada
 somRender(j,mod);
}
function somCabecalho(v){
 const h=v.historico_temporal||{}, c=v.historico_curvas||{}, cn=v.contagens||{};
 let s='<div style="margin:6px 0">'+somBadge(v.estado,null)+' <b style="font-size:12.5px">'+somEsc(v.rotulo_modo||'')+'</b> · '+somEsc(v.modalidade||'')+' · '+somEsc(v.duracao_s)+' s</div>';
 s+='<p style="font-size:12px;margin:3px 0"><b>Natureza</b>: '+somEsc(v.natureza_rotulo||'—')+(v.nao_validacao_historica?' <span style="color:#d29922">(não é validação histórica)</span>':'')+'</p>';
 if(v.mensagem)s+='<p style="font-size:12px;color:#f85149;margin:3px 0">'+somEsc(v.mensagem)+'</p>';
 if(v.bloqueado)s+='<p style="font-size:12px;color:#d29922;margin:3px 0">Bloqueado: '+somEsc(v.motivo_bloqueio||'histórico insuficiente')+'. Nada foi avaliado.</p>';
 s+='<p style="font-size:12px;margin:3px 0">Histórico temporal: <b>'+somEsc(h.rotulo||'—')+'</b> · Histórico de curvas: <b>'+somEsc(c.rotulo||'—')+'</b></p>';
 // null = nada foi avaliado (histórico indisponível ou bloqueado): mostra-se "—", nunca 0
 const cv=function(x){return x==null?'—':somEsc(x);};
 s+='<p style="font-size:12px;margin:3px 0">Alvos previstos: <b>'+cv(cn.previstos)+'</b> · avaliáveis: <b>'+cv(cn.avaliaveis)+'</b> · rejeitados: <b>'+cv(cn.rejeitados)+'</b>'+
    (cn.previsto_nao_avaliavel?' · previstos sem avaliação possível (pendentes): <b>'+somEsc(cn.previsto_nao_avaliavel)+'</b>':'')+'</p>';
 return s;
}
function somOperacional(mod){
 const it=(typeof IR_J!=='undefined'&&IR_J&&IR_J.modalidades&&IR_J.modalidades[mod])||null;
 if(!it)return '<p style="font-size:12px;color:#8b949e;margin:3px 0">Operacional (180 s): abra o painel do IR operacional para ver o ajuste ativo desta modalidade.</p>';
 const aj=it.ajuste||{}, P=it.parametros||{};
 return '<p style="font-size:12px;margin:3px 0"><b>Operacional</b> (180 s, ajuste v'+somEsc(aj.versao_ajuste==null?'—':aj.versao_ajuste)+' de '+somEsc(String(aj.ajustado_em||'—').slice(0,10))+', P0 '+somNum(P.p0,1)+' W). '+
   'Curva em % do P0 no painel do IR operacional; não é uma previsão por alvo e não entra nesta comparação em W.</p>';
}
function somEvidencia(v){
 // Sempre visivel, com ou sem metricas: a ausencia de evidencia tambem e informacao.
 return '<p style="font-size:12px;margin:3px 0">Suficiência da amostra: <b>'+somEsc((v.suficiencia||{}).rotulo||'sem informacao')+'</b>. '+
   somEsc((v.evidencia_para_revisao||{}).texto||'Sem evidencia suficiente para uma futura revisao do candidato.')+'</p>';
}
function somMetricas(v){
 if(!v.metricas)return '<p style="font-size:12px;color:#8b949e;margin:4px 0">Sem métricas: nenhum alvo avaliável nesta seleção.</p>';
 const pm=v.metricas.por_modelo||{}, ordem=['IR','M0','M1','M2'].filter(function(k){return pm[k];});
 const rot={IR:'<b>IR sombra (experimental)</b>',M0:'M0 média expansiva',M1:'M1 último valor',M2:'M2 nível'};
 let t=(v.nao_validacao_historica?'<p style="font-size:12px;color:#d29922;margin:4px 0">Métricas retrospectivas: valores atuais, não validação temporal.</p>':'')+
  '<table style="font-size:12px;border-collapse:collapse;margin:4px 0"><thead><tr><th style="text-align:left">Modelo</th><th>n</th><th>MAE (W)</th><th>RMSE (W)</th><th>Viés (W)</th></tr></thead><tbody>';
 ordem.forEach(function(k){const m=pm[k]||{}; t+='<tr><td>'+rot[k]+'</td><td style="text-align:right">'+somEsc(m.n)+'</td><td style="text-align:right">'+somNum(m.mae,1)+'</td><td style="text-align:right">'+somNum(m.rmse,1)+'</td><td style="text-align:right">'+somNum(m.erro_medio,1)+'</td></tr>';});
 t+='</tbody></table>';
 const d=v.metricas.delta_mae_ir_menos_referencia||{};
 const per=v.periodo?v.periodo[0]+' a '+v.periodo[1]:'—';
 t+='<p style="font-size:12px;margin:3px 0">Diferença de MAE (IR menos referência): vs M0 '+somNum(d.M0,1)+' W, vs M1 '+somNum(d.M1,1)+' W. Negativo = IR com menor erro. '+
   'Amostra: <b>'+somEsc(v.metricas.n_alvos)+'</b> alvos · período avaliado: <b>'+somEsc(per)+'</b>.</p>';
 return t;
}
function somGrafico(v){
 const ns=((v.serie||{}).sombra||[]).length, nr=((v.serie||{}).referencia_observada||[]).length;
 let msg='';
 if(v.estado==='indisponivel')msg='Sem dados: '+(v.mensagem||'indisponível')+'.';
 else if(v.bloqueado)msg='Bloqueado: nenhuma previsão foi calculada (motivo: '+(v.motivo_bloqueio||'histórico insuficiente')+').';
 else if((v.contagens||{}).previsto_nao_avaliavel)msg=(v.contagens||{}).previsto_nao_avaliavel+' previsão(ões) calculada(s) sem avaliação possível: experimental e pendente, não desenhada.';
 else if(!ns&&!nr)msg='Sem previsões calculadas para esta seleção: nenhuma linha experimental é desenhada.';
 return '<canvas id="somCanvas" width="640" height="240" style="max-width:100%;display:block;margin:6px 0"></canvas>'+
   (msg?'<p style="font-size:12px;color:#d29922;margin:3px 0">'+somEsc(msg)+'</p>':'')+
   '<p style="font-size:11.5px;color:#8b949e;margin:2px 0"><span style="color:#58a6ff">●</span> previsão experimental da sombra (W, '+ns+' pontos) · '+
   '<span style="color:#f0883e">●</span> valor observado usado na avaliação (W, '+nr+' pontos). Não há série operacional em W nesta vista.</p>';
}
function somDesenhar(v){
 const cv=document.getElementById('somCanvas'); if(!cv||!cv.getContext)return;
 const g=cv.getContext('2d'); if(!g)return;
 const W=cv.width, H=cv.height, PL=56, PR=12, PT=12, PB=28;
 const S=((v.serie||{}).sombra)||[], R=((v.serie||{}).referencia_observada)||[];
 g.clearRect(0,0,W,H);
 if(!S.length&&!R.length)return;                // sem previsoes calculadas: nenhuma linha experimental
 const dia=function(s){return Date.parse(s)/86400000;};
 const pts=S.concat(R).map(function(p){return {x:dia(p.data),y:Number(p.valor)};}).filter(function(p){return isFinite(p.x)&&isFinite(p.y);});
 if(!pts.length)return;
 const x0=Math.min.apply(null,pts.map(function(p){return p.x;})), x1=Math.max.apply(null,pts.map(function(p){return p.x;}));
 const y0=Math.min.apply(null,pts.map(function(p){return p.y;})), y1=Math.max.apply(null,pts.map(function(p){return p.y;}))+1e-9;
 const X=function(x){return PL+(x1===x0?0.5:(x-x0)/(x1-x0))*(W-PL-PR);};
 const Y=function(y){return PT+(1-(y-y0)/((y1-y0)||1))*(H-PT-PB);};
 g.strokeStyle='#30363d'; g.lineWidth=1;
 g.beginPath(); g.moveTo(PL,PT); g.lineTo(PL,H-PB); g.lineTo(W-PR,H-PB); g.stroke();
 g.fillStyle='#8b949e';
 g.fillText(String(Math.round(y1))+' W',4,PT+8); g.fillText(String(Math.round(y0))+' W',4,H-PB);
 if(v.periodo){ g.fillText(v.periodo[0],PL,H-8); g.fillText(v.periodo[1],W-PR-70,H-8); }
 const pintar=function(lista,cor,raio){
  g.fillStyle=cor;
  lista.forEach(function(p){const x=dia(p.data), y=Number(p.valor); if(!isFinite(x)||!isFinite(y))return;
   g.beginPath(); g.arc(X(x),Y(y),raio,0,6.283); g.fill();});
 };
 pintar(R,'#f0883e',2.5);                       // referencia observada (y), so onde existe
 pintar(S,'#58a6ff',3.5);                       // previsao experimental da sombra, so onde foi calculada
}
function prospCarregar(){
 const el=document.getElementById('somProsp'); if(!el)return;
 el.innerHTML='<p style="font-size:12px">A carregar registo prospetivo...</p>';
 fetch('/api/modelo/ir/sombra/prospetiva').then(function(r){return r.json();}).then(function(j){prospRender(j);})
  .catch(function(){el.innerHTML='<p style="font-size:12px;color:#f85149">Registo prospetivo indisponível.</p>';});
}
function prospRender(j){
 const el=document.getElementById('somProsp'); if(!el)return;
 if(!j||j.status!=='OK'){el.innerHTML='<p style="font-size:12px;color:#f85149">'+somEsc((j&&j.mensagem)||'Registo prospetivo indisponível.')+'</p>';return;}
 const g=j.registo||{}, sf=g.suficiencia_metricas||{};
 const ROT={migracao_pendente:'migração pendente: nada foi registado',sem_previsoes:'ainda não existem previsões',
  so_bloqueadas:'existem previsões bloqueadas, sem valor',pendentes:'existem previsões pendentes de resultado',
  com_resultados:'existem previsões com resultado associado'};
 let t='<p style="font-size:12px;margin:3px 0">Estado: <b>'+somEsc(ROT[g.estado]||g.estado||'desconhecido')+'</b>. Registado no instante real do cálculo; não é retroativo.</p>';
 if(g.estado==='migracao_pendente'){el.innerHTML=t;return;}     // sem a migracao nao ha contagens: nao se mostra 0
 t+='<p style="font-size:12px;margin:3px 0">Previsões: <b>'+somEsc(g.previsoes||0)+'</b> · calculadas: '+somEsc(g.calculadas||0)+
  ' · bloqueadas: '+somEsc(g.bloqueadas||0)+' · pendentes: '+somEsc(g.pendentes_de_resultado||0)+
  ' (vencidas sem resultado: '+somEsc(g.vencidas_sem_resultado||0)+') · com resultado: '+somEsc(g.com_resultado||0)+
  ' · revisões de resultado: '+somEsc(g.revisoes_de_resultado||0)+'</p>';
 if(g.motivos_bloqueio&&Object.keys(g.motivos_bloqueio).length){
  t+='<p style="font-size:11.5px;color:#8b949e;margin:2px 0">Motivos de bloqueio: '+Object.keys(g.motivos_bloqueio).map(function(k){return somEsc(k)+' ('+g.motivos_bloqueio[k]+')';}).join(', ')+'</p>';}
 const m=g.metricas;
 t+= m ? '<p style="font-size:12px;margin:3px 0">Erro prospetivo: MAE '+somNum(m.mae,1)+' W, erro médio '+somNum(m.erro_medio,1)+' W (n='+somEsc(m.n)+', '+somEsc(m.periodo[0])+' a '+somEsc(m.periodo[1])+').</p>'
   : '<p style="font-size:12px;color:#8b949e;margin:3px 0">Métricas: dados insuficientes (n='+somEsc(sf.n||0)+'; são necessários 20 alvos, 15 datas e 90 dias).</p>';
 el.innerHTML=t;
}
function somLimitacoes(v){
 const L=(v.limitacoes||[]).concat(v.avisos||[]);
 return L.map(function(t){return '<p style="font-size:11.5px;color:#8b949e;margin:2px 0">'+somEsc(t)+'</p>';}).join('');
}
function somLacunas(v){
 const L=v.lacunas||[], pm=v.rejeicoes_por_motivo||{}, pc=v.rejeicoes_por_categoria||{};
 if(!L.length)return '';
 const cats=Object.keys(pc).map(function(k){return somEsc(k)+' '+pc[k];}).join(' · ');
 const linhas='<li>Categorias: '+(cats||'&mdash;')+'</li>'+Object.keys(pm).map(function(k){return '<li>'+somEsc(k)+': <b>'+pm[k]+'</b></li>';}).join('');
 const det=L.slice(0,20).map(function(p){return somEsc(p.data)+' ('+somEsc(p.motivo||'—')+')';}).join(', ');
 return '<details style="margin:4px 0"><summary style="font-size:12px;cursor:pointer">Lacunas: '+L.length+' alvo(s) sem previsão avaliável</summary>'+
   '<ul style="font-size:12px;margin:3px 0 3px 18px">'+linhas+'</ul><p style="font-size:11.5px;color:#8b949e;margin:2px 0">'+det+(L.length>20?' …':'')+'</p></details>';
}
function somRender(j,mod){
 const saida=document.getElementById('somSaida'); if(!saida)return;
 const v=(j&&j.vista)||{estado:'indisponivel',origem:'pedido_falhou',mensagem:(j&&j.mensagem)||'Pedido de validação indisponível.',modalidade:mod,duracao_s:null,modo:null};
 const a=document.getElementById('somAviso'); if(a)a.textContent='';
 saida.innerHTML=somCabecalho(v)+somOperacional(v.modalidade||mod)+somEvidencia(v)+somMetricas(v)+somGrafico(v)+somLacunas(v)+somLimitacoes(v);
 somDesenhar(v);
}
// <</ir-sombra>>



async function montarExport(){
 let d;
 try{ d=await fetch('/api/export').then(r=>r.json()); }
 catch(e){ return; }
 const el=document.getElementById('exportBox'); if(!el)return;
 const c=d.ficheiros||d.conjuntos||{};
 el.innerHTML='<div class="wrap"><table><thead><tr>'+
  ['Conjunto','O que tem','CSV','JSON'].map(x=>'<th>'+x+'</th>').join('')+
  '</tr></thead><tbody>'+
  Object.keys(c).map(k=>
   '<tr><td><b>'+k+'</b></td>'+
   '<td style="font-size:12px;color:#8b949e">'+c[k]+'</td>'+
   '<td><a href="/api/export/'+k+'.csv">descarregar</a></td>'+
   '<td><a href="/api/export/'+k+'.json" target="_blank">ver</a></td></tr>'
  ).join('')+'</tbody></table></div>'+
  (d.tudo?'<div class="sub" style="margin-top:8px">'+
   '<a href="/api/export/tudo.json" target="_blank">tudo num so ficheiro</a> — '+
   d.tudo.split('—')[1]+'</div>':'');
}

async function load(){
 let d;
 try{ d=await fetch('/api/pmc').then(r=>r.json()); }
 catch(e){ document.getElementById('sub').innerHTML=
   '<span class="err">Nao consegui carregar</span>'; return; }
 if(d.error){ document.getElementById('sub').innerHTML=
   '<span class="err">'+d.error+'</span>'; return; }
 D=d;

 const s=d.serie||[];
 document.getElementById('sub').textContent=
  s.length+' dias, de '+(s[0]||{}).date+' a '+(s[s.length-1]||{}).date;

 const a=d.actual||{},e=a.estado||{};
 document.getElementById('kpis').innerHTML=[
  ['CTL (fitness)',a.ctl,'#5DADE2'],
  ['ATL (fadiga)',a.atl,'#E74C3C'],
  ['TSB (forma)',a.tsb,e.cor||'#2ECC71'],
  ['Estado',e.label||'—',e.cor||'#8b949e'],
  ['Ramp 7d',a.ramp,(a.ramp>8?'#E67E22':'#5DADE2')],
  ['Sessoes',(d.sessoes||[]).length,'#5DADE2']
 ].map(k=>'<div class="card"><div class="label">'+k[0]+'</div>'+
  '<div class="value" style="color:'+k[2]+'">'+(k[1]==null?'—':k[1])+'</div></div>').join('');

 document.getElementById('alertas').innerHTML=(d.alertas||[]).map(function(al){
  const c=al.nivel==='aviso'?'#E67E22':'#5DADE2';
  return '<div style="border-left:3px solid '+c+';background:#161b22;'+
   'padding:9px 12px;margin-bottom:8px;border-radius:0 6px 6px 0;font-size:13px">'+
   al.texto+'</div>';}).join('');

 // so' redesenha, com as cores de D; sem novo pedido. Isolado: uma falha so' no
 // painel de sugestoes (ex.: payload malformado) nao pode impedir o resto de load().
 try{ mostrarSugestoes(); }catch(e){ console.error('sugestoes:',e); }
montarExport();
 drawPMC(); mostrarFMT5(); mostrarRegimeHoje();
 try{mostrarEvolucao();}catch(e){console.error('Evolução da performance indisponível nesta carga:',e);}
 mostrarAlos();
 mostrarDtrimpDkj(); mostrarEficienciaKj();
 redesenhar();  // drawCTLg/drawFMT so' correm daqui -- sem isto, o
                 // grafico de Curvatura ficava em branco ate' o
                 // primeiro resize (ex.: abrir o F12) disparar
                 // window.addEventListener('resize',redesenhar)

 // ── fase actual, com ΔCTLγ e HRV em sigma ──
 const F=d.ftlm;
 if(d.erro_ftlm){
  document.getElementById('faseCard').innerHTML=
   '<div class="err" style="margin-bottom:10px">FTLM: '+d.erro_ftlm+'</div>';
 } else if(F&&F.fase_actual){
  const fa=F.fase_actual, fg=F.fase_global;
  const seta=(fa.dctlg>0?'&uarr;':'&darr;');
  const dv=fa.dctlg==null?'—':Math.abs(fa.dctlg).toFixed(4)+'/d';
  const hz=fa.hrv_z==null?'':' | HRV '+(fa.hrv_z>=0?'+':'')+fa.hrv_z.toFixed(2)+'&sigma;';
  let html='<div style="background:'+hexRgba(fa.cor,0.10)+';border-left:4px solid '+
   fa.cor+';padding:9px 14px;border-radius:0 5px 5px 0;margin-bottom:8px">'+
   '<b>Fase actual (carga agregada):</b> '+fa.label+' — '+fa.desc+'<br>'+
   '<small style="color:#8b949e">'+fa.dias+'d nesta fase | &Delta;CTL&gamma; '+
   seta+dv+hz+
   (fa.modalidades_incluidas?' | soma de '+fa.modalidades_incluidas.join(', '):'')+
   '</small></div>';
  if(fg&&fg.codigo!==fa.codigo){
   const ctb=Object.keys(fg.contribuicoes||{})
     .map(m=>m+' '+Math.round(fg.contribuicoes[m]*100)+'%').join(' · ');
   const pm=fg.fases_por_modalidade||{};
   const leg=F.fases_legenda||{};
   const det=Object.keys(pm).map(m=>m+': '+((leg[pm[m]]||{}).label||pm[m])).join(' · ');
   html+='<div style="background:'+hexRgba(fg.cor,0.10)+';border-left:4px solid '+
    fg.cor+';padding:9px 14px;border-radius:0 5px 5px 0;margin-bottom:8px">'+
    '<b>Fase global ponderada (por CTL&gamma;):</b> '+fg.label+'<br>'+
    '<small style="color:#8b949e">peso: '+ctb+'<br>'+det+'</small></div>';}
  else if(fg){
   const pm=fg.fases_por_modalidade||{};
   const leg=F.fases_legenda||{};
   const det=Object.keys(pm).map(m=>m+': '+((leg[pm[m]]||{}).label||pm[m])).join(' · ');
   if(det)html+='<div style="font-size:12px;color:#8b949e;margin:-4px 0 10px">'+
    'Por modalidade: '+det+'</div>';}
  document.getElementById('faseCard').innerHTML=html;

  const g=F.gammas||{};
  document.getElementById('subPMC').innerHTML=
   'CTL 42d e ATL 7d no eixo esquerdo &middot; CTL&gamma; no eixo direito &middot; '+
   'carga diaria empilhada por modalidade em baixo<br>'+
   '<span style="font-size:12px">Kernel Riemann-Liouville: CTL&gamma;(t) = '+
   '&Sigma; Load(t&minus;k)&middot;k<sup>&gamma;&minus;1</sup>/&Gamma;(&gamma;) &middot; '+
   '&gamma;<sub>perf</sub> '+gammaTexto(g.perf)+
   ' &middot; &gamma;<sub>rec</sub> '+gammaTexto(g.rec)+
   (D.cp_fonte&&D.cp_fonte.da_curva
    ? '<br>CP de '+D.cp_fonte.da_curva+' sessoes ajustado a P(t)=W&prime;/t+CP '+
      '(2-20min, R&sup2;&ge;0.80)' : '')+'</span>';

  drawCTLg(); tabelaGammas(); drawFMT();
  const fm=F.fmt||{};
  // As dimensoes CTLg_perf e CTLg_rec usam o gamma aplicado. Quando e o padrao,
  // o kappa herda essa escolha: dizemos isso aqui, sem alterar o calculo.
  const gFmt=F.gammas||{};
  const herdados=[['perf',gFmt.perf],['rec',gFmt.rec]]
   .filter(x=>x[1]&&x[1].origem==='padrao');
  const notaPadrao=herdados.length?'<br><span style="color:#E67E22">'+
   herdados.map(x=>'CTLg_'+x[0]+' com γ padrão '+x[1].gamma).join(' e ')+
   ' (ajuste não aceito): κ herda esse γ.</span>':'';
  document.getElementById('subFMT').innerHTML=
   '&kappa;(t) = trace(cov(&Delta;x)) em janela de 28d sobre '+
   (fm.dimensoes||[]).length+' dimensoes: '+(fm.dimensoes||[]).join(', ')+
   '. &kappa; alto = sistema a oscilar mais.'+notaPadrao;
 }

}
['verFTLM','verFases','escCTLgPMC'].forEach(id=>
 document.getElementById(id).onchange=function(){if(D)drawPMC();});
['homeoMod','homeoVista','homeoSuav','homeoMods','haIni','haFim','hrIni','hrFim'].forEach(id=>
 document.getElementById(id).onchange=function(){if(D)drawHomeo();});
['evolMod','evolJanela','evolFonte'].forEach(id=>
 document.getElementById(id).onchange=function(){if(D)mostrarEvolucao();});
document.getElementById('canalFMT').onchange=function(){if(D&&D.fmt)drawAtencao();};
// ── CTL vs KJ — coeficientes dTRIMP/dkJ e eficiência por kJ ──────────────────
// Dados vêm de D.dtrimp_dkj e D.eficiencia_kj (calculados em pmc.py, sem
// alteração aqui). Só mostramos o que o backend devolveu; modalidades sem
// resultado recebem uma mensagem explícita, nunca um valor inventado.
const CTLKJ_MIN_TXT='mínimo 8 sessões com RPE e kJ, duração ≥ 10 min';
function ctlkjEsc(v){ return String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function ctlkjNum(v,dec){ return (v==null||!isFinite(v))?'—':Number(v).toFixed(dec); }
function ctlkjNomeTipo(t){
 const nomes={todos:'Todos os tipos',base:'Base (RPE ≤ 5)',tempo:'Tempo (RPE 6–7)',intervalado:'Intervalado (RPE ≥ 8)'};
 return nomes[t]||t;
}
function ctlkjMostrarSubtab(k){
 const coef=(k==='coef');
 document.getElementById('ctlkjCoefBox').style.display=coef?'':'none';
 document.getElementById('ctlkjEfBox').style.display=coef?'none':'';
 const bc=document.getElementById('ctlkjBtnCoef'), be=document.getElementById('ctlkjBtnEf');
 bc.style.background=coef?'#1c2331':'#161b22'; bc.style.border='1px solid '+(coef?'#5DADE2':'#30363d'); bc.style.color=coef?'#5DADE2':'#8b949e';
 be.style.background=coef?'#161b22':'#1c2331'; be.style.border='1px solid '+(coef?'#30363d':'#5DADE2'); be.style.color=coef?'#8b949e':'#5DADE2';
 bc.setAttribute('aria-pressed',coef?'true':'false'); be.setAttribute('aria-pressed',coef?'false':'true');
}
function mostrarDtrimpDkj(){
 const head=document.getElementById('ctlkjCoefHead'), body=document.getElementById('ctlkjCoefBody');
 if(!head||!body) return;
 const cols=['Modalidade','Tipo de sessão','n','dTRIMP/dkJ','R²','Coef. densidade','kJ médio','TRIMP médio'];
 head.innerHTML=cols.map(function(c){ return '<th>'+ctlkjEsc(c)+'</th>'; }).join('');
 const dados=D.dtrimp_dkj;
 if(dados===undefined||dados===null){
  body.innerHTML='<tr><td colspan="8" style="color:#8b949e;">Dados de dTRIMP/dkJ ausentes na resposta.</td></tr>'; return; }
 const mods=(D.ciclicos||Object.keys(dados));
 const com=mods.filter(function(m){ return Array.isArray(dados[m])&&dados[m].length; });
 const sem=mods.filter(function(m){ return !(Array.isArray(dados[m])&&dados[m].length); });
 if(!com.length){
  body.innerHTML='<tr><td colspan="8" style="color:#8b949e;">Sem resultado: nenhuma modalidade tem amostras suficientes ('+CTLKJ_MIN_TXT+').</td></tr>'; return; }
 let html='';
 com.forEach(function(m){
  dados[m].forEach(function(l){
   html+='<tr><td>'+ctlkjEsc(m)+'</td><td>'+ctlkjEsc(ctlkjNomeTipo(l.tipo))+'</td><td>'+ctlkjEsc(l.n)+'</td>'
    +'<td>'+ctlkjNum(l.dtrimp_dkj,4)+'</td><td>'+ctlkjNum(l.r2,3)+'</td><td>'+ctlkjNum(l.coef_densidade,3)+'</td>'
    +'<td>'+ctlkjNum(l.kj_medio,0)+'</td><td>'+ctlkjNum(l.trimp_medio,1)+'</td></tr>';
  });
 });
 if(sem.length) html+='<tr><td colspan="8" style="color:#8b949e;">Sem resultado para: '+ctlkjEsc(sem.join(', '))+' ('+CTLKJ_MIN_TXT+' — ou dados ausentes).</td></tr>';
 body.innerHTML=html;
}
function mostrarEficienciaKj(){
 const box=document.getElementById('ctlkjEfCards'); if(!box) return;
 const dados=D.eficiencia_kj;
 if(dados===undefined||dados===null){
  box.innerHTML='<div class="sub">Dados de eficiência por kJ ausentes na resposta.</div>'; return; }
 const mods=(D.ciclicos||Object.keys(dados));
 const com=mods.filter(function(m){ return !!dados[m]; });
 const sem=mods.filter(function(m){ return !dados[m]; });
 if(!com.length){
  box.innerHTML='<div class="sub">Sem resultado: nenhuma modalidade tem amostras suficientes (mínimo 10 sessões com RPE e kJ, 8 após o corte dos extremos de 5 %).</div>'; return; }
 let html=com.map(function(m){
  const e=dados[m];
  const aviso=e.desactualizado&&e.aviso?'<div style="color:#E67E22;font-size:12px;margin-top:6px;">'+ctlkjEsc(e.aviso)+'</div>':'';
  return '<div class="card" style="min-width:220px;">'
   +'<div class="label">'+ctlkjEsc(m)+' · '+ctlkjEsc(e.n_sessoes)+' sessões</div>'
   +'<div class="value">'+ctlkjNum(e.eff_actual,3)+'</div>'
   +'<div class="sub">TRIMP/kJ atual (média rolling de 4 semanas)</div>'
   +'<div class="sub">Mediana histórica: '+ctlkjNum(e.eff_historica,3)+' · tendência: '+ctlkjEsc(e.tendencia)+'</div>'
   +'<div class="sub">Última sessão com kJ e RPE: '+ctlkjEsc(e.ultima_data)+'</div>'
   +aviso+'</div>';
 }).join('');
 if(sem.length) html+='<div class="sub" style="width:100%;">Sem resultado para: '+ctlkjEsc(sem.join(', '))+' ('+CTLKJ_MIN_TXT+' — ou dados ausentes).</div>';
 box.innerHTML=html;
}

// ── Evolucao da performance: FTP/eFTP e melhores esforcos (pmc.evolucao_performance)
// Mostra o que o backend devolveu. Ausencia nunca vira zero; sem dados = aviso explicito.
const EV_MOTIVOS={sem_dados:'sem observacoes nas janelas',
 observacoes_insuficientes:'menos de 10 observacoes validas numa das janelas',
 cobertura_insuficiente:'cobertura temporal abaixo de 70% numa das janelas'};
function evEsc(v){return String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function evV(v,dec){return (v===null||v===undefined||!isFinite(v))?'—':Number(v).toFixed(dec);}
function evCabecalho(){return ['Série','Mediana anterior','Mediana recente','Δ W','Δ %','Obs. ant. / rec.','Semanas rec. / exigidas','Estado'];}
function evLinha(rotulo,s,jan){
 if(!s||!s.janelas)return '<tr><td>'+evEsc(rotulo)+'</td><td colspan="7">'+
  (s?'janelas não informadas nesta resposta.':'série indisponível nesta resposta.')+'</td></tr>';
 const w=(s.janelas&&s.janelas[jan])||{};
 const ok=w.status==='ok';
 const estado=ok?'<span style="color:#2ECC71">ok</span>':
  '<span class="err">Dados insuficientes: '+evEsc((w.motivos||[]).map(m=>EV_MOTIVOS[m]||m).join('; '))+'</span>';
 const cells=[evEsc(rotulo),
  ok?evV(w.mediana_anterior,1):'—',
  ok?evV(w.mediana_recente,1):'—',
  ok?evV(w.diferenca,1):'—',
  (ok&&w.variacao_pct!==null&&w.variacao_pct!==undefined)?evV(w.variacao_pct,1)+'%':'—',
  evEsc((w.n_anterior||0)+' / '+(w.n_recente||0)),
  evEsc((w.semanas_recente||0)+' / '+(w.semanas_necessarias||'—')),
  estado];
 return '<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>';
}
function evNotaQualidade(rotulo,s){
 if(!s||!s.qualidade)return '<div class="sub"><b>'+evEsc(rotulo)+':</b> '+
  (s?'qualidade não informada nesta resposta.':'série indisponível nesta resposta.')+'</div>';
 const q=s.qualidade;
 const p=[(q.n_validos||0)+' válidos'];
 if(q.n_zero)p.push(q.n_zero+' zero(s) tratados como ausência');
 if(q.n_ausentes)p.push(q.n_ausentes+' ausente(s)');
 if(q.n_negativos)p.push(q.n_negativos+' negativo(s)');
 if(q.n_excluidos_tendencia)p.push(q.n_excluidos_tendencia+' excluído(s) da tendência (padrão confirmado com evidência)');
 (q.sequencias_identicas||[]).forEach(b=>p.push('sequência de '+b.n+' valores iguais ('+b.valor+') de '+b.inicio+' a '+b.fim+
  ' ('+b.dias+' dias): '+(b.classificacao==='padrao_confirmado'?'padrão confirmado, excluída':'suspeita, origem não confirmada, mantida e sinalizada')));
 if((q.valores_suspeitos||[]).length)p.push(q.valores_suspeitos.length+' valor(es) suspeito(s) ('+evEsc(q.valores_suspeitos[0].motivo)+'), mantido(s) e sinalizado(s): medianas podem não ser confiáveis');
 if((q.outliers_sinalizados||[]).length)p.push(q.outliers_sinalizados.length+' valor(es) extremo(s) sinalizado(s), não removidos');
 if(q.idade_dias!==null&&q.idade_dias!==undefined)p.push('última observação há '+q.idade_dias+' dias');
 return '<div class="sub"><b>'+evEsc(rotulo)+':</b> '+evEsc(p.join(' · '))+
  (s&&s.semantica?'<br><i>Origem: '+evEsc(s.semantica)+'</i>':'')+'</div>';
}
// ── Graficos da evolucao: um ponto por atividade, medianas de janela, sem interpolacao
const EV_COR={ponto:'#3a78b5',sinal:'#c4691f',mediana:'#e6edf3',ausente:'#8b949e',grade:'#21262d',eixo:'#8b949e',texto:'#e6edf3'};
const EV_SINAL={sequencia:'sequencia de valores iguais',suspeito:'escala suspeita',extremo:'valor extremo',excluido:'excluido da tendencia'};
function evDia(s){return Date.parse(String(s).slice(0,10)+'T00:00:00Z')/86400000;}
function evFundo(){const b=getComputedStyle(document.body).backgroundColor;
 return (b&&b!=='rgba(0, 0, 0, 0)'&&b!=='transparent')?b:'#0d1117';}
// eFTP da atividade: resumo textual com os campos ja calculados pelo backend (janelas[jan]).
// Nao recalcula medianas nem diferencas; so formata e classifica o sinal da diferenca ja fornecida.
function evResumoEftp(s,jan){
 const cab='<div class="sub" id="evEftpResumo"><b>eFTP da atividade (icu_pm_ftp)</b> · janela de '+evEsc(jan)+' dias';
 if(!s)return '<div class="sub">'+cab+'<br>Série indisponível nesta resposta.</div>';
 const w=(s.janelas&&s.janelas[jan])||{};
 if(w.status!=='ok'){
  const motivos=(w.motivos||[]).map(m=>EV_MOTIVOS[m]||m).join('; ');
  return '<div class="sub">'+cab+'<br><span class="err">Dados insuficientes para comparar</span>'+
   (motivos?' ('+evEsc(motivos)+')':'')+'.</div>';}
 const d=w.diferenca;
 const dTxt=(d===null||d===undefined)?'—':((d>0?'+':'')+Number(d).toFixed(1)+' W');
 const arred=(d===null||d===undefined)?null:Number(d).toFixed(1);
 const estado=(arred===null)?'—':((arred==='0.0'||arred==='-0.0')?'estabilidade':(d>0?'subida':'descida'));
 const pct=(w.variacao_pct===null||w.variacao_pct===undefined)?'—':((w.variacao_pct>0?'+':'')+Number(w.variacao_pct).toFixed(1)+'%');
 return '<div class="sub">'+cab+'<br>'+
  'Mediana anterior: <b>'+evV(w.mediana_anterior,1)+' W</b>'+evPerTxt(w.periodo_anterior)+'<br>'+
  'Mediana recente: <b>'+evV(w.mediana_recente,1)+' W</b>'+evPerTxt(w.periodo_recente)+'<br>'+
  'Diferença: <b>'+dTxt+'</b> ('+pct+') · estado: <b>'+estado+'</b>.<br>'+
  '<span class="evmut">Comparação entre duas medianas. Descreve o que mudou entre os períodos; não identifica causa.</span></div>';
}
function evPerTxt(q){const p=evPeriodo(q);return p?' ('+evEsc(p.join(' a '))+')':'';}
function evTabelaEftp(s){
 const q=(s&&s.qualidade)||{};
 const pts=(s&&Array.isArray(s.pontos))?s.pontos:[];
 const linhas=pts.map(p=>'<tr><td>'+evEsc(p.data)+'</td><td>'+(p.valor===null?'—':evEsc(Math.round(p.valor)+' W'))+
  '</td><td>'+evEsc(p.estado)+'</td><td>'+evEsc(evRotuloSinais(p))+'</td></tr>').join('');
 return '<div class="evmut">'+evEsc((q.n_validos||0)+' válidos')+' · '+evEsc((q.n_ausentes||0)+' ausentes')+' · '+
  evEsc((q.n_zero||0)+' zeros')+'</div>'+
  '<div class="wrap"><table><thead><tr><th>Data</th><th>Valor</th><th>Estado</th><th>Sinalização</th></tr></thead><tbody>'+linhas+'</tbody></table></div>';
}

function evRotuloSinais(p){return (p.sinalizacoes||[]).map(s=>EV_SINAL[s]||s).join(', ');}
function evPeriodo(p){return (Array.isArray(p)&&p.length===2&&p[0]&&p[1])?p:null;}
// ── Melhores esforcos: grafico comparativo em variacao percentual (Δ% vs mediana anterior)
// Denominador: mediana_anterior do backend, mesma duracao e janela. Nunca a mediana recente.
// Sem mediana anterior valida (ausente, zero, negativa ou nao finita): serie indisponivel.
const EV_DUR=[
 {k:'60',rot:'1 min',cor:'#3987e5',forma:'circ'},
 {k:'300',rot:'5 min',cor:'#d95926',forma:'quad'},
 {k:'720',rot:'12 min',cor:'#199e70',forma:'tri'},
 {k:'1200',rot:'20 min',cor:'#c98500',forma:'dia'}];
const EV_NORM_CONTROLE='#f0efec';
const EV_OCULTAS={};
let EV_ULTIMO_NORM=null;
function evMedAnt(s,jan){
 if(!s)return {ok:false,motivo:'série indisponível nesta resposta'};
 const w=(s.janelas&&s.janelas[jan])||null;
 if(!w)return {ok:false,motivo:'janela '+jan+' dias não informada'};
 if(w.status!=='ok')return {ok:false,motivo:'dados insuficientes nesta janela ('+((w.motivos||[]).map(m=>EV_MOTIVOS[m]||m).join('; ')||'sem motivo informado')+')'};
 const m=w.mediana_anterior;
 if(m===null||m===undefined)return {ok:false,motivo:'mediana anterior ausente'};
 if(typeof m!=='number'||!isFinite(m))return {ok:false,motivo:'mediana anterior inválida'};
 if(m===0)return {ok:false,motivo:'mediana anterior igual a zero'};
 if(m<0)return {ok:false,motivo:'mediana anterior negativa'};
 return {ok:true,valor:m,motivo:''};
}
function evDeltaPct(p,m){return 100*(p/m-1);}
function evSerieNorm(s,jan){
 const med=evMedAnt(s,jan);
 const w=(s&&s.janelas&&s.janelas[jan])||{};
 const recDelta=(med.ok&&typeof w.mediana_recente==='number'&&isFinite(w.mediana_recente))?evDeltaPct(w.mediana_recente,med.valor):null;
 const brutos=(s&&Array.isArray(s.pontos))?s.pontos:[];
 const pontos=brutos.filter(p=>p&&typeof p==='object').map(p=>{
  const valido=p.estado==='valido'&&typeof p.valor==='number'&&isFinite(p.valor)&&p.valor>0;
  return {data:String(p.data||''),id:p.id,estado:String(p.estado||''),
   valor:valido?p.valor:null,
   sinal:Array.isArray(p.sinalizacoes)?p.sinalizacoes:[],
   delta:(med.ok&&valido)?evDeltaPct(p.valor,med.valor):null};
 });
 return {ok:med.ok,motivo:med.motivo,med:med.ok?med.valor:null,recDelta:recDelta,
  periodoRec:evPeriodo(w.periodo_recente),pontos:pontos,
  n:{ant:(w.n_anterior==null?null:w.n_anterior),rec:(w.n_recente==null?null:w.n_recente)}};
}
function evPasso(span){const b=Math.max(span,1e-9)/4, m=Math.pow(10,Math.floor(Math.log10(b))), r=b/m;
 return (r<=1?1:r<=2?2:r<=5?5:10)*m;}
function evForma(g,f,x,y,r){
 g.beginPath();
 if(f==='circ'){g.arc(x,y,r,0,2*Math.PI);}
 else if(f==='quad'){g.rect(x-r,y-r,2*r,2*r);}
 else if(f==='tri'){g.moveTo(x,y-r*1.2);g.lineTo(x+r*1.1,y+r*0.8);g.lineTo(x-r*1.1,y+r*0.8);g.closePath();}
 else {g.moveTo(x,y-r*1.25);g.lineTo(x+r*1.25,y);g.lineTo(x,y+r*1.25);g.lineTo(x-r*1.25,y);g.closePath();}
 g.fill();
}
function evMarcadorSvg(f,cor){
 const forma={circ:'<circle cx="6" cy="6" r="4"/>',quad:'<rect x="2" y="2" width="8" height="8"/>',
  tri:'<polygon points="6,1.5 10.5,9.5 1.5,9.5"/>',dia:'<polygon points="6,1 11,6 6,11 1,6"/>'}[f]||'';
 return '<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style="vertical-align:middle;margin-right:4px;fill:'+cor+'">'+forma+'</svg>';
}
function evTextoPonto(h){
 const p=h.p, sg=p.delta>=0?'+':'';
 let t=h.rot+' · '+p.data+'\n'+Math.round(p.valor)+' W · '+sg+p.delta.toFixed(1)+'% vs mediana anterior ('+h.med.toFixed(1)+' W)';
 if(p.sinal.length)t+='\n'+p.sinal.map(x=>EV_SINAL[x]||x).join(', ');
 const fe=evAjusteEm(h.aj,evDia(p.data));
 t+='\n'+(fe===null?'tendência: sem ajuste nesta data':'tendência estimada: '+(fe>=0?'+':'')+fe.toFixed(1)+'% (estimativa, não observação)');
 return t;
}
// ── Tendencia: regressao local linear por duracao (ver auditoria, EV_AJ) ──
// Ajuste independente por duracao, sobre os pontos validos do historico (730 dias)
// e sem os marcados 'excluido' (sequencia confirmada, a mesma regra da tendencia do backend).
// Linha = regressao local linear com pesos tricubo, raio de EV_AJ.H dias.
// Banda = +/-1 DP LOCAL dos residuos em torno da linha (mesmos pesos). E dispersao
// dos pontos, NAO intervalo de confianca nem de predicao.
// Sem linha onde ha menos de EV_AJ.NMIN observacoes no raio (nao extrapola).
const EV_AJ={H:90,NMIN:8,PASSO:7};
// Suporte unilateral (apenas apresentacao): menos de EV_LADO_MIN observacoes de um dos lados, dentro de +-H.
// Metade do minimo de ajuste; nao altera a estimativa, so sinaliza que o trecho depende de um lado.
const EV_LADO_MIN=EV_AJ.NMIN/2;
function evTricubo(d,H){const u=Math.abs(d)/H;return u<1?Math.pow(1-u*u*u,3):0;}
function evLLFit(t0,T,Y,excl){
 let n=0,sw=0,sx=0,sxx=0,sy=0,sxy=0;
 for(let i=0;i<T.length;i++){if(i===excl)continue;const d=T[i]-t0,w=evTricubo(d,EV_AJ.H);if(w<=0)continue;
  n++;sw+=w;sx+=w*d;sxx+=w*d*d;sy+=w*Y[i];sxy+=w*d*Y[i];}
 if(n<EV_AJ.NMIN)return null;
 const det=sw*sxx-sx*sx; if(!(det>1e-9*sw*sw))return null;
 return {yhat:(sxx*sy-sx*sxy)/det,n:n};
}
function evObsAjuste(norm){
 return (norm&&norm.ok?norm.pontos:[]).filter(p=>p.delta!==null&&p.sinal.indexOf('excluido')<0)
  .map(p=>({t:evDia(p.data),y:p.delta,id:p.id})).sort((a,b)=>a.t-b.t);
}
function evAjusteDur(obs){
 const out={ok:false,motivo:'',n_obs:obs.length,T:[],Y:[],grade:[],banda:0,curva:0};
 if(obs.length<EV_AJ.NMIN){out.motivo='menos de '+EV_AJ.NMIN+' observações válidas no histórico';return out;}
 const T=obs.map(o=>o.t),Y=obs.map(o=>o.y); out.T=T; out.Y=Y;
 const R=T.map((t,i)=>{const f=evLLFit(t,T,Y);return f?Y[i]-f.yhat:null;});
 for(let g=Math.ceil(T[0]);g<=T[T.length-1];g+=EV_AJ.PASSO){
  const f=evLLFit(g,T,Y);
  if(!f){out.grade.push({t:g,y:null,sd:null,lado:null,nL:0,nR:0});continue;}
  let k=0,sw2=0,ss=0;
  for(let i=0;i<T.length;i++){if(R[i]===null)continue;const w=evTricubo(T[i]-g,EV_AJ.H);if(w<=0)continue;k++;sw2+=w;ss+=w*R[i]*R[i];}
  const sd=(k>=EV_AJ.NMIN&&sw2>0)?Math.sqrt(ss/sw2):null;
  let nL=0,nR=0;
  for(let i=0;i<T.length;i++){const d=T[i]-g; if(evTricubo(d,EV_AJ.H)<=0)continue; if(d<0)nL++; else if(d>0)nR++;}
  const lado=(nR<EV_LADO_MIN&&nL<EV_LADO_MIN)?'ambos':(nR<EV_LADO_MIN?'direita':(nL<EV_LADO_MIN?'esquerda':null));
  out.grade.push({t:g,y:f.yhat,sd:sd,lado:lado,nL:nL,nR:nR});
  if(sd!==null)out.banda++;
 }
 out.curva=out.grade.filter(q=>q.y!==null).length;
 out.ok=out.curva>0;
 if(!out.ok)out.motivo='sem ponto com '+EV_AJ.NMIN+' observações em ±'+EV_AJ.H+' dias';
 return out;
}
function evAjusteEm(a,t){const f=(a&&a.ok)?evLLFit(t,a.T,a.Y):null;return f?f.yhat:null;}
function evRgba(hex,al){const n=parseInt(hex.slice(1),16);return 'rgba('+((n>>16)&255)+','+((n>>8)&255)+','+(n&255)+','+al+')';}
// ── Melhores esforcos: resumo, grafico conjunto e auditoria (refino visual) ──
// Sensibilidade (diagnostico, nao altera a tendencia oficial): retira-se UMA observacao
// que influencia o ponto mais recente da curva. Criterio explicito: a estimativa e
// "sensivel" se a retirada desloca o ponto em pelo menos 1 DP local dos residuos
// (a propria dispersao tipica de uma observacao em torno da tendencia). Limite de 1 DP
// e convencao documentada, nao um ajuste estatistico.
let EV_AUDIT_SRC=null;
const EV_CSS_GRAF='<style>'+
 '.evgrid{display:flex;flex-direction:column;gap:2px;font-size:12px;margin:6px 0}'+
 '.evrow{display:grid;grid-template-columns:118px 1.4fr 1.3fr 1.8fr;gap:8px;align-items:baseline;padding:5px 0;border-bottom:1px solid #21262d}'+
 '.evhead{color:#8b949e;font-size:11px;border-bottom:1px solid #30363d}'+
 '.evmut{color:#8b949e;font-size:11px}'+
 '.evspan{grid-column:span 4}'+
 '.evchart{position:relative;margin-top:8px}'+
 '#ev_norm{width:100%;height:290px;display:block}'+
 '.evleg{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}'+
 '.evtip{position:absolute;pointer-events:none;display:none;white-space:pre-line;background:#161b22;border:1px solid #30363d;color:#e6edf3;padding:4px 8px;font-size:12px;border-radius:4px;max-width:230px}'+
 '#ev_norm:focus-visible,.evleg button:focus-visible{outline:2px solid #58a6ff;outline-offset:2px}'+
 '@media (max-width:560px){#ev_norm{height:250px}.evhead{display:none}'+
 '.evrow{grid-template-columns:1fr 1fr;gap:6px 10px}.evd{grid-column:1/-1}.evspan{grid-column:1/-1}.evrow .evc:last-child{grid-column:1/-1}'+
 '.evc::before{content:attr(data-l);display:block;color:#8b949e;font-size:10px}}'+
 '</style>';
const EV_METODO='Δ% = 100 × (valor da atividade ÷ mediana anterior − 1), na mesma duração e janela, com a mediana anterior calculada pelo backend. '+
 'Pontos = melhores esforços observados; ausentes, zeros e negativos não entram. '+
 'Linha = tendência ajustada a cada duração separadamente, por regressão local linear com pesos tricubo e raio de ±90 dias. '+
 'Só aparece onde há pelo menos 8 observações válidas no raio, e não avança além das datas observadas. '+
 'Linha clara = trecho com menos de 4 observações de pelo menos um dos lados (até 90 dias): a estimativa é a mesma, mas o suporte é unilateral ou limitado nessa borda (recente ou início da série). Nenhum ponto é removido. '+
 'Faixa = ±1 desvio-padrão local dos resíduos em torno da linha: mede a dispersão dos pontos, não é intervalo de confiança nem de predição. A dispersão é estimada sobre os mesmos dados do ajuste e pode subestimar a variabilidade real. '+
 'Tendência (90 dias) compara a linha de hoje com a de 90 dias atrás; aparece como estável quando a diferença é menor que a dispersão local. '+
 'Sensibilidade (diagnóstico): retira-se, só neste cálculo, a observação mais influente dentro de 90 dias do ponto mais recente e compara-se a estimativa no mesmo ponto temporal. Razão = |deslocamento| ÷ DP local; razão ≥ 1 = Sensibilidade: elevada; razão < 1 = Sensibilidade: baixa segundo o critério utilizado; sem banda, dispersão nula ou sem ajuste = não calculável. A tendência oficial não muda e nenhum dado é removido: não altera dados, medianas nem a curva oficial. '+
 'Excluídos da tendência: somente os marcados pelo backend como padrão confirmado. Ausentes, zeros e negativos não entram como valor. Valores extremos e suspeitos entram no cálculo e são sinalizados, não removidos. Variação positiva ou negativa é relativa à referência e não classifica performance.';
const EV_BTN='display:inline-flex;align-items:center;gap:4px;background:transparent;border:1px solid #30363d;color:#e6edf3;border-radius:999px;padding:5px 10px;min-height:32px;font-size:12px;cursor:pointer';
function evFmtData(t){const s=new Date(t*86400000).toISOString().slice(0,10);return s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(0,4);}
function evPp(v){return (v>=0?'+':'')+Number(v).toFixed(1);}
// Limiar do diagnostico de sensibilidade: razao = |deslocamento ao retirar 1 observacao| / DP local.
// razao >= 1 => "elevada". Convencao documentada (1 DP local), nao ajuste estatistico.
const EV_SENS_LIMITE=1;
// Dispersao abaixo disto (em pontos percentuais) e numericamente nula (ruido de ponto flutuante):
// tratada como 'dispersao nula' para nao gerar razao sem significado.
const EV_SD_MIN=1e-9;
// Texto de suporte nas bordas (diagnostico visual; nao altera o ajuste). lado: 'direita' | 'esquerda' | 'ambos' | null
function evBordaTxt(lado,onde){
 if(lado==='direita')return 'suporte unilateral à direita '+onde;
 if(lado==='esquerda')return 'suporte unilateral à esquerda '+onde;
 if(lado==='ambos')return 'suporte limitado dos dois lados '+onde;
 return null;
}
function evSensRotulo(razao,sd,temInf){
 if(!temInf)return {nivel:'nc',rot:'Sensibilidade: não calculável (sem observação influente com ajuste)'};
 if(sd===null||sd===undefined)return {nivel:'nc',rot:'Sensibilidade: não calculável (sem banda)'};
 if(!(sd>EV_SD_MIN))return {nivel:'nc',rot:'Sensibilidade: não calculável (dispersão nula)'};
 if(razao===null||!isFinite(razao))return {nivel:'nc',rot:'Sensibilidade: não calculável'};
 return razao>=EV_SENS_LIMITE?{nivel:'elevada',rot:'Sensibilidade: elevada'}
  :{nivel:'baixa',rot:'Sensibilidade: baixa segundo o critério utilizado'};
}
function evTendencia90(a){
 const ult=a.grade.filter(q=>q.y!==null);
 if(!ult.length)return {dir:null,texto:'sem tendência nesta janela',sens:'Sensibilidade: não calculável',nivel:'nc',razao:null,inf:null,oficial:null,sem:null,ponto:null,n_rec:0};
 const e=ult[ult.length-1];
 let dir=null, texto;
 const base=e.t-EV_AJ.H, pr=(base>=a.T[0])?evLLFit(base,a.T,a.Y):null;
 if(pr===null){texto='sem ajuste há '+EV_AJ.H+' dias para comparar';}
 else{const dlt=e.y-pr.yhat;
  if(e.sd===null){texto=evPp(dlt)+' pp em '+EV_AJ.H+' dias (sem banda: não classificada)';}
  else{dir=(Math.abs(dlt)<1e-9||Math.abs(dlt)<e.sd)?'estável':(dlt>0?'ascendente':'descendente');
   texto=dir+' ('+evPp(dlt)+' pp em '+EV_AJ.H+' dias)';}}
 let melhor=null;
 for(let i=0;i<a.T.length;i++){if(Math.abs(a.T[i]-e.t)>=EV_AJ.H)continue;
  const f=evLLFit(e.t,a.T,a.Y,i); if(!f)continue;
  const dlt=f.yhat-e.y; if(melhor===null||Math.abs(dlt)>Math.abs(melhor.dlt))melhor={i:i,dlt:dlt,t:a.T[i],y:a.Y[i]};}
 const razao=(melhor&&e.sd!==null&&e.sd>EV_SD_MIN)?Math.abs(melhor.dlt)/e.sd:null;
 const s=evSensRotulo(razao,e.sd,!!melhor);
 const ini=a.grade.find(q=>q.y!==null);
 const bordas=[evBordaTxt(e.lado,'nos dados recentes'),ini?evBordaTxt(ini.lado,'no início da série'):null].filter(Boolean);
 return {dir:dir,texto:texto,sens:s.rot,nivel:s.nivel,razao:razao,inf:melhor,borda:bordas,
  oficial:e.y,sem:melhor?e.y+melhor.dlt:null,ponto:e,n_rec:a.T.filter(t=>t>e.t-EV_AJ.H).length};
}
// Bordas consolidadas: as durações compartilham datas; se o resultado for igual, uma única nota basta.
function evBordaPeriodos(a){
 const dir=[],esq=[];
 a.grade.forEach(q=>{if(q.y===null)return;
  if(q.lado==='direita'||q.lado==='ambos')dir.push(q.t);
  if(q.lado==='esquerda'||q.lado==='ambos')esq.push(q.t);});
 return {dir:dir,esq:esq};
}
function evBordaNota(series){
 const com=series.filter(x=>x.aj&&x.aj.ok).map(x=>({k:x.d.k,p:evBordaPeriodos(x.aj)}));
 if(!com.length)return {comum:true,nota:null,perRow:false};
 const chave=c=>JSON.stringify([c.p.dir,c.p.esq]);
 const comum=com.every(c=>chave(c)===chave(com[0]));
 const p=com[0].p;
 if(!p.dir.length&&!p.esq.length)return {comum:comum,nota:null,perRow:!comum};
 if(!comum)return {comum:false,nota:null,perRow:true};
 const faixa=arr=>[evFmtData(arr[0]),evFmtData(arr[arr.length-1])];
 const partes=[];
 if(p.dir.length){const f=faixa(p.dir); partes.push('à direita, nos dados recentes: de '+f[0]+' a '+f[1]);}
 if(p.esq.length){const f=faixa(p.esq); partes.push('à esquerda, no início da série: de '+f[0]+' a '+f[1]);}
 const quem=com.length>1?'em todas as '+com.length+' durações com ajuste':'nesta duração';
 return {comum:true,perRow:false,nota:'Suporte unilateral nas bordas, '+quem+' (mesmas datas): '+partes.join('; ')+'. Diagnóstico visual: nenhum ponto é removido nem alterado.'};
}
function evResumoHtml(series,jan){
 const bn=evBordaNota(series);
 const linhas=series.map(x=>{
  const sx=x.s, w=(sx&&sx.janelas&&sx.janelas[jan])||{}, nr=x.norm, a=x.aj;
  const oc=!!EV_OCULTAS[x.d.k];
  const cab='<span class="evd" style="color:'+x.d.cor+'"><b>'+evEsc(x.d.rot)+'</b>'+(oc?' <span class="evmut">(oculta no gráfico)</span>':'')+'</span>';
  if(!nr.ok)return '<div class="evrow">'+cab+'<span class="evc evmut evspan" data-l="Comparação entre períodos">comparação indisponível: '+evEsc(nr.motivo)+'</span></div>';
  const rec=(w.mediana_recente!=null)?evV(w.mediana_recente,1)+' W':'—';
  const dW=(w.diferenca!=null)?evPp(w.diferenca)+' W':'—';
  const pc=(w.variacao_pct!=null)?evPp(w.variacao_pct)+'%':'—';
  const suf='janela de '+jan+' dias · obs. '+(w.n_anterior!=null?w.n_anterior:'—')+' no período anterior / '+(w.n_recente!=null?w.n_recente:'—')+' no recente';
  const t=(a&&a.ok)?evTendencia90(a):{texto:'sem tendência: '+evEsc((a&&a.motivo)||'sem dados'),sens:'Sensibilidade: não calculável',nivel:'nc',inf:null,razao:null,oficial:null,sem:null};
  const ajuste=(a&&a.ok)?('ajuste de '+evFmtData(a.T[0])+' a '+evFmtData(a.T[a.T.length-1])+' · '+a.n_obs+' obs. · '+t.n_rec+' nos últimos '+EV_AJ.H+' dias'):'';
  let diag='';
  if(t.inf&&t.nivel!=='nc'){
   diag='diagnóstico, sem a observação de '+evFmtData(t.inf.t)+' ('+evPp(t.inf.y)+'%): estimativa '+evPp(t.sem)+'% em vez de '+evPp(t.oficial)+'% · '+evV(t.razao,2)+' DP local. Não altera dados nem a curva oficial.';}
  return '<div class="evrow">'+cab+
   '<span class="evc" data-l="Comparação entre períodos">'+evV(nr.med,1)+' → '+rec+' · '+dW+' · '+pc+'<br><span class="evmut">'+evEsc(suf)+'</span></span>'+
   '<span class="evc" data-l="Tendência recente estimada">'+evEsc(t.texto)+(ajuste?'<br><span class="evmut">'+evEsc(ajuste)+'</span>':'')+(bn.perRow&&t.borda&&t.borda.length?t.borda.map(b=>'<br><span class="evmut">'+evEsc(b)+'</span>').join(''):'')+'</span>'+
   '<span class="evc" data-l="Sensibilidade (diagnóstico)"><b>'+evEsc(t.sens)+'</b>'+(diag?'<br><span class="evmut">'+evEsc(diag)+'</span>':'')+'</span></div>';
 }).join('');
 return '<div class="evgrid" role="table" aria-label="Resumo por duração"><div class="evrow evhead" role="row">'+
  '<span>Duração</span><span>Comparação entre períodos</span><span>Tendência recente estimada</span><span>Sensibilidade (diagnóstico)</span></div>'+
  linhas+'</div>'+(bn.nota?'<div class="evmut" style="margin-top:6px">'+evEsc(bn.nota)+'</div>':'');
}
function evLegendaCompacta(series){
 return series.map(x=>{const d=x.d, oc=!!EV_OCULTAS[d.k], ok=x.norm.ok;
  const txt=ok?(oc?'oculta':'visível'):'indisponível';
  return '<button type="button" aria-pressed="'+(oc?'false':'true')+'"'+(ok?' onclick="evAlternarDur(\''+d.k+'\')"':' disabled')+
   ' aria-label="'+evEsc(d.rot)+': '+txt+(ok?'; clique para '+(oc?'exibir':'ocultar'):'')+'"'+
   ' style="'+EV_BTN+';opacity:'+((ok&&!oc)?'1':'0.6')+';cursor:'+(ok?'pointer':'default')+'">'+
   evMarcadorSvg(d.forma,d.cor)+'<b>'+evEsc(d.rot)+'</b><span class="evmut">'+txt+'</span></button>';
 }).join('');
}
function evAlternarDur(k){EV_OCULTAS[k]=!EV_OCULTAS[k]; mostrarEvolucao();}
function evAuditoriaHtml(series){
 return series.map(x=>{const n=x.norm;
  const linhas=n.pontos.map(p=>'<tr><td>'+evEsc(p.data)+'</td><td>'+(p.valor===null?'—':evEsc(Math.round(p.valor)+' W'))+
   '</td><td>'+(p.delta===null?'—':evEsc((p.delta>=0?'+':'')+p.delta.toFixed(1)+'%'))+
   '</td><td>'+evEsc(p.estado)+'</td><td>'+evEsc(p.sinal.map(q=>EV_SINAL[q]||q).join(', '))+'</td></tr>').join('');
  return '<h4 class="evmut" style="margin:10px 0 4px">'+evEsc(x.d.rot)+' · '+n.pontos.length+' registros nos últimos 730 dias'+
   (n.ok?'':' · normalização indisponível: '+evEsc(n.motivo))+'</h4>'+
   '<div class="wrap"><table><thead><tr><th>Data</th><th>Valor</th><th>Δ% vs mediana anterior</th><th>Estado</th><th>Sinalização</th></tr></thead><tbody>'+linhas+'</tbody></table></div>';
 }).join('');
}
function drawEvolNorm(id,series,jan,ref){
 const cvEl=document.getElementById(id);
 const H=(cvEl&&cvEl.clientHeight)||290;
 const c=ctx(id,H); if(!c)return; const g=c.g, W=c.W;
 const mob=W<520;
 const vis=series.filter(x=>x.norm.ok&&!EV_OCULTAS[x.d.k]);
 EV_ULTIMO_NORM={visiveis:vis.map(x=>x.d.k),ocultas:series.filter(x=>EV_OCULTAS[x.d.k]).map(x=>x.d.k),desenhados:{},curvas:{},bandas:{},ajuste:{},sensibilidade:{}};
 series.forEach(x=>{const a=x.aj||{ok:false,motivo:'sem ajuste',n_obs:0,curva:0,banda:0,grade:[]};
  EV_ULTIMO_NORM.ajuste[x.d.k]={ok:a.ok,motivo:a.motivo,n_obs:a.n_obs,curva:a.curva,banda:a.banda,grade:a.grade};
  if(a.ok&&!EV_OCULTAS[x.d.k]){const t=evTendencia90(a);EV_ULTIMO_NORM.sensibilidade[x.d.k]={sens:t.sens,nivel:t.nivel,razao:t.razao,dir:t.dir,oficial:t.oficial,sem:t.sem,inf:t.inf?{t:t.inf.t,y:t.inf.y,dlt:t.inf.dlt}:null};}});
 const valores=[];
 vis.forEach(x=>{x.norm.pontos.forEach(p=>{if(p.delta!==null)valores.push(p.delta);});
  if(x.norm.recDelta!==null)valores.push(x.norm.recDelta);
  if(x.aj&&x.aj.ok)x.aj.grade.forEach(q=>{if(q.y===null)return;valores.push(q.y);if(q.sd!==null){valores.push(q.y+q.sd);valores.push(q.y-q.sd);}});});
 if(!valores.length){noData(g,W,H,'Sem pontos válidos com mediana anterior nas séries visíveis');return;}
 let lo=Math.min(0,Math.min.apply(null,valores)), hi=Math.max(0,Math.max.apply(null,valores));
 const pad=Math.max((hi-lo)*0.1,2); lo-=pad; hi+=pad;
 const PL=mob?40:52,PR=12,PT=10,PB=mob?34:38, x0=PL,x1=W-PR,y0=PT,y1=H-PB;
 const fimN=evDia(ref), iniN=fimN-730;
 const X=d=>x0+(x1-x0)*((d-iniN)/(fimN-iniN));
 const Y=v=>y1-(y1-y0)*((v-lo)/(hi-lo));
 const passo=evPasso(hi-lo), dec=passo<1?1:0;
 g.font='11px sans-serif'; g.textBaseline='middle'; g.textAlign='right';
 for(let v=Math.ceil(lo/passo)*passo; v<=hi+1e-9; v+=passo){
  const zero=Math.abs(v)<1e-9;
  g.strokeStyle='#21262d'; g.lineWidth=1; g.beginPath(); g.moveTo(x0,Y(v)); g.lineTo(x1,Y(v)); g.stroke();
  g.fillStyle=zero?'#e6edf3':'#8b949e'; g.fillText(zero?'0%':(v>0?'+':'')+v.toFixed(dec)+'%',x0-6,Y(v));
 }
 // referencia 0% = mediana anterior: linha tracejada, sem texto sobre os dados
 g.strokeStyle='#c9d1d9'; g.lineWidth=1.2; g.setLineDash([5,4]); g.globalAlpha=0.8;
 g.beginPath(); g.moveTo(x0,Y(0)); g.lineTo(x1,Y(0)); g.stroke(); g.setLineDash([]); g.globalAlpha=1;
 g.textAlign='center'; g.textBaseline='top'; g.fillStyle='#8b949e';
 let ultimoX=-1e9;
 for(let d=Math.ceil(iniN/91)*91; d<=fimN; d+=91){
  const xx=X(d), lab=new Date(d*86400000).toISOString().slice(0,7), lw=g.measureText(lab).width;
  if(xx-ultimoX<lw+12)continue;
  g.fillText(lab,xx,y1+8); ultimoX=xx;
 }
 // bandas: dispersao +/-1 DP local, opacidade baixa; quebra onde nao ha desvio
 vis.forEach(x=>{const a=x.aj; if(!a||!a.ok)return;
  const runs=[]; let cur=[];
  a.grade.forEach(q=>{if(q.y===null||q.sd===null){if(cur.length)runs.push(cur);cur=[];}else cur.push(q);});
  if(cur.length)runs.push(cur);
  runs.forEach(run=>{ if(run.length<2)return;
   g.fillStyle=evRgba(x.d.cor,0.07); g.beginPath();
   run.forEach((q,i)=>{const px=X(q.t),py=Y(q.y+q.sd); i?g.lineTo(px,py):g.moveTo(px,py);});
   for(let i=run.length-1;i>=0;i--){const q=run[i]; g.lineTo(X(q.t),Y(q.y-q.sd));}
   g.closePath(); g.fill();});
 });
 // tendencia: linha mais forte que os pontos; lacunas sem suporte ficam sem linha
 vis.forEach(x=>{const a=x.aj; if(!a||!a.ok)return;
  const runs=[]; let cur=[];
  a.grade.forEach(q=>{if(q.y===null){if(cur.length)runs.push(cur);cur=[];}else cur.push(q);});
  if(cur.length)runs.push(cur);
  g.strokeStyle=x.d.cor; g.lineWidth=mob?2.2:2.5; g.lineJoin='round';
  runs.forEach(run=>{for(let i=1;i<run.length;i++){const q0=run[i-1], q1=run[i];
    g.globalAlpha=(q0.lado||q1.lado)?0.35:1;
    g.beginPath(); g.moveTo(X(q0.t),Y(q0.y)); g.lineTo(X(q1.t),Y(q1.y)); g.stroke();}});
  g.globalAlpha=1;
  EV_ULTIMO_NORM.curvas[x.d.k]=a.curva; EV_ULTIMO_NORM.bandas[x.d.k]=a.banda;
 });
 // mediana recente: tracejado da cor da duracao (distinto da tendencia, continua)
 vis.forEach(x=>{const n=x.norm; if(n.recDelta===null||!n.periodoRec)return;
  const a=evDia(n.periodoRec[0]), b=evDia(n.periodoRec[1]);
  g.strokeStyle=x.d.cor; g.lineWidth=2; g.setLineDash([2,3]); g.beginPath(); g.moveTo(X(a),Y(n.recDelta)); g.lineTo(X(b),Y(n.recDelta)); g.stroke(); g.setLineDash([]);
 });
 // observacoes: marcadores pequenos e transparentes; sinalizados ganham anel
 const hits=[];
 const raioR=mob?3:3.2;
 vis.forEach(x=>{let k=0;
  x.norm.pontos.forEach(p=>{if(p.delta===null)return; k++;
   const px=X(evDia(p.data)), py=Y(p.delta);
   g.globalAlpha=0.62; g.fillStyle=x.d.cor; g.strokeStyle=x.d.cor; g.lineWidth=1; evForma(g,x.d.forma,px,py,raioR);
   g.globalAlpha=1;
   if(p.sinal.length){g.strokeStyle='#f0efec'; g.lineWidth=1.2; g.beginPath(); g.arc(px,py,raioR+2.6,0,2*Math.PI); g.stroke();}
   hits.push({x:px,y:py,rot:x.d.rot,p:p,med:x.norm.med,aj:x.aj});
  });
  EV_ULTIMO_NORM.desenhados[x.d.k]=k;
 });
 // diagnostico de sensibilidade: anel tracejado na observacao influente (sem alterar dados)
 vis.forEach(x=>{const t=EV_ULTIMO_NORM.sensibilidade[x.d.k]; if(!t||!t.inf||t.nivel!=='elevada')return;
  const px=X(t.inf.t), py=Y(x.aj.Y[x.aj.T.indexOf(t.inf.t)]);
  g.strokeStyle='#f0efec'; g.lineWidth=1; g.setLineDash([2,2]); g.beginPath(); g.arc(px,py,raioR+5,0,2*Math.PI); g.stroke(); g.setLineDash([]);
 });
 const cv=document.getElementById(id), tip=document.getElementById(id+'Tip');
 if(!cv||!tip)return;
 const alcance=mob?24:14;
 const mostra=function(ev){
  const r=cv.getBoundingClientRect(), mx=ev.clientX-r.left, my=ev.clientY-r.top;
  let best=null, bd=alcance*alcance;
  hits.forEach(h=>{const dd=(h.x-mx)*(h.x-mx)+(h.y-my)*(h.y-my); if(dd<=bd){bd=dd; best=h;}});
  if(!best){tip.style.display='none';return;}
  tip.textContent=evTextoPonto(best);
  tip.style.display='block';
  tip.style.left=Math.max(4,Math.min(mx+12,W-236))+'px'; tip.style.top=Math.max(my-10,0)+'px';
 };
 cv.onpointermove=function(ev){ if(ev.pointerType!=='touch')mostra(ev); };
 cv.onpointerdown=function(ev){ mostra(ev); };
 cv.onpointerleave=function(ev){ if(ev.pointerType!=='touch')tip.style.display='none'; };
 if(!window.__evTipFora){window.__evTipFora=true;
  window.addEventListener('pointerdown',function(e){
   if(e.target&&e.target.closest&&e.target.closest('#ev_norm'))return;
   const t=document.getElementById('ev_normTip'); if(t)t.style.display='none';});}
}
function evPopulacao(M){
 const F=M.fontes||{}, ME=M.melhores_esforcos||{};
 const c=(s,k)=>(s&&s.qualidade&&s.qualidade[k]!=null)?s.qualidade[k]:'—';
 const e=F.eftp_atividade, m=ME['60'];
 return 'Populações distintas: o eFTP da atividade vem das atividades do Intervals.icu ('+c(e,'n_brutos')+
  ' atividades, '+c(e,'n_validos')+' com valor válido). Os melhores esforços vêm da curva de potência por atividade ('+
  c(m,'n_brutos')+' atividades com curva, '+c(m,'n_validos')+' com 1 min válido). As contagens podem diferir: nem toda atividade tem curva e nem toda curva tem eFTP. Zero e ausência não entram como valor.';
}
function mostrarEvolucao(){
 const E=D&&D.evolucao_performance;
 const head=document.getElementById('evolHead'), body=document.getElementById('evolBody');
 const mHead=document.getElementById('evolMeHead'), mBody=document.getElementById('evolMeBody');
 const avisos=document.getElementById('evolAvisos'), sub=document.getElementById('evolSub');
 const cfgBody=document.getElementById('evolCfgBody'), graf=document.getElementById('evolGraficos');
 const pop=document.getElementById('evolPopulacao');
 if(!E||!E.modalidades){
  sub.innerHTML='<span class="err">Evolução indisponível'+(E&&E.erro?': '+evEsc(E.erro):'')+'</span>';
  head.innerHTML='';body.innerHTML='';mHead.innerHTML='';mBody.innerHTML='';avisos.innerHTML='';
  cfgBody.innerHTML='';graf.innerHTML='';pop.textContent='';return;}
 const mod=document.getElementById('evolMod').value;
 const jan=document.getElementById('evolJanela').value;
 const fonte=document.getElementById('evolFonte').value;
 const M=E.modalidades[mod];
 sub.innerHTML='Referência: '+evEsc(E.referencia||'—')+' · '+evEsc(mod)+' · janela de '+evEsc(jan)+' dias (recente vs. anterior)';
 head.innerHTML=evCabecalho().map(h=>'<th>'+h+'</th>').join('');
 mHead.innerHTML=evCabecalho().map(h=>'<th>'+h+'</th>').join('').replace('Série','Melhor esforço');
 if(!M){body.innerHTML='';mBody.innerHTML='';cfgBody.innerHTML='';graf.innerHTML='';pop.textContent='';
  avisos.innerHTML='<div class="sub">Sem dados para '+evEsc(mod)+'.</div>';return;}
 const F=M.fontes||{}, ME=M.melhores_esforcos||{};
 pop.textContent=evPopulacao(M);
 const fontes=[['eftp_atividade','eFTP da atividade (icu_pm_ftp)'],['eftp_nao_confirmado','EFTP (campo não confirmado)']]
  .filter(f=>fonte==='todas'||fonte===f[0]);
 body.innerHTML=fontes.map(f=>evLinha(f[1],F[f[0]],jan)).join('');
 const cfg=F.ftp_configurado;
 const mostraCfg=(fonte==='todas'||fonte==='ftp_configurado');
 cfgBody.innerHTML=mostraCfg?'<tr><td>FTP configurado (icu_ftp)</td><td>'+
  (cfg&&cfg.ultimo_valor!=null?evEsc(Math.round(cfg.ultimo_valor)+' W'):'—')+'</td><td>'+
  evEsc((cfg&&cfg.qualidade&&cfg.qualidade.ultima_observacao)||'—')+'</td><td>Referência de configuração do Intervals.icu. Não compõe a tendência: sem Δ, sem estado e sem classificação.</td></tr>':'';
 const sec=[['60','1 min'],['300','5 min'],['720','12 min'],['1200','20 min']];
 mBody.innerHTML=sec.map(x=>evLinha(x[1],ME[x[0]],jan)).join('');
 avisos.innerHTML=fontes.map(f=>evNotaQualidade(f[1],F[f[0]])).join('')+
  sec.map(x=>evNotaQualidade(x[1],ME[x[0]])).join('');

 const normSeries=EV_DUR.map(d=>{const norm=evSerieNorm(ME[d.k],jan);return {d:d,s:ME[d.k],norm:norm,aj:evAjusteDur(evObsAjuste(norm))};});
 EV_AUDIT_SRC=normSeries;
 const detMet0=document.getElementById('evMetodoDet');
 const abertos=!!(detMet0&&detMet0.open);
 const nRegs=normSeries.reduce((t,x)=>t+x.norm.pontos.length,0);
 graf.innerHTML=EV_CSS_GRAF+
  '<div class="sub"><b>Melhores esforços</b> · variação relativa à mediana anterior · janela de '+evEsc(jan)+' dias · histórico de 730 dias</div>'+
  '<div id="evResumo">'+evResumoHtml(normSeries,jan)+'</div>'+
  '<div class="evchart"><canvas id="ev_norm" role="img" aria-label="Variação percentual dos melhores esforços de 1, 5, 12 e 20 minutos em relação à mediana anterior, com tendência e dispersão" aria-describedby="evResumo"></canvas>'+
  '<div id="ev_normTip" class="evtip"></div></div>'+
  '<div class="evleg" id="evNormLeg">'+evLegendaCompacta(normSeries)+'</div>'+
  '<div class="sub evmut" id="evComoLer" style="line-height:1.5"><b>Como ler:</b> pense nos pontos como as suas medições ao longo do tempo.'+
  '<ul style="margin:6px 0 6px 18px;padding:0">'+
  '<li><b>Pontos:</b> melhores esforços observados. Ausências, zeros e valores negativos não contam.</li>'+
  '<li><b>Linha:</b> tendência estimada a partir dos pontos próximos; cada duração tem a sua própria linha.</li>'+
  '<li><b>Faixa:</b> mostra o quanto os pontos oscilam em torno da linha. Não é um intervalo de confiança.</li>'+
  '<li><b>Linha clara nas bordas:</b> há poucas medições de um dos lados. A tendência continua calculada, mas tem menos suporte.</li>'+
  '<li><b>Tendência (90 dias):</b> compara a linha atual com a de 90 dias atrás. Diferenças pequenas face à dispersão são consideradas estáveis.</li>'+
  '<li><b>Sensibilidade:</b> verifica se uma única medição influente muda muito a estimativa. É um diagnóstico, não uma alteração da curva.</li>'+
  '</ul>Importante: nenhuma medição é removida por esse diagnóstico. A variação é relativa à mediana anterior e, sozinha, não prova melhora ou piora da performance.</div>'+
  '<div class="evmut">pontos = observados · linha = tendência · faixa = ±1 DP local dos resíduos (dispersão, não intervalo de confiança) · linha clara = trecho com suporte unilateral ou limitado nas bordas · tracejado = mediana recente · 0% = mediana anterior</div>'+
  '<details class="sub" id="evMetodoDet"><summary>Ver método estatístico</summary><div class="sub evmut" style="line-height:1.5">'+EV_METODO+'</div></details>'+
  evResumoEftp(F.eftp_atividade,jan);
 const detMet=document.getElementById('evMetodoDet'), aud=document.getElementById('evAudit');
 if(abertos&&detMet)detMet.open=true;
 const audBody=document.getElementById('evAuditBody'), audRes=document.getElementById('evAuditResumo');
 if(audRes)audRes.textContent='Detalhes por observação (auditoria) · '+nRegs+' registros nas 4 durações';
 if(aud&&audBody){const renderAud=()=>{audBody.innerHTML=aud.open?evAuditoriaHtml(EV_AUDIT_SRC||[]):'';};
  aud.ontoggle=renderAud; renderAud();}
 try{drawEvolNorm('ev_norm',normSeries,jan,E.referencia);}
  catch(err){console.error('Gráfico comparativo indisponível:',err);const c=ctx('ev_norm',290);if(c)noData(c.g,c.W,290,'Gráfico indisponível nesta resposta');}
 const etab=document.getElementById('evEftpTab'), etBody=document.getElementById('evEftpTabBody'), etRes=document.getElementById('evEftpTabResumo');
 if(etRes)etRes.textContent='Pontos do eFTP da atividade (tabela) · '+(((F.eftp_atividade||{}).pontos)||[]).length+' registros';
 if(etab&&etBody){const renderEt=()=>{etBody.innerHTML=etab.open?evTabelaEftp(F.eftp_atividade):'';};
  etab.ontoggle=renderEt; renderEt();}
}

function redesenhar(){
 if(!D)return;
 drawPMC();
 if(D.ftlm){drawCTLg();drawFMT();}
 if(D.fmt){drawMatriz();drawEigen();drawAtencao();}}
document.getElementById('janelaPMC').onchange=redesenhar;
window.addEventListener('resize',redesenhar);
window.addEventListener('resize',function(){if(D)mostrarEvolucao();});
load();
carregarSugestoes();
carregarIRExploratorio();
"""


def render():
    corpo = BODY
    for chave, (titulo, texto) in EXPLICACOES.items():
        corpo = corpo.replace('__EXPL_' + chave + '__', explicacao(titulo, texto))
    return page('PMC', SLUG, corpo, JS)
