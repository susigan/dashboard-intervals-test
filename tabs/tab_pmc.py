"""Tab PMC — Performance Management Chart.

Carga vem da Intervals.icu (icu_training_load, ja na base de dados).
Wellness e composicao corporal vem dos Google Sheets, como no dashboard
original: HRV/RMSSD, HRR, sono, stress, fadiga, humor, dores musculares,
peso, gordura, calorias e macros.
"""

from datetime import datetime, timedelta
from flask import jsonify, request

import db
import pmc
import sheets_client as sheets
from api_client import fetch_activities, norm_tipo, num
from config import CICLICOS, CORES_MOD, ANOS_HRV, limite_hrv
from tabs.base import page, explicacao

SLUG = 'pmc'


def _sheets(force=False):
    """Wellness e composicao corporal. A cache vive no sheets_client, para
    que as tabs nao dependam umas das outras."""
    return sheets.carregar(force)


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
            'cp': (num(a.get('icu_pm_cp')) or num(a.get('icu_rolling_ftp'))
                   or num(a.get('icu_pm_ftp')) or None),
            'w_prime': num(a.get('icu_pm_w_prime')) or None,
        })

    desde = request.args.get('desde') or None
    serie = pmc.calcular(sessoes, 'tl', desde=desde)
    mods = pmc.por_modalidade(sessoes, CICLICOS, 'tl', desde=desde)

    wellness, corporal, erros_sheets = _sheets()

    # CP ajustado a curva de potencia tem prioridade sobre o icu_pm_cp: aquele
    # e a estimativa de uma sessao isolada e, nos dias sem esforcos maximos,
    # subestima muito e enche a serie de ruido.
    cp_curva, n_cp_curva = {}, 0
    try:
        for r in db.cp_por_sessao():
            if r['r2'] >= 0.80:      # so ajustes que sao mesmo uma recta
                cp_curva[r['activity_id']] = r
    except Exception as e:
        print(f"cp_por_sessao: {e}")
    for s in sessoes:
        r = cp_curva.get(s['id'])
        if r:
            s['cp'] = r['cp']
            s['w_prime'] = r['w_prime'] or s.get('w_prime')
            n_cp_curva += 1

    try:
        ftlm_res = pmc.calcular_ftlm(sessoes, wellness, serie, CICLICOS)
        erro_ftlm = None
    except Exception as e:
        import traceback
        traceback.print_exc()
        ftlm_res, erro_ftlm = None, f'{type(e).__name__}: {e}'

    try:
        fmt_res = pmc.calcular_fmt(sessoes, wellness, serie,
                                   desde_hrv=limite_hrv())
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
        'cp_fonte': {
            'da_curva': n_cp_curva,
            'do_icu_pm_cp': sum(1 for s in sessoes
                                if s.get('cp') and s['id'] not in cp_curva),
            'nota': "ajustado a P(t)=W'/t+CP nas duracoes 2-20min, R2>=0.80"},
        'fmt': fmt_res,
        'homeostatico': homeo, 'homeostatico_mod': homeo_mod,
        'alostatico': alos,
        'cores': CORES_MOD, 'ciclicos': CICLICOS,
    })


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
            'cp': (num(a.get('icu_pm_cp')) or num(a.get('icu_rolling_ftp'))
                   or num(a.get('icu_pm_ftp')) or None),
            'w_prime': num(a.get('icu_pm_w_prime')) or None,
        })

    cp_curva = {}
    try:
        for r in db.cp_por_sessao():
            if r['r2'] >= 0.80:
                cp_curva[r['activity_id']] = r
    except Exception:
        pass
    for s in sessoes:
        r = cp_curva.get(s['id'])
        if r:
            s['cp'] = r['cp']

    wellness, _c, _e = _sheets()
    serie = pmc.calcular(sessoes, 'tl')
    res = pmc.calcular_fmt(sessoes, wellness, serie)
    if not res or res.get('erro'):
        return {'erro': (res or {}).get('erro', 'nao foi possivel calibrar')}

    cal = res.get('calibracao') or {}
    # ?desde=YYYY-MM-DD limita as analises que dependem de HRV.
    # Util quando as medicoes so comecaram a meio do historico.
    desde_hrv = request.args.get('desde') or limite_hrv()
    segmentado = pmc.calibrar_segmentado(sessoes, wellness, serie, CICLICOS,
                                         desde=desde_hrv)
    eventos = pmc.teste_eventos(sessoes, wellness, serie, CICLICOS,
                                desde=desde_hrv)
    # Calibrar contra os dias de esforco maximo. Usa o historico completo:
    # a CP de um teste de 2022 e comparavel com a de 2026, ao contrario do HRV.
    try:
        ancora = pmc.calibrar_com_ancora(serie, CICLICOS)
    except Exception as e:
        import traceback
        traceback.print_exc()
        ancora = {'erro': f'{type(e).__name__}: {e}'}
    return {
        'status': 'OK',
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
  a essa modalidade; ponteado = CTL e ATL classicos, como referencia.</div>
<div id="painelMods" class="grid2"></div>
<div class="wrap" style="max-height:280px;margin-bottom:14px"><table>
  <thead><tr id="gHead"></tr></thead><tbody id="gBody"></tbody></table></div>

<h2>FMT — tensor 5&times;5 e mapa de atencao</h2>
<div class="sub" id="subFMT5"></div>
<div class="grid2">
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
<h3>Calibracao dos parametros</h3>
<div id="veredicto"></div>
<div class="sub" id="notaCal"></div>
<div class="wrap" style="max-height:260px;margin-bottom:14px"><table>
  <thead><tr id="calHead"></tr></thead><tbody id="calBody"></tbody></table></div>

<h2>Curvatura &kappa; ao longo do tempo</h2>
__EXPL_fmt__
<div class="sub" id="subFMT"></div>
<div class="chartbox">
  <div class="legend" id="lgFMT"></div>
  <canvas id="chFMT" height="220"></canvas>
</div>



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

<h2>Indice alostatico</h2>
__EXPL_alos__
<div class="sub" id="subAlos"></div>
<div id="alosCard"></div>
<div class="wrap" style="max-height:320px;margin-bottom:14px"><table>
  <thead><tr id="alosHead"></tr></thead><tbody id="alosBody"></tbody></table></div>

<div class="sub" style="margin-top:20px">
  <a href="/api/pmc" target="_blank">JSON</a> &middot;
  <a href="/api/debug/sheets" target="_blank">Diagnostico dos Google Sheets</a>
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

function drawHomeo(){
 const H=D.homeostatico;
 if(!H){const o=ctx('chHomeo',300);if(o)noData(o.g,o.W,o.H);return;}
 const vista=document.getElementById('homeoVista').value;
 const modSel=document.getElementById('homeoMod').value;
 const verMods=document.getElementById('homeoMods').checked;
 const HM=D.homeostatico_mod||{};
 const principal=(modSel&&HM[modSel])?HM[modSel]:H;
 const P=periodosHomeo(principal.serie);

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
 const mods=(verMods&&vista==='todo')?Object.keys(HM):[];
 const idxMod={};
 mods.forEach(function(m){
  const ix={};(HM[m].serie||[]).forEach(r=>{ix[r.date]=r.p_hat_suave;});
  idxMod[m]=ix;});

 let mn=Infinity,mx=-Infinity;
 [ant,rec].forEach(seg=>seg.forEach(function(d){
  [d.banda_inf,d.banda_sup,d.p_hat_suave].forEach(function(v){
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
   seg.forEach(function(d,i){const v=d.banda_sup;if(v==null)return;
    if(!ok){g.moveTo(X(i),Y(v));ok=true;}else g.lineTo(X(i),Y(v));});
   for(let i=seg.length-1;i>=0;i--){const v=seg[i].banda_inf;if(v==null)continue;
    g.lineTo(X(i),Y(v));}
   g.closePath();g.fill();}
  if(vista==='todo'&&!OFFH.bruto){
   g.strokeStyle=hexRgba(cor,0.30);g.lineWidth=1;g.beginPath();let st=false;
   seg.forEach(function(d,i){const v=d.p_hat;if(v==null){st=false;return;}
    if(!st){g.moveTo(X(i),Y(v));st=true;}else g.lineTo(X(i),Y(v));});
   g.stroke();}
  g.strokeStyle=cor;g.lineWidth=2.4;g.beginPath();let st2=false;
  let pico=null,pi=0;
  seg.forEach(function(d,i){const v=d.p_hat_suave;if(v==null){st2=false;return;}
   if(pico===null||v>pico){pico=v;pi=i;}
   if(!st2){g.moveTo(X(i),Y(v));st2=true;}else g.lineTo(X(i),Y(v));});
  g.stroke();
  // marcador do pico
  if(pico!==null){
   g.fillStyle=cor;g.beginPath();g.arc(X(pi),Y(pico),4,0,Math.PI*2);g.fill();
   g.fillStyle=cor;g.font='10px sans-serif';g.textAlign='center';
   g.fillText('pico '+Math.round(pico),X(pi),Y(pico)-8);g.textAlign='left';}
  return pico;
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
 if(vista==='todo')itens.push(['bruto','p̂ diario','rgba(150,150,150,0.4)']);
 mods.forEach(m=>itens.push([m,m,(D.cores||{})[m]||'#8b949e']));
 document.getElementById('lgHomeo').innerHTML=itens.map(x=>
  (x[0].indexOf('__')===0
   ? '<span><i style="background:'+x[2]+'"></i>'+x[1]+'</span>'
   : '<span class="tog'+(OFFH[x[0]]?' off':'')+'" data-k="'+x[0]+'">'+
     '<i style="background:'+x[2]+'"></i>'+x[1]+'</span>')).join('');
 document.querySelectorAll('#lgHomeo span.tog').forEach(function(sp){
  sp.onclick=function(){OFFH[sp.dataset.k]=!OFFH[sp.dataset.k];drawHomeo();};});

 // leitura por baixo, como no dashboard
 const el=document.getElementById('homeoAnalise');
 if(picoA!=null&&picoR!=null){
  const dif=picoR-picoA, pct=(dif/picoA*100);
  const cor=dif>=0?'#2ECC71':'#E67E22';
  el.innerHTML='<div style="background:#161b22;border-left:3px solid '+cor+
   ';padding:9px 13px;border-radius:0 6px 6px 0;font-size:13px;margin:10px 0">'+
   'O pico de reserva '+(dif>=0?'subiu':'desceu')+' <b>'+Math.abs(Math.round(dif))+
   ' unidades</b> entre periodos ('+Math.round(picoA)+' → '+Math.round(picoR)+', '+
   (pct>=0?'+':'')+pct.toFixed(0)+'%). '+
   (dif>=0?'Melhor adaptacao na fase recente.'
         :'A reserva caiu — carga sem compensacao, ou fase de acumulacao.')+
   '</div>';
 } else el.innerHTML='';

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
    linhaTip(rot==='Anterior'?CORANT:CORREC,'p̂ ajustado',d.p_hat_suave);
   mods.forEach(function(m){
    if(OFFH[m])return;
    const v=idxMod[m][d.date];if(v==null)return;
    html+=linhaTip((D.cores||{})[m]||'#8b949e',m,v);});
   html+='<div class="tr"><span>Banda</span><b>'+d.banda_inf+' a '+d.banda_sup+'</b></div>';
   return html;});});
}

const MOTIVOS={
 ok:'ajustado',
 poucos_pontos_cp:'poucos pontos de CP',
 k_negativo:'K&#8322; sairia negativo',
 r2_nao_positivo:'R&sup2; nao positivo',
 sem_tentativas:'sem tentativas'};
function motivoHomeo(v){
 if(v.ajustado)return 'ajustado';
 let t=MOTIVOS[v.motivo]||'defeito 42/7';
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
  const ult=s.length?s[s.length-1].p_hat_suave:'—';
  return '<tr><td style="color:'+cor+'">'+nome+'</td>'+
   '<td class="num">'+v.k1+'</td><td class="num">'+v.k2+'</td>'+
   '<td class="num">'+v.t1+'d</td><td class="num">'+v.t2+'d</td>'+
   '<td class="num">'+v.r2+'</td><td class="num">'+ult+'</td>'+
   '<td style="font-size:12px;color:'+(v.ajustado?'#2ECC71':'#E67E22')+'" '+
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
  'p̂(t) = p₀ + K₁·EWM(carga,T₁) − K₂·EWM(carga,T₂) · '+H.nota;
 const selM=document.getElementById('homeoMod');
 const HM=D.homeostatico_mod||{};
 if(selM.options.length<=1)
  selM.innerHTML='<option value="">Global</option>'+
   Object.keys(HM).map(m=>'<option>'+m+'</option>').join('');
 document.getElementById('homeoKpis').innerHTML=[
  ['K₁ (ganho fitness)',H.k1],['K₂ (ganho fadiga)',H.k2],
  ['T₁ (τ fitness)',H.t1+'d'],['T₂ (τ fadiga)',H.t2+'d'],
  ['R²',H.r2],['Pontos de CP',H.n_testes]
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
  document.getElementById('lgM'+m).innerHTML=
   '<span><i style="background:'+cor+'"></i>CTLγ γ='+v.gamma+' R²='+v.r2+'</span>'+
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

 registarTip('chAtencao',function(mxp,myp,rw){
  const esc=rw/W,x=mxp/esc;
  const i=Math.floor((x-PL)/bw);
  if(i<0||i>=n)return '';
  return '<div class="th">'+A.datas[i]+' · '+(A.lag[i]===0?'hoje':'d-'+A.lag[i])+
   '</div>'+linhaTip(A.cor,'Peso',(A.pesos[i]*100).toFixed(1)+'%');});
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
  sel.innerHTML=Object.keys(F.canais).map(k=>
   '<option value="'+k+'">'+F.canais[k].nome+'</option>').join('');
 document.getElementById('notaAtencao').textContent=F.nota_atencao||'';
 tabelaCalibracao();
 drawMatriz();drawEigen();drawAtencao();
}

function drawFMT(){
 if(!D.ftlm){return;}
 drawLinhas('chFMT','lgFMT',janelaPMC(D.ftlm.serie),['kappa','lambda1'],
  {kappa:'#E74C3C',lambda1:'#F4D03F'},
  {kappa:'κ (instabilidade)',lambda1:'λ₁ (dominancia)'},
  {off:OFFK,redraw:drawFMT,height:220,escala:'propria'});
}
function tabelaGammas(){
 const pm=(D.ftlm||{}).por_modalidade||{};
 const mods=Object.keys(pm);
 document.getElementById('gHead').innerHTML=
  ['Modalidade','γ','R²','n','Sessoes','CTLγ actual','Fase']
   .map((c,i)=>'<th class="'+(i&&i<6?'num':'')+'">'+c+'</th>').join('');
 const leg=(D.ftlm||{}).fases_legenda||{};
 document.getElementById('gBody').innerHTML=mods.map(function(m){
  const v=pm[m], f=leg[v.fase]||{};
  return '<tr><td style="color:'+(D.cores[m]||'#e6e6e6')+'">'+m+'</td>'+
   '<td class="num">'+v.gamma+'</td><td class="num">'+v.r2+'</td>'+
   '<td class="num">'+v.n+'</td><td class="num">'+v.n_sessoes+'</td>'+
   '<td class="num">'+v.ctlg_actual+'</td>'+
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
  it.push(['ctlg_perf','CTLγ perf (γ='+(gg.perf?gg.perf.gamma:'?')+')','#2980b9']);
  it.push(['ctlg_rec','CTLγ rec (γ='+(gg.rec?gg.rec.gamma:'?')+')','#8e44ad']);}
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

 drawPMC(); mostrarFMT5(); mostrarHomeo(); mostrarAlos();

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
   '&gamma;<sub>perf</sub> '+(g.perf?g.perf.gamma+' (R&sup2; '+g.perf.r2+')':'—')+
   ' &middot; &gamma;<sub>rec</sub> '+(g.rec?g.rec.gamma+' (R&sup2; '+g.rec.r2+')':'—')+
   (D.cp_fonte&&D.cp_fonte.da_curva
    ? '<br>CP de '+D.cp_fonte.da_curva+' sessoes ajustado a P(t)=W&prime;/t+CP '+
      '(2-20min, R&sup2;&ge;0.80)' : '')+'</span>';

  drawCTLg(); tabelaGammas(); drawFMT();
  const fm=F.fmt||{};
  document.getElementById('subFMT').innerHTML=
   '&kappa;(t) = trace(cov(&Delta;x)) em janela de 28d sobre '+
   (fm.dimensoes||[]).length+' dimensoes: '+(fm.dimensoes||[]).join(', ')+
   '. &kappa; alto = sistema a oscilar mais.';
 }

}
['verFTLM','verFases','escCTLgPMC'].forEach(id=>
 document.getElementById(id).onchange=function(){if(D)drawPMC();});
['homeoMod','homeoVista','homeoMods','haIni','haFim','hrIni','hrFim'].forEach(id=>
 document.getElementById(id).onchange=function(){if(D)drawHomeo();});
document.getElementById('canalFMT').onchange=function(){if(D&&D.fmt)drawAtencao();};
function redesenhar(){
 if(!D)return;
 drawPMC();
 if(D.ftlm){drawCTLg();drawFMT();}
 if(D.fmt){drawMatriz();drawEigen();drawAtencao();}
 if(D.homeostatico){drawHomeo();}}
document.getElementById('janelaPMC').onchange=redesenhar;
window.addEventListener('resize',redesenhar);
load();
"""


def render():
    corpo = BODY
    for chave, (titulo, texto) in EXPLICACOES.items():
        corpo = corpo.replace('__EXPL_' + chave + '__', explicacao(titulo, texto))
    return page('PMC', SLUG, corpo, JS)
