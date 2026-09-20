"""tab_moxy.py — sessões com sensor NIRS.

Mostra a última sessão com Moxy da modalidade escolhida, com os streams de
SmO2 e THb limpos pelo pipeline do mnirs (Jem Arnold): resample, substituir
inválidos e outliers, filtrar, e opcionalmente normalizar.
"""

from tabs.base import page

SLUG = 'moxy'

BODY = """
<div class="wrap">

  <h1>Moxy</h1>

  <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;">
    <button id="mxSubBtnPrincipal" onclick="mxMudarSubTab('principal')"
      style="background:#1c2331;border:1px solid #5DADE2;color:#5DADE2;
      padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">
      Principal</button>
    <button id="mxSubBtnLimiares" onclick="mxMudarSubTab('limiares')"
      style="background:#161b22;border:1px solid #30363d;color:#8b949e;
      padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">
      Limiares</button>
    <button id="mxSubBtnIntervencoes" onclick="mxMudarSubTab('intervencoes')"
      style="background:#161b22;border:1px solid #30363d;color:#8b949e;
      padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">
      Intervenções</button>
    <button id="mxSubBtnRede" onclick="mxMudarSubTab('rede')"
      style="background:#161b22;border:1px solid #30363d;color:#8b949e;
      padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">
      Rede causal</button>
    <button id="mxSubBtnVerificacao" onclick="mxMudarSubTab('verificacao')"
      style="background:#161b22;border:1px solid #30363d;color:#8b949e;
      padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">
      Verificação</button>
  </div>

  <div class="controls">
    <label class="sel">Modalidade
      <select id="mxModalidade" onchange="mxSessoes()">
        <option value="">todas</option>
        <option>Bike</option><option>Row</option>
        <option>Ski</option><option>Run</option>
      </select>
    </label>
    <button onclick="mxActualizar(1)" title="Mostra o que seria alterado, sem alterar nada.">Verificar</button>
    <button onclick="mxActualizar(0)" title="Reconcilia com a Intervals.icu.">Actualizar sessões</button>
    <span id="mxEstado" style="color:#8b949e;font-size:12px;margin-left:8px;"></span>
  </div>

  <div id="mxErro" style="display:none;color:#F85149;font-size:12px;
    border-left:3px solid #F85149;padding:6px 10px;margin:6px 0;"></div>

  <div id="mxDatas" style="margin:8px 0;"></div>
  <div id="mxCanais" style="margin:6px 0;"></div>
  <label style="font-size:11px;color:#8b949e;margin-left:4px;">
   <input type="checkbox" id="mxTendencia" onchange="mxDraw()"> mostrar tendência (topo/fundo de cada bloco) dos canais activos
  </label>

  <div class="controls" style="margin:4px 0;flex-wrap:wrap;gap:6px 14px;">
    <label class="sel">Alinhar por
      <select id="mxAlinha" onchange="mxAlinhar()">
        <option value="bloco">1.º bloco de trabalho</option>
        <option value="watts">degrau de watts equivalente</option>
        <option value="inicio">início do corte</option>
      </select>
    </label>
    <label class="sel">Normalizar
      <select id="mxNorm" onchange="mxCarregar()">
        <option value="">valores brutos</option>
        <option value="deslocar">base a zero (Δ)</option>
        <option value="reescalar">0–100% da amplitude</option>
      </select>
    </label>
    <label class="sel">Suavização
      <select id="mxFc" onchange="mxCarregar()">
        <option value="0.05">leve</option>
        <option value="0.02" selected>média</option>
        <option value="0.008">forte</option>
      </select>
    </label>
  </div>

  <div id="mxSubPrincipalA">
  <div class="chartbox" style="position:relative;">
    <canvas id="chMoxy" height="380"></canvas>
    <div id="mxTip" style="display:none;position:absolute;pointer-events:none;
      background:#161b22;border:1px solid #30363d;border-radius:6px;
      padding:6px 9px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
    <div id="mxCartaoBP" style="display:none;position:absolute;top:8px;right:8px;
      background:rgba(13,17,23,0.9);border:1px solid #30363d;border-radius:6px;
      padding:6px 10px;font-size:11px;color:#c9d1d9;z-index:4;pointer-events:none;"></div>
  </div>

  <div class="controls" id="mxOffsetsWrap" style="margin-top:4px;flex-wrap:wrap;gap:4px 12px;">
    <span style="color:#8b949e;font-size:12px;">Ajuste fino:</span>
    <span id="mxOffsets" style="display:flex;flex-wrap:wrap;gap:4px 12px;
      align-items:center;"></span>
  </div>

  <div class="controls" style="margin-top:6px;flex-wrap:wrap;gap:6px 12px;">
    <span style="color:#8b949e;font-size:12px;">Intervalo analisado:</span>
    <input type="range" id="mxIni" min="0" max="100" value="0" step="0.2"
           style="width:180px" oninput="mxSlider()">
    <input type="range" id="mxFim" min="0" max="100" value="100" step="0.2"
           style="width:180px" oninput="mxSlider()">
    <span id="mxCorteTxt" style="color:#c9d1d9;font-size:12px;"></span>
    <button onclick="mxAplicarProposta()" title="Repõe os cursores na proposta automática. Não grava.">Repor proposta</button>
    <button onclick="mxTudo()" title="Repõe os cursores na sessão inteira. Não grava.">Sessão inteira</button>
    <button onclick="mxGuardarCorte()" title="Grava este intervalo para esta sessão.">💾 Gravar este intervalo</button>
    <span id="mxCorteEstado" style="color:#8b949e;font-size:11px;"></span>
  </div>
  <p id="mxCorteNota" style="color:#8b949e;font-size:11px;margin:4px 0;"></p>
  <p style="color:#8b949e;font-size:11px;margin:0 0 8px 0;">
    <b>Repor proposta</b> e <b>Sessão inteira</b> só movem os cursores.
    <b>Gravar este intervalo</b> guarda-o para esta sessão. O limite de pausa
    que separa aquecimento de protocolo sai da mediana das pausas da própria
    sessão, porque o descanso varia de protocolo para protocolo.</p>

  <div id="mxBlocos" style="overflow-x:auto;margin-top:10px;"></div>
  <div id="mxDiag" style="margin-top:8px;"></div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Como os dados são tratados</summary>
    <div style="font-size:11px;color:#8b949e;margin-top:6px;">
      <p>Pipeline portado do pacote <b>mnirs</b> de Jem Arnold. A ordem não é
      arbitrária: <b>resample → substituir inválidos e outliers → filtrar →
      normalizar</b>. Filtrar antes de remover outliers espalha-os pelos
      vizinhos.</p>
      <p><b>Outliers</b> contra a <b>mediana</b> local, não a média — com a
      média, um pico desloca o próprio centro contra o qual está a ser julgado.</p>
      <p>A FC, o DFA-a1 e a respiração vêm todos da série de RR: se a cinta
      falha, os três herdam os buracos, e por isso levam o filtro de
      artefactos. O SmO2 e o THb vêm do Moxy e não são afectados.</p>
    </div>
  </details>

  <div id="mxCartoesSimples" style="margin-top:10px;"></div>
  <div id="mxRpe" style="margin-top:10px;"></div>
  <div id="mxResumo" style="margin-top:14px;"></div>
  </div>

  <div id="mxAnaliseUnica">
  <div id="mxResumoBloco" style="display:none;">
    <h2 style="font-size:15px;margin-top:18px;">Comparação — limitador por sessão</h2>
    <div class="controls">
      <button onclick="mxResumo()">Recalcular</button>
      <span id="mxResumoEstado" style="color:#8b949e;font-size:12px;"></span>
    </div>
    <div id="mxResumo" style="margin-top:6px;"></div>
  </div>

  <div id="mxLimiaresBloco" style="display:none;">
    <h2 style="font-size:15px;margin-top:18px;">Limiares por SmO2</h2>
    <div style="display:flex;flex-wrap:wrap;gap:10px;">
      <div style="flex:1;min-width:300px;">
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxLimiares" height="220"></canvas>
          <div id="mxTipLimiares" style="display:none;position:absolute;pointer-events:none;
            background:#161b22;border:1px solid #30363d;border-radius:6px;
            padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
      </div>
      <div style="flex:1;min-width:300px;">
        <h3 style="font-size:13px;color:#8b949e;margin:0 0 4px;">Dmax (Cheng et al. 1992)</h3>
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxDmax" height="220"></canvas>
          <div id="mxTipDmax" style="display:none;position:absolute;pointer-events:none;
            background:#161b22;border:1px solid #30363d;border-radius:6px;
            padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
      </div>
      <div style="flex:1;min-width:300px;">
        <h3 style="font-size:13px;color:#8b949e;margin:0 0 4px;">DFA-α1 × intensidade</h3>
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxDfa1" height="220"></canvas>
          <div id="mxTipDfa1" style="display:none;position:absolute;pointer-events:none;
            background:#161b22;border:1px solid #30363d;border-radius:6px;
            padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
      </div>
    </div>
    <div class="controls"><button onclick="mxLimiares()">Calcular</button>
      <button onclick="mxGuardarAnalise()" title="Grava perfil, breakpoints, 5-1-5 e rede causal. Voltar a gravar substitui, com a versão do método usada.">💾 Gravar análise</button>
      <button onclick="mxGravarTodas()" title="Grava todas as sessões com Moxy. Só re-grava as que foram calculadas com uma versão anterior do método.">💾 Gravar todas</button>
      <label class="sel">Terminaram por exaustão
        <select id="mxExaustao" title="Só blocos que terminaram por falha são pontos válidos para o CER.">
          <option value="">nenhum</option>
          <option value="ultimos:2">os 2 últimos</option>
          <option value="ultimos:3">os 3 últimos</option>
          <option value="ultimos:4">os 4 últimos</option>
          <option value="1">todos</option>
        </select></label>
      <span id="mxLimEstado" style="color:#8b949e;font-size:12px;"></span></div>
    <div id="mxLimiares" style="margin-top:6px;"></div>
    <h3 style="font-size:13px;color:#8b949e;margin:14px 0 4px;">DFA-α1 — HRVT1c individualizado</h3>
    <div id="mxDfa1" style="margin-top:4px;"></div>
    <div id="mxDerivadas" style="margin-top:6px;"></div>
    <div id="mxEstilosRecentes" style="margin-top:10px;"></div>
    <details style="margin-top:6px;">
      <summary style="cursor:pointer;font-size:12px;color:#8b949e;">Método e fiabilidade por modalidade</summary>
      <div style="font-size:11px;color:#8b949e;margin-top:6px;">
        <p><b>Método:</b> regressão segmentada de três troços contínuos sobre
        SmO2 mínimo × potência de cada degrau — as três fases descritas por
        Bhambhani. O segundo troço parte de onde o primeiro acaba: o sinal não
        salta no limiar, muda de inclinação.</p>
        <p><b>Rejeição:</b> teste F contra uma recta única. Numa descida
        linear com ruído, dois breakpoints reduzem o erro em 27% de graça —
        quatro parâmetros extra ajustam ruído. O teste F desconta isso, e
        rejeita a curva sem quebra.</p>
        <p><b>Bike e Run:</b> concordância moderada com VT1/VT2, com
        subestimação sistemática (Feldmann 2022). Na corrida, o SmO2 mínimo
        não reflecte o VO₂pico.</p>
        <p><b>Row:</b> Possamai 2024, específico de remo, conclui que estes
        limiares <i>"should not be considered interchangeable"</i> com MLSS e
        CP. Calculam-se, mas não servem para prescrever.
        <b>Ski:</b> sem literatura; tratado como o remo.</p>
        <p><b>Número de degraus:</b> testado com escadas de breakpoint
        conhecido — 12 degraus acertam o BP1 no valor exacto, 9 erram 30 W.</p>
        <p><b>Que limiar é qual.</b> O SmO2max — o topo da parábola, quando
        existe — aproxima o <b>FatMax / LT1 / VT1</b>, o primeiro limiar. A
        quebra na queda (deoxy-BP) e o padrão de dessaturação apontam ao
        <b>RCP / VT2 / MLSS</b>, o segundo. São coisas diferentes e não se
        substituem.</p>
        <p><b>Dois perfis de resposta</b> (Jem Arnold, no mesmo protocolo 5-1).
        <i>Parabólico:</i> o SmO2 sobe nas cargas baixas até um máximo e só
        depois desce — o topo aproxima o FatMax. <i>Monotónico:</i> desce
        desde o primeiro degrau. Neste segundo perfil o primeiro limiar
        <b>não é observável no SmO2</b>: nas palavras dele, o sinal associado
        ao LT1 <i>"may not exist at all"</i>. Procurá-lo aí é procurar o que
        não está.</p>
        <p><b>Aviso do próprio autor sobre a concordância:</b> a associação
        entre deoxy-BP e RCP existe ao nível do grupo, mas ao nível individual
        <i>"this association broke down"</i>, com variabilidade de ±100 W.</p>
        <p><b>MLSS e BP2 não são o mesmo cálculo.</b> O MLSS lê a forma
        <i>dentro</i> de cada bloco ao longo do tempo — estabiliza ou continua
        a descer. O BP2 lê a curva SmO2 × potência <i>entre</i> blocos. Apontam
        à mesma fronteira fisiológica (VT2 / RCP) por rotas diferentes, e a
        distância entre eles é a incerteza real da estimativa, não um erro.
        Quando discordam muito, é sinal de que o protocolo não tem degraus
        suficientes ou não são longos que cheguem.</p>
        <p><b>Método da Moxy (MoxyBreakPoint v0.8), adaptado.</b> Usa a
        <i>média</i> de SmO2 de cada intervalo WORK, ordena por potência e
        interpola 10 pontos entre cada par antes de ajustar dois breakpoints.
        A interpolação faz o breakpoint cair numa grelha fina em vez de só
        nos degraus medidos.
        <b>Uma correcção ao original:</b> interpolar não cria informação — os
        51 pontos ajustados vêm de 6 medições. O script deles não faz teste de
        significância; se se fizesse sobre os interpolados, o n estaria
        inflacionado 10× e daria significativo quase sempre. Aqui a
        interpolação localiza o breakpoint e o <b>teste F corre sobre os
        pontos originais</b>.
        <b>Limite:</b> dois breakpoints são seis parâmetros. Com 6 degraus
        sobram zero graus de liberdade e o BP2 sai do ajuste mas não é
        testável — são precisos 8 degraus.</p>
        <p><b>Três métodos, por ordem de aplicabilidade aqui.</b>
        <b>1) Taxa de dessaturação:</b> mede a velocidade de queda do SmO2
        dentro de cada degrau, depois do transiente, e procura a quebra.
        <b>Dois padrões são válidos</b>, e o Rogers descreve os dois no mesmo
        artigo: <i>aceleração</i>, típica do recto femoral, em que a queda se
        agrava acima do ponto; e <i>patamar</i>, típico do vasto lateral, em
        que a queda deixa de se agravar porque a extracção chegou ao limite.
        O músculo onde tens o sensor determina qual esperas ver.
        É o que o Rogers usa nas escadas — <i>"the rate of change between
        stages showing a shift at high power outputs corresponding to the
        RCP"</i>. Funciona com blocos curtos e bastam 4 degraus, porque a taxa
        não depende de onde o degrau começou.
        <b>2) Padrão de dessaturação (MLSS):</b> precisa de 5 min por degrau.
        <b>3) Regressão sobre os mínimos:</b> desenhada para rampa contínua.</p>
        <p><b>Qual travessia conta.</b> O MLSS sai do <b>último</b> bloco que
        estabiliza tendo tudo acima dele a descer — não do primeiro que desce.
        Numa sequência como 155(sobe) 173(desce) 193(desce) 213(estabiliza)
        229(desce) 251(desce), tomar a primeira travessia daria 164 W; mas
        213 W estabiliza, e se essa carga é sustentável o MLSS não pode estar
        50 W abaixo. Lê-se de cima para baixo, como se lê a olho. Blocos que
        contradizem a leitura ficam assinalados a laranja.</p>
        <p><b>Para protocolos de blocos, o método principal é outro.</b> O
        MLSS sai do padrão dentro de cada bloco: abaixo dele o SmO2 desce e
        <i>estabiliza</i>; acima, desce <i>continuamente até ao fim</i>. Não
        basta o declive médio ser negativo — Rogers dá o contra-exemplo de um
        bloco a 268 W que descia mas estabilizava aos 4 minutos, e que por
        isso não marcava o MLSS. Por isso medimos a segunda metade do bloco,
        depois de ignorar o transiente de arranque.</p>
      </div>
    </details>
  </div>

  <div id="mxSubIntervencoesA" style="display:none;">
  <div id="mxIntervencoes"></div>
  <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start;">
    <div class="chartbox" style="position:relative;width:480px;max-width:100%;">
      <canvas id="chMxZonas" height="220"></canvas>
      <div id="mxTipZonas" style="display:none;position:absolute;pointer-events:none;
        background:#161b22;border:1px solid #30363d;border-radius:6px;
        padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
    </div>
    <div id="mxPlanoZonas" style="flex:1;min-width:280px;"></div>
  </div>
  </div>

  <div id="mxSubRedeA" style="display:none;">
  <h2 style="font-size:15px;margin-top:18px;">Rede causal entre canais</h2>
  <div class="controls" style="flex-wrap:wrap;gap:6px 12px;">
    <button onclick="mxRede()">Calcular</button>
    <label class="sel"><input type="checkbox" id="mxRdDif" onchange="mxAnalises()" checked> diferenciar séries</label>
    <label class="sel"><input type="checkbox" id="mxRdCond" onchange="mxAnalises()" checked> condicionar aos watts</label>
    <label class="sel"><input type="checkbox" id="mxRdDer" onchange="mxAnalises()" title="O SmO2 é calculado de O2Hb e HHb: incluí-los testa se uma variável causa os seus próprios componentes."> incluir O2Hb/HHb</label>
    <label class="sel">Lag máx.
      <select id="mxRdLag" onchange="mxAnalises()"><option>3</option><option selected>5</option>
        <option>10</option></select></label>
    <label class="sel">Correlação mín.
      <select id="mxRdCorr" onchange="mxAnalises()"><option>0.2</option><option selected>0.3</option>
        <option>0.5</option></select></label>
    <span id="mxRdEstado" style="color:#8b949e;font-size:12px;"></span>
  </div>
  <details style="margin:2px 0 8px 0;">
    <summary style="cursor:pointer;font-size:11px;color:#8b949e;">Como escolher o lag e a correlação mínima</summary>
    <div style="font-size:11px;color:#8b949e;margin-top:6px;">
      <p><b>Os valores por omissão — lag 5 s, correlação 0,30 — são os mais
      defensáveis</b>, e é por isso que estão escolhidos. As outras opções
      servem para ver se o resultado aguenta, não para o melhorar.</p>
      <p><b>Lag máximo.</b> É o atraso máximo testado entre causa e efeito. A
      resposta do SmO2 à potência ronda 3–8 s e a da FC 10–30 s, por isso 5 s
      apanha a primeira e parte da segunda. <b>Lag 3</b> pode perder relações
      reais mais lentas. <b>Lag 10</b> apanha-as, mas cada lag acrescenta
      parâmetros ao modelo: com 10 lags e 5 canais são 50 coeficientes por
      teste, e o F sobe por sobre-ajuste em vez de por relação. Se aumentares
      o lag e aparecerem muitas arestas novas com F baixo, é ruído.</p>
      <p><b>Correlação mínima.</b> Filtra que pares chegam a ser testados.
      <b>0,20</b> testa quase tudo — mais pares, mais correcção de
      Benjamini-Hochberg a aplicar, e o corte de p fica mais exigente para
      todos. <b>0,50</b> testa só o óbvio e pode esconder relações fracas mas
      reais, sobretudo depois de condicionar aos watts, que já retira boa
      parte da variação partilhada.</p>
      <p><b>Teste de robustez:</b> se uma aresta desaparece ao mudares de 5
      para 3 ou de 0,30 para 0,50, não confies nela. As que sobrevivem às três
      combinações são as que valem.</p>
    </div>
  </details>
  <div id="mxRedeDetalhe">
    <div class="grid2">
      <div class="chartbox" style="position:relative;width:100%;">
        <canvas id="chMxRedeGrafo" height="240"></canvas>
        <div id="mxTipRedeGrafo" style="display:none;position:absolute;pointer-events:none;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
      </div>
      <div class="chartbox" style="position:relative;width:100%;">
        <canvas id="chMxRedePCR" height="240"></canvas>
      </div>
    </div>
    <div id="mxRede" style="overflow-x:auto;margin-top:6px;"></div>
  </div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">O que a rede diz e o que não diz</summary>
    <div style="font-size:11px;color:#8b949e;margin-top:6px;">
      <p>Adaptado do <b>PhysioNexus</b> (Evan Peikon). Correlação selecciona
      os pares, Granger dá-lhes direcção.</p>
      <p><b>Granger não é causalidade.</b> Mede precedência preditiva: se o
      passado de A ajuda a prever B além do que o passado de B já explica,
      A precede B. Num sistema com um controlador comum isso é uma pista,
      não uma prova.</p>
      <p><b>Condicionar aos watts</b> existe porque o protocolo é causa comum
      de tudo — os watts sobem por decisão tua e o resto responde. Sem
      condicionar, a rede redescobre o protocolo. A pergunta passa a ser: o
      canal A acrescenta poder preditivo sobre B <i>além do que a potência
      já explica</i>?</p>
      <p><b>Diferenciar</b> porque o Granger pressupõe séries estacionárias.
      Ou se diferenciam todas ou nenhuma — misturar compara níveis com
      variações.</p>
      <p>Os p são corrigidos por Benjamini-Hochberg. Pares onde ambos os
      sentidos passam e nenhum domina ficam marcados <b>ambíguos</b>.</p>
      <p><b>Canais mecânicos</b> — watts, cadência, torque, velocidade — entram
      só como controlo e nunca como nós da rede. São decisão tua, não resposta
      fisiológica, e testá-los como causas seria redescobrir o protocolo.</p>
      <p>O <b>limitador</b> sai do peso de cada sistema pelo F das arestas que
      dele partem menos as que nele chegam. Um sistema que só recebe está a
      responder; um que só emite está a impor o ritmo. Usa-se o F e não a
      contagem: uma aresta com F=169 e outra com F=17 não valem o mesmo.</p>
    </div>
  </details>
  </div>

  <div id="mxSub515A" style="display:none;">
  <h2 style="font-size:15px;margin-top:18px;">Interpretação 5-1-5 — limitador</h2>
  <div class="controls" style="flex-wrap:wrap;gap:6px 12px;">
    <button onclick="mx515()">Avaliar</button>
    <label class="sel">"Clear" acima de
      <select id="mx515Claro" onchange="mxAnalises()"><option>5</option><option selected>10</option>
        <option>15</option></select>% da amplitude</label>
    <label class="sel"><input type="checkbox" id="mx515Rep" onchange="mxAnalises()" title="O protocolo repete o mesmo escalão de carga? Se não, as perguntas 8A e 13 não se aplicam."> tem carga repetida</label>
    <label class="sel">Parte final
      <select id="mx515Frac" onchange="mxAnalises()"><option value="0.33">último terço</option>
        <option value="0.5" selected>última metade</option>
        <option value="1">tudo</option></select></label>
    <span id="mx515Estado" style="color:#8b949e;font-size:12px;"></span>
  </div>
  <div id="mx515Detalhe"><div id="mx515" style="overflow-x:auto;margin-top:6px;"></div></div>

  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Como funciona a 5-1-5</summary>
    <div style="font-size:11px;color:#8b949e;margin-top:6px;">
      <p>Portado do <b>515 Interpretation Tool v2.2</b>. O original faz 13
      perguntas sobre o que vês nos gráficos; aqui são medidas dos blocos, e
      podes corrigir cada uma.</p>
      <p><b>U/S — Utilização vs Fornecimento.</b> SmO2 de trabalho alto
      significa que o músculo não extrai o que lhe chega: limitação de
      utilização. SmO2 abaixo de 20% e a continuar a descer: extracção no
      limite, falta entrega.</p>
      <p><b>P/C — Pulmonar vs Cardíaco.</b> THb e SmO2 de repouso a subir, com
      atraso na resposta, apontam ao lado ventilatório. THb a descer ao longo
      da sessão aponta ao volume de sangue local.</p>
      <p><b>Duas correcções face ao ficheiro original:</b> as linhas 29–32 do
      motor de cálculo referenciavam as linhas erradas, deslocando o eixo P/C
      em toda a secção de FC; e o máximo de U/S estava fixo em 11, que só é
      atingível com dois sensores — com um, o denominador é 9.</p>
      <p>Os cortes entre "clear" e "slight" não existem no original: pedia ao
      utilizador que olhasse e decidisse. Aqui são explícitos e ajustáveis.</p>
    </div>
  </details>

  </div>
  </div>

  <div id="mxSubPrincipalB">
  <details style="margin-top:10px;">
    <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Todas as sessões</summary>
    <div id="mxLista" style="overflow-x:auto;margin-top:6px;"></div>
  </details>
  </div>

  <div id="mxSubVerificacao" style="display:none;">
    <h2 style="font-size:15px;margin-top:18px;">Verificação — protocolo VST</h2>

    <h3 style="font-size:13px;margin-top:6px;">Verificações salvas</h3>
    <div id="mxVstConjuntosSalvos" style="margin-top:6px;"></div>

    <p class="sub" style="font-size:12px;margin-top:14px;">Verifica se os intervalos reais
      de uma sessão com a tag <b>VST</b> mostram uma resposta fisiológica
      compatível com BP1/BP2 — sempre a partir do que foi realmente feito,
      nunca de watts ou durações fixas.</p>
    <div class="controls">
      <label class="sel">Sessão VST
        <select id="mxVstSelect" onchange="mxVstCarregar()">
          <option value="">a carregar…</option>
        </select></label>
      <span id="mxVstEstado" style="color:#8b949e;font-size:12px;"></span>
    </div>

    <div class="controls" style="margin-top:6px;">
      <label class="sel">Sessão Moxy correspondente
        <select id="mxVstMoxySelect" onchange="mxVstMoxySelecionado()">
          <option value="">escolhe uma sessão VST primeiro</option>
        </select></label>
      <button onclick="mxVstSincronizar()">Comparar / Sincronizar</button>
    </div>
    <div id="mxVstConjuntoEstado" style="margin-top:6px;"></div>

    <h3 style="font-size:14px;margin-top:16px;">Comparação — Dia 1 × Dia 2</h3>
    <div id="mxVstResumoCartoes" style="margin-top:8px;"></div>
    <div id="mxVstRpe" style="margin-top:10px;"></div>

    <div id="mxVstTemporalToggles" style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;font-size:11px;">
      <label style="display:flex;align-items:center;gap:3px;cursor:pointer;">
        <input type="checkbox" checked onchange="mxVstToggleTemporal('power',this.checked)">
        <span style="color:#c9d1d9;">Power (W)</span></label>
      <label style="display:flex;align-items:center;gap:3px;cursor:pointer;">
        <input type="checkbox" checked onchange="mxVstToggleTemporal('heartrate',this.checked)">
        <span style="color:#E3B341;">HR (bpm)</span></label>
      <label style="display:flex;align-items:center;gap:3px;cursor:pointer;">
        <input type="checkbox" checked onchange="mxVstToggleTemporal('respiration',this.checked)">
        <span style="color:#79C0FF;">RF (resp/min)</span></label>
      <label style="display:flex;align-items:center;gap:3px;cursor:pointer;">
        <input type="checkbox" checked onchange="mxVstToggleTemporal('smo2',this.checked)">
        <span style="color:#F85149;">SmO2 (%)</span></label>
    </div>
    <div class="chartbox" style="position:relative;width:100%;margin-top:6px;">
      <canvas id="chMxVstTemporal" height="240"></canvas>
      <div id="mxTipVstTemporal" style="display:none;position:absolute;pointer-events:none;
        background:#161b22;border:1px solid #30363d;border-radius:6px;
        padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
    </div>

    <h3 style="font-size:14px;margin-top:20px;">Resposta fisiológica</h3>
    <div style="display:flex;flex-wrap:wrap;gap:10px;">
      <div style="flex:1;min-width:280px;">
        <h4 style="font-size:12px;color:#8b949e;margin:0 0 2px;">Power × HR</h4>
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxVstHR" height="170"></canvas>
          <div id="mxTipVstHR" class="mxTipVst" style="display:none;position:absolute;pointer-events:none;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
      </div>
      <div style="flex:1;min-width:280px;">
        <h4 style="font-size:12px;color:#8b949e;margin:0 0 2px;">Power × RF</h4>
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxVstRF" height="170"></canvas>
          <div id="mxTipVstRF" class="mxTipVst" style="display:none;position:absolute;pointer-events:none;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
      </div>
      <div style="flex:1;min-width:280px;">
        <h4 style="font-size:12px;color:#8b949e;margin:0 0 2px;">Power × SmO2</h4>
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxVstSmO2" height="170"></canvas>
          <div id="mxTipVstSmO2" class="mxTipVst" style="display:none;position:absolute;pointer-events:none;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
        <p class="sub" style="font-size:9px;margin:2px 0 0;">SmO2 é indicador periférico — a queda pode reflectir extracção e/ou fluxo sanguíneo.</p>
      </div>
      <div style="flex:1;min-width:280px;">
        <h4 style="font-size:12px;color:#8b949e;margin:0 0 2px;">Power × THb <span class="sub" style="font-size:9px;">(contextual)</span></h4>
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxVstTHb" height="170"></canvas>
          <div id="mxTipVstTHb" class="mxTipVst" style="display:none;position:absolute;pointer-events:none;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
      </div>
      <div style="flex:1;min-width:280px;">
        <h4 style="font-size:12px;color:#8b949e;margin:0 0 2px;">Power × DFA1 <span class="sub" style="font-size:9px;">(complementar)</span></h4>
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxVstDFA1" height="170"></canvas>
          <div id="mxTipVstDFA1" class="mxTipVst" style="display:none;position:absolute;pointer-events:none;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
        <p class="sub" style="font-size:9px;margin:2px 0 0;">Depende da qualidade/estacionariedade dos RR durante carga crescente.</p>
      </div>
      <div style="flex:1;min-width:280px;">
        <h4 style="font-size:12px;color:#8b949e;margin:0 0 2px;">Power × RPE <span class="sub" style="font-size:9px;">(complementar)</span></h4>
        <div class="chartbox" style="position:relative;width:100%;">
          <canvas id="chMxVstRPE" height="170"></canvas>
          <div id="mxTipVstRPE" class="mxTipVst" style="display:none;position:absolute;pointer-events:none;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
        </div>
        <p class="sub" style="font-size:9px;margin:2px 0 0;">Percepção subjectiva de esforço (1–10) por WORK — evidência complementar, não determina BP sozinha.</p>
      </div>
    </div>

    <h3 style="font-size:14px;margin-top:20px;">Recovery</h3>
    <div id="mxVstRecoveryCartoes" style="margin-top:8px;"></div>
    <div class="controls" style="margin-top:6px;">
      <label class="sel">Métrica
        <select id="mxVstRecMetrica" onchange="mxDesenharVstRecoveryTempo(MX_VST_ULT_COMP)">
          <option value="hr">HR</option><option value="respiracao">RF</option>
          <option value="smo2">SmO2</option><option value="thb">THb</option>
          <option value="dfa1">DFA1</option>
        </select></label>
    </div>
    <div class="chartbox" style="position:relative;width:100%;margin-top:6px;">
      <canvas id="chMxVstRecoveryTempo" height="180"></canvas>
      <div id="mxTipVstRecTempo" style="display:none;position:absolute;pointer-events:none;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;font-size:11px;color:#c9d1d9;z-index:5;"></div>
    </div>
    <p class="sub" style="font-size:10px;margin-top:2px;">A janela sombreada (0–60s) é usada na comparação directa Dia 1 × Dia 2 — o recovery completo fica sempre visível.</p>

    <h3 style="font-size:14px;margin-top:20px;">Timing</h3>
    <div class="grid2">
      <div class="chartbox"><div class="legend"><span>BP1 — Δ% por WORK</span></div>
        <canvas id="chVstTimingBP1" height="200"></canvas></div>
      <div class="chartbox"><div class="legend"><span>BP2 — Δ% por WORK</span></div>
        <canvas id="chVstTimingBP2" height="200"></canvas></div>
    </div>

    <h3 style="font-size:14px;margin-top:20px;">Heatmap</h3>
    <div class="grid2">
      <div class="chartbox"><div class="legend"><span>BP1</span></div>
        <canvas id="chVstHeatBP1" height="200"></canvas></div>
      <div class="chartbox"><div class="legend"><span>BP2</span></div>
        <canvas id="chVstHeatBP2" height="200"></canvas></div>
    </div>

    <details style="margin-top:16px;">
      <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Detalhes da comparação</summary>
      <div id="mxVstCartoes" style="margin-top:10px;"></div>
      <div id="mxVstTabela" style="margin-top:14px;overflow-x:auto;"></div>
      <div id="mxVstComparacao" style="margin-top:8px;"></div>
      <div id="mxVstRpeTabela" style="margin-top:14px;overflow-x:auto;"></div>
      <div id="mxVstRecoveryTabela" style="margin-top:14px;overflow-x:auto;"></div>
    </details>

    <details style="margin-top:10px;">
      <summary style="cursor:pointer;font-size:13px;color:#8b949e;padding:4px 0;">Limitações</summary>
      <div id="mxVstLimitacoes" style="margin-top:8px;"></div>
    </details>

    <div id="mxVstRecoveryFinal" style="margin-top:12px;"></div>
  </div>

</div>
"""

JS = """
let MX = null, MX_SESSOES = [], MX_ESC = null;
let MX_CORTE = null;   // [inicio_s, fim_s]

// Sub-tabs dentro da tab Moxy: Principal (o que ja' estava, nunca
// escondido de proposito), Limiares, Intervencoes, Rede causal. Os
// blocos de Limiares/Intervencoes/Rede tem id's proprios -- escondem-se
// por omissao; a Principal e' so' "o que sobra visivel" quando os
// outros tres estao escondidos.
const MX_SUBTAB_IDS = {
 principal: ['mxSubPrincipalA', 'mxSubPrincipalB'],
 limiares: ['mxLimiaresBloco', 'mxSub515A'],
 intervencoes: ['mxSubIntervencoesA'],
 rede: ['mxSubRedeA'],
 verificacao: ['mxSubVerificacao'],
};
function mxMudarSubTab(nome){
 Object.keys(MX_SUBTAB_IDS).forEach(function(k){
  MX_SUBTAB_IDS[k].forEach(function(id){
   const el=document.getElementById(id);
   if(el) el.style.display = (k===nome) ? '' : 'none';
  });
 });
 const btns={principal:'mxSubBtnPrincipal', limiares:'mxSubBtnLimiares',
            intervencoes:'mxSubBtnIntervencoes', rede:'mxSubBtnRede',
            verificacao:'mxSubBtnVerificacao'};
 Object.keys(btns).forEach(function(k){
  const b=document.getElementById(btns[k]);
  if(!b) return;
  if(k===nome){
   b.style.background='#1c2331'; b.style.borderColor='#5DADE2'; b.style.color='#5DADE2';
  } else {
   b.style.background='#161b22'; b.style.borderColor='#30363d'; b.style.color='#8b949e';
  }
 });
 // Redesenhar DEPOIS de o display:none ter sido tirado -- os canvas
 // so' teem largura real com o contentor ja visivel. Sem isto, o
 // grafico ficava em branco ate' o utilizador clicar "actualizar
 // sessao" (que forca um novo mxLimiares() e, de caminho, desenha
 // com o contentor entretanto visivel).
 if(nome==='limiares' && MX_ULT_LIMIARES_D){
  mxDesenharLimiaresSmo2(MX_ULT_LIMIARES_D);
  mxDesenharDmax(MX_ULT_LIMIARES_D);
  mxDesenharDfa1(MX_ULT_LIMIARES_D.dfa1);
 }
 if(nome==='intervencoes' && MX_ULT_PLANO){
  mxDesenharZonas(MX_ULT_PLANO, MX_ULT_RPE_D, MX_ULT_ZONAS_D);
 }
 if(nome==='verificacao' && !MX_VST_LISTA_CARREGADA){
  MX_VST_LISTA_CARREGADA = true;
  mxVstCarregarLista();
  mxVstCarregarConjuntosSalvos();
 }
}

let MX_VST_LISTA_CARREGADA = false;

const MX_CORES = {smo2:'#F85149', thb:'#58A6FF', o2hb:'#3FB950',
                  hhb:'#A371F7', watts:'#6e7681', heartrate:'#E3B341',
                  respiration:'#79C0FF', dfa_a1:'#D2A8FF',
                  cadence:'#F0883E', velocity_smooth:'#3FB950',
                  torque:'#8b949e',
                  wprime:'#E3B341', mprime:'#F85149',
                  hhb_calc:'#A371F7'};
// Escalas muito diferentes no mesmo grafico ficariam ilegiveis: o SmO2 anda
// nos 60, a potencia nos 250 e o DFA-a1 abaixo de 2. Cada canal e' normalizado
// ao seu proprio intervalo para o desenho, e o hover mostra sempre o valor
// real.
let MX_ON = {};

function mxActualizar(soVer){
 const est=document.getElementById('mxEstado');
 const nota=document.getElementById('mxCorteNota');
 est.textContent = soVer ? 'a verificar...' : 'a reconciliar...';
 fetch('/api/moxy/actualizar?dias=1095' + (soVer?'&so_diagnostico=1':''))
 .then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ est.textContent='erro: '+(d.mensagem||''); return; }
  est.textContent = (soVer?'[verificação] ':'')
   + d.na_api+' na API · '+d.na_base_local+' locais · '
   + d.n_novas+' novas · '+d.n_orfas+' órfãs'
   + (soVer?'' : ' · '+(d.gravadas||0)+' gravadas');
  let h = 'Janela ' + (d.janela||[]).join(' a ')
   + ' em ' + (d.blocos_pedidos||[]).length + ' blocos de '
   + d.bloco_dias + ' dias: '
   + (d.blocos_pedidos||[]).map(function(b){ return b.de+' ('+b.n+')'; })
     .join(' · ');
  if((d.erros_api||[]).length)
   h += '<br><span style="color:#F85149;">Erros da API: '
     + d.erros_api.map(function(e){ return e.de+' — '+e.erro; }).join(' · ')
     + '</span>';
  if(d.n_novas) h += '<br><b>Novas:</b> ' + (d.novas||[]).join(', ');
  if(d.n_orfas) h += '<br><b>Órfãs' + (soVer?' (a remover)':' removidas')
   + ':</b> ' + (d.orfas||[]).join(', ');
  if(d.erro_gravar) h += '<br><span style="color:#F85149;">Erro ao gravar: '
   + d.erro_gravar + '</span>';
  if(d.descartadas_sem_data) h += '<br>' + d.descartadas_sem_data
   + ' descartadas por não terem data válida.';
  nota.innerHTML = h;
  if(!soVer) mxSessoes();
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

function mxSessoes(){
 const mod = document.getElementById('mxModalidade').value;
 const est = document.getElementById('mxEstado');
 est.textContent = 'a procurar sessões...';
 fetch('/api/moxy/sessoes' + (mod ? '?modalidade=' + mod : ''))
 .then(r=>r.json()).then(function(d){
  if(d.status !== 'ok'){ est.textContent = d.mensagem || 'erro'; return; }
  MX_SESSOES = d.sessoes || [];
  est.textContent = MX_SESSOES.length + ' sessões com Moxy';
  if(!MX_SEL.length && MX_SESSOES.length) MX_SEL=[String(MX_SESSOES[0].id)];
  MX_SEL = MX_SEL.filter(id=>MX_SESSOES.some(x=>String(x.id)===id));
  mxDatasChips(); mxLista();
  if(MX_SEL.length) mxCarregar();
  else { MX=null; MX_DADOS={}; mxDraw(); mxBlocosTabela();
         document.getElementById('mxDiag').innerHTML=''; }
 }).catch(e=>{ est.textContent = 'erro: ' + e.message; });
}




function mxTabelaDegraus(){
 const box=document.getElementById('mxDiag');
 if(!MXC||!box) return;
 const pares=MXC.degraus_emparelhados||[];
 if(!pares.length) return;
 const canal=(MX.canais_nirs||[]).find(k=>k.indexOf('smo2')===0)
   ? 'smo2' : (MX.canais_nirs[0]||'').split('_')[0];
 let h='<h3 style="font-size:13px;color:#8b949e;margin:12px 0 4px 0;">'
  +'Degraus emparelhados por potência</h3>'
  +'<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Watts</th>'
  + MXC.sessoes.map(function(s){
     return '<th style="padding-right:14px;">#'+s.indice+' '+canal+'</th>'; }).join('')
  +'<th>Δ</th></tr>';
 pares.forEach(function(p){
  const vs=MXC.sessoes.map(function(s){
   const d=p.por_sessao[String(s.indice-1)];
   return d ? d[canal] : null; });
  const validos=vs.filter(v=>v!=null);
  const delta=validos.length>1
   ? Math.round((validos[validos.length-1]-validos[0])*10)/10 : null;
  h+='<tr><td style="padding-right:14px;">'+p.watts_centro+' W'
   +(p.watts_min!==p.watts_max
     ? ' <span style="color:#6e7681;">('+p.watts_min+'–'+p.watts_max+')</span>'
     : '')+'</td>'
   + vs.map(function(v){ return '<td style="padding-right:14px;">'
       +(v!=null?v:'—')+'</td>'; }).join('')
   +'<td style="color:'+(delta==null?'#8b949e':delta>0?'#3FB950':'#F85149')+';">'
   +(delta==null?'—':(delta>0?'+':'')+delta)+'</td></tr>';
 });
 h+='</table><p style="color:#8b949e;font-size:11px;">Média de cada degrau, '
  +'descartando os primeiros 30 s: o SmO2 leva tempo a responder a uma '
  +'mudança de carga, e incluir a transição mistura o degrau novo com o '
  +'anterior. Δ = última sessão menos a primeira.</p>';
 box.innerHTML = h + box.innerHTML;
}

// MX_SEL: ids escolhidos. MX_DADOS: {id: resposta}. MX_OFF: desvio manual
// em segundos por id. Com uma sessao so', tudo funciona como antes.
let MX_SEL = [], MX_DADOS = {}, MX_OFF = {}, MX_ALINHA_AVISO = {};

let MX515_EDIT = {};

// Com uma sessao, mostra tudo. Com varias, corre as duas analises em cada
// uma e mostra so' os cartoes, mais o consenso entre elas.
function mxAnalises(){
 const ids=Object.keys(MX_DADOS);
 const uni=document.getElementById('mxAnaliseUnica');
 const res=document.getElementById('mxResumo');
 if(!ids.length){ if(res) res.innerHTML=''; return; }
 if(ids.length===1){
  if(uni) uni.style.display='';
  if(res) res.innerHTML='';
  mxRede(); mx515();
  return;
 }
 if(uni) uni.style.display='none';
 mxComparativo(ids);
}

function mxParams(){
 const rd='?lag='+document.getElementById('mxRdLag').value
  +'&corr='+document.getElementById('mxRdCorr').value
  +(document.getElementById('mxRdDif').checked?'':'&diferenciar=0')
  +(document.getElementById('mxRdCond').checked?'':'&condicionar=0')
  +(document.getElementById('mxRdDer').checked?'&derivados=1':'');
 const it='?claro='+document.getElementById('mx515Claro').value
  +'&fraccao='+document.getElementById('mx515Frac').value
  +'&repetida='+(document.getElementById('mx515Rep').checked?'1':'0');
 return {rd:rd, it:it};
}

function mxComparativo(ids){
 const res=document.getElementById('mxResumo');
 const est=document.getElementById('mxEstado');
 const P=mxParams();
 res.innerHTML='<p style="color:#8b949e;font-size:12px;">a analisar '
  +ids.length+' sessões...</p>';
 Promise.all(ids.map(function(id){
  const c=mxCorteDe(id);
  const jan='&inicio='+Math.round(c[0])+'&fim='+Math.round(c[1]);
  return Promise.all([
   fetch('/api/moxy/rede/'+id+P.rd+jan).then(r=>r.json()).catch(e=>({status:'erro',mensagem:e.message})),
   fetch('/api/moxy/interpretacao/'+id+P.it+jan).then(r=>r.json()).catch(e=>({status:'erro',mensagem:e.message}))
  ]).then(function(par){ return {id:id, rede:par[0], it:par[1]}; });
 })).then(function(rs){
  let h='<h2 style="font-size:15px;">Comparação — '+rs.length+' sessões</h2>';
  h+='<div style="display:flex;flex-wrap:wrap;gap:12px;">';
  rs.forEach(function(x, si){
   const s2=MX_SESSOES.find(y=>String(y.id)===x.id)||{};
   h+='<div style="flex:1;min-width:300px;border:1px solid #21262d;'
    +'border-radius:6px;padding:8px 10px;">'
    +'<b style="color:'+mxCorSessao(si)+';">'+(si+1)+'· '+(s2.data||x.id)
    +'</b> <span style="color:#8b949e;font-size:11px;">'
    +(s2.modalidade||'')+'</span>';
   h+=mxCardRede(x.rede)+mxCard515(x.it)+'</div>';
  });
  h+='</div>';
  h+=mxConsenso(rs);
  res.innerHTML=h;
  est.textContent=rs.length+' sessões analisadas';
 });
}

function mxCardRede(d){
 if(!d || d.status!=='ok'){
  return '<p style="color:#F0883E;font-size:11px;">rede: '
   +((d&&(d.motivo||d.mensagem))||'sem dados')+'</p>'; }
 const L=d.limitador||{};
 const cores={periferico:'#F85149',cardiaco:'#58A6FF',
              respiratorio:'#3FB950',autonomico:'#A371F7'};
 const cor=cores[L.sistema]||'#8b949e';
 const cp=L.controlo_pct||{};
 const ks=Object.keys(cp).sort(function(a,b){ return cp[b]-cp[a]; });
 return '<div style="border-left:3px solid '+cor+';padding:5px 9px;'
  +'margin-top:8px;"><b style="color:'+cor+';font-size:12px;">REDE CAUSAL: '
  +(L.sistema?L.sistema.toUpperCase():'indeterminado')+'</b>'
  +'<br><span style="font-size:11px;">'+(L.leitura||'')+'</span>'
  +(ks.length?'<br><span style="font-size:10px;color:#8b949e;">'
    +ks.map(function(k){ return k+' '+cp[k]+'%'; }).join(' · ')
    +' · '+d.n_dirigidas+' arestas</span>':'')
  +'</div>';
}

function mxCard515(d){
 if(!d || d.status!=='ok'){
  return '<p style="color:#F0883E;font-size:11px;">5-1-5: '
   +((d&&(d.motivo||d.mensagem))||'sem dados')+'</p>'; }
 const p=d.pontuacao, i=d.interpretacao;
 const cor=v=>v==='Utilização'||v==='Pulmonar'?'#58A6FF'
   :v==='Fornecimento'||v==='Cardíaco'?'#F85149':'#8b949e';
 let h='';
 [['Utilização vs Fornecimento',p.us,i.us],
  ['Pulmonar vs Cardíaco',p.pc,i.pc]].forEach(function(b){
  if(!b[2]) return;
  h+='<div style="border-left:3px solid '+cor(b[2].limitador)+';'
   +'padding:5px 9px;margin-top:8px;">'
   +'<span style="color:#8b949e;font-size:10px;">'+b[0]+'</span><br>'
   +'<b style="color:'+cor(b[2].limitador)+';font-size:12px;">'
   +b[2].limitador+'</b> <span style="color:#8b949e;font-size:11px;">'
   +b[1].pontos+'/'+b[1].max+' = '+(b[1].score!=null?b[1].score:'—')
   +'</span><br><span style="font-size:11px;">'+b[2].texto+'</span>'
   +(b[2].o_que_treinar?'<br><span style="font-size:10px;color:#8b949e;">'
     +'→ '+b[2].o_que_treinar+'</span>':'')
   +'</div>';
 });
 return h;
}

// Consenso: qual limitador aparece mais vezes, e com que concordancia.
// Se as sessoes discordarem, diz-se isso em vez de forcar um vencedor --
// discordancia entre sessoes e' informacao sobre a variabilidade do
// atleta, ou sobre a qualidade dos dados, e nao ruido a esconder.
function mxConsenso(rs){
 const eixos={rede:{}, us:{}, pc:{}};
 let nR=0, nI=0;
 rs.forEach(function(x){
  if(x.rede && x.rede.status==='ok'){
   const s2=(x.rede.limitador||{}).sistema;
   if(s2){ eixos.rede[s2]=(eixos.rede[s2]||0)+1; nR++; }
  }
  if(x.it && x.it.status==='ok'){
   const i=x.it.interpretacao||{};
   if(i.us){ eixos.us[i.us.limitador]=(eixos.us[i.us.limitador]||0)+1; }
   if(i.pc){ eixos.pc[i.pc.limitador]=(eixos.pc[i.pc.limitador]||0)+1; }
   nI++;
  }
 });
 function linha(nome, mapa, n){
  const ks=Object.keys(mapa).sort(function(a,b){ return mapa[b]-mapa[a]; });
  if(!ks.length) return '<li>'+nome+': sem resultados</li>';
  const top=ks[0], c=mapa[top];
  const pct=n?Math.round(c/n*100):0;
  const cor=pct>=70?'#3FB950':pct>=50?'#F0883E':'#F85149';
  return '<li>'+nome+': <b style="color:'+cor+';">'+top+'</b> em '
   +c+' de '+n+' sessões ('+pct+'%)'
   +(ks.length>1?' <span style="color:#8b949e;">— também '
     +ks.slice(1).map(function(k){ return k+' ('+mapa[k]+')'; }).join(', ')
     +'</span>':'')
   +'</li>';
 }
 let h='<div style="border:1px solid #30363d;border-radius:6px;'
  +'padding:8px 12px;margin-top:12px;">'
  +'<b>Consenso entre as '+rs.length+' sessões</b>'
  +'<ul style="font-size:12px;margin:6px 0;padding-left:18px;">'
  +linha('Rede causal', eixos.rede, nR)
  +linha('Utilização vs Fornecimento', eixos.us, nI)
  +linha('Pulmonar vs Cardíaco', eixos.pc, nI)
  +'</ul>'
  +'<p style="color:#8b949e;font-size:11px;margin:0;">Verde acima de 70% de '
  +'concordância, laranja acima de 50%, vermelho abaixo. <b>Concordância '
  +'baixa não é falha do método</b> — ou o limitador mudou entre sessões, ou '
  +'os protocolos não são comparáveis, ou a qualidade dos dados varia. Vale '
  +'mais saber isso do que ver uma média que esconde a discordância.</p>'
  +'</div>';
 return h;
}

function mx515(){
 const ids=Object.keys(MX_DADOS);
 const est=document.getElementById('mx515Estado');
 const box=document.getElementById('mx515');
 if(!ids.length){ est.textContent='escolhe uma sessão'; return; }
 const id=ids[0];
 const c=mxCorteDe(id);
 let q=mxParams().it+'&inicio='+Math.round(c[0])+'&fim='+Math.round(c[1]);
 Object.keys(MX515_EDIT).forEach(function(k){
  q+='&resp_'+k+'='+encodeURIComponent(MX515_EDIT[k]); });
 est.textContent='a avaliar...';
 fetch('/api/moxy/interpretacao/'+id+q).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ est.textContent=d.motivo||d.mensagem||'sem dados';
   box.innerHTML=''; return; }
  const p=d.pontuacao, i=d.interpretacao, m=d.medicoes;
  // guardar os dois eixos para a síntese os cruzar com a rede causal
  MX_ULT_US=(i.us||{}).limitador||null;
  MX_ULT_PC=(i.pc||{}).limitador||null;
  if(typeof mxSintese==='function') mxSintese();
  est.textContent=m.n_blocos_usados+' de '+m.n_blocos_trabalho
   +' blocos usados'+(d.respostas_editadas?' · com respostas editadas':'');

  const cor=v=>v==='Utilização'||v==='Pulmonar'?'#58A6FF'
    :v==='Fornecimento'||v==='Cardíaco'?'#F85149':'#8b949e';
  let h='<div style="display:flex;flex-wrap:wrap;gap:12px;">';
  [['Utilização vs Fornecimento',p.us,i.us],
   ['Pulmonar vs Cardíaco',p.pc,i.pc]].forEach(function(bl){
   if(!bl[2]) return;
   h+='<div style="flex:1;min-width:280px;border-left:3px solid '
    +cor(bl[2].limitador)+';padding:6px 10px;">'
    +'<span style="color:#8b949e;font-size:11px;">'+bl[0]+'</span><br>'
    +'<b style="color:'+cor(bl[2].limitador)+';font-size:15px;">'
    +bl[2].limitador+'</b> '
    +'<span style="color:#8b949e;font-size:11px;">'+bl[1].pontos+'/'
    +bl[1].max+' = '+(bl[1].score!=null?bl[1].score:'—')+'</span>'
    +'<br><span style="font-size:11px;">'+bl[2].texto+'</span></div>';
  });
  h+='</div>';
  (i.reservas||[]).forEach(function(r2){
   h+='<p style="color:#F0883E;font-size:11px;margin:4px 0;">⚠ '+r2+'</p>'; });
  (d.avisos||[]).forEach(function(a){
   h+='<p style="color:#F85149;font-size:11px;margin:4px 0;">⚠ '+a+'</p>'; });

  // as 13 perguntas com as figuras ficam em dropdown: os dois cartões
  // acima são a resposta, isto é a auditoria de como se lá chegou
  h+='<details style="margin-top:8px;"><summary style="cursor:pointer;'
   +'font-size:12px;color:#8b949e;padding:4px 0;">As 13 perguntas, com o '
   +'padrão de cada resposta</summary><div style="margin-top:6px;">';
  h+='<table style="width:100%;border-collapse:collapse;font-size:11px;'
   +'"><tr style="color:#8b949e;text-align:left;'
   +'border-bottom:1px solid #21262d;"><th style="padding:5px;">#</th>'
   +'<th>Pergunta</th><th>Padrão</th><th>Medido</th><th>Resposta</th><th>Pontos</th>'
   +'<th>Eixo</th></tr>';
  [['us',p.us.detalhe],['pc',p.pc.detalhe]].forEach(function(par){
   par[1].forEach(function(dd){
    const md=(m.respostas||{})[dd.pergunta]||{};
    const opcoes = dd.pergunta==='2A' ? d.faixas_2A
                 : dd.pergunta==='9' ? d.faixas_9 : d.niveis;
    const val = md.valor!=null ? md.valor
      : md.declive_pct_da_amplitude!=null
        ? (md.declive_pct_da_amplitude>0?'+':'')+md.declive_pct_da_amplitude+'%'
      : md.atraso_mediano_s!=null ? md.atraso_mediano_s+'s' : '—';
    h+='<tr style="border-bottom:1px solid #161b22;'
     +(md.editada?'background:rgba(227,179,65,0.07);':'')+'">'
     +'<td style="padding:5px;color:#8b949e;">'+dd.pergunta+'</td>'
     +'<td style="color:#8b949e;">'+dd.texto
     +((d.onde_mede||{})[dd.pergunta]
       ? '<br><span style="color:#6e7681;font-size:10px;">medido no '
         +d.onde_mede[dd.pergunta]+'</span>' : '')+'</td>'
     +'<td>'+(((d.figuras||{})[dd.pergunta]||{})[dd.resposta]
              || '<span style="color:#484f58;font-size:10px;">—</span>')+'</td>'
     +'<td style="color:#8b949e;">'+val+'</td>'
     +'<td><select class="mx515R" data-q="'+dd.pergunta+'" '
     +'style="font-size:11px;max-width:150px;">'
     + (opcoes||[]).map(function(o){
        return '<option'+(o===dd.resposta?' selected':'')+'>'+o+'</option>';
       }).join('')
     + (dd.resposta==null?'<option selected>—</option>':'')
     +'</select></td>'
     +'<td style="color:'+(dd.nao_aplicavel?'#6e7681'
        :dd.pontos==null?'#F0883E'
        :dd.pontos<0?'#F85149':'#c9d1d9')+';">'
     +(dd.nao_aplicavel?'não se aplica'
       :dd.pontos!=null?dd.pontos+' / '+dd.max:'sem resposta / '+dd.max)+'</td>'
     +'<td style="color:#8b949e;">'+par[0].toUpperCase()+'</td></tr>';
   });
  });
  h+='</table>';
  h+='<p style="color:#8b949e;font-size:11px;margin-top:6px;">'
   +'Alterar uma resposta recalcula tudo. "Medido" mostra o valor ou o '
   +'declive em % da amplitude do canal na sessão — é isso que decide entre '
   +'"clear" e "slight". Cortes actuais: claro acima de '
   +d.cortes.claro_pct+'%, ligeiro acima de '+d.cortes.ligeiro_pct+'%. '
   +'Valores de repouso e de trabalho medidos nos últimos '
   +(m.repouso_seg||30)+' s de cada bloco — o início ainda está em '
   +'transição, e o patamar é o que a pergunta procura. Na figura, os '
   +'pontos cinzentos marcam exactamente onde a tendência foi tirada: em '
   +'cima nas perguntas de repouso, em baixo nas de trabalho.</p>';
  h+='</div></details>';
  box.innerHTML=h;
  Array.prototype.forEach.call(box.querySelectorAll('.mx515R'), function(el){
   el.addEventListener('change', function(){
    MX515_EDIT[el.getAttribute('data-q')]=el.value; mx515(); });
  });
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

// Em comparacao esconde-se o detalhe: com 4 sessoes seriam 4 tabelas de
// arestas e 4 de 13 perguntas. Ficam os cartoes e o consenso.
function mxModoUnico(unico){
 // o cartão de intervenções acompanha o modo: em sessão única sai do
 // cruzamento dos três métodos, em comparação sai do consenso entre
 // sessões (que o mxResumo escreve no seu próprio bloco)
 const _bi=document.getElementById('mxIntervencoes');
 if(_bi){ _bi.style.display = unico ? '' : 'none';
          if(!unico) _bi.innerHTML=''; }
 ['mxRedeDetalhe','mx515Detalhe','mxLimiaresBloco'].forEach(function(id){
  const e=document.getElementById(id);
  if(e) e.style.display = unico ? '' : 'none';
 });
 const r=document.getElementById('mxResumoBloco');
 if(r) r.style.display = unico ? 'none' : '';
}

function mxResumo(){
 const ids=Object.keys(MX_DADOS);
 const box=document.getElementById('mxResumo');
 const est=document.getElementById('mxResumoEstado');
 if(!box) return;
 if(ids.length<2){ box.innerHTML=''; return; }
 const q='?ids='+ids.join(',')
  +'&lag='+document.getElementById('mxRdLag').value
  +'&corr='+document.getElementById('mxRdCorr').value
  +'&claro='+document.getElementById('mx515Claro').value
  +'&fraccao='+document.getElementById('mx515Frac').value;
 est.textContent='a analisar '+ids.length+' sessões...';
 fetch('/api/moxy/resumo'+q).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ est.textContent=d.mensagem||'erro'; return; }
  est.textContent=d.n_sessoes+' sessões analisadas';
  const cons=d.consenso||{};
  const rot={rede:'Rede causal',us:'Utilização vs Fornecimento',
             pc:'Pulmonar vs Cardíaco'};
  let h='<div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:10px;">';
  ['rede','us','pc'].forEach(function(k){
   const c=cons[k]||{};
   if(!c.mais_comum){ return; }
   const forte=c.concordancia_pct>=70;
   const cor=forte?'#3FB950':'#F0883E';
   h+='<div style="flex:1;min-width:230px;border:1px solid '+cor+';'
    +'border-radius:6px;padding:8px 10px;">'
    +'<span style="color:#8b949e;font-size:11px;">'+rot[k]+'</span><br>'
    +'<b style="font-size:16px;color:'+cor+';">'+c.mais_comum+'</b><br>'
    +'<span style="font-size:11px;color:#8b949e;">'+c.n+' de '+c.de
    +' sessões ('+c.concordancia_pct+'%)'
    +(c.unanime?' · unânime':'')+'</span><br>'
    +'<span style="font-size:10px;color:#8b949e;">'
    +Object.keys(c.contagem).map(function(x){
      return x+': '+c.contagem[x]; }).join(' · ')+'</span></div>';
  });
  h+='</div>';
  h+='<p style="color:#8b949e;font-size:11px;">'+(d.nota||'')+'</p>';

  // ── tabela longitudinal de limiares ───────────────────────────────
  // A literatura e' consistente: o breakpoint de SmO2 nao substitui o VT2
  // (Osmani 2023, Possamai 2024, Arnold, Springer 2026). O que parece ser
  // e' reprodutivel no MESMO atleta com o MESMO protocolo. Por isso o que
  // interessa nao e' o valor de uma sessao, e' a evolucao entre sessoes.
  const comLim=(d.sessoes||[]).filter(x=>x.limiares && !x.limiares.erro);
  if(comLim.length){
   h+='<h3 style="font-size:14px;margin:14px 0 4px 0;">Limiares por SmO2 '
    +'ao longo do tempo</h3>'
    +'<table style="width:100%;border-collapse:collapse;font-size:11px;">'
    +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
    +'<th style="padding:5px;">Data</th><th>Perfil</th>'
    +'<th>BP1 (LT1/FatMax)</th><th>BP2 (VT2/MLSS)</th>'
    +'<th>SmO2 máx→mín</th><th>Degraus</th></tr>';
   comLim.forEach(function(x, si){
    const L=x.limiares;
    const s2=MX_SESSOES.find(y=>String(y.id)===String(x.activity_id))||{};
    const par=L.perfil==='parabólico';
    h+='<tr style="border-bottom:1px solid #161b22;">'
     +'<td style="padding:5px;color:'+mxCorSessao(si)+';">'+(s2.data||'—')+'</td>'
     +'<td style="color:'+(par?'#A371F7':'#79C0FF')+';">'+(L.perfil||'—')+'</td>'
     +'<td>'+(L.bp1_w!=null
        ? '<b>'+Math.round(L.bp1_w)+' W</b>'
          +(L.bp1_bpm?' <span style="color:#8b949e;">'+L.bp1_bpm+' bpm</span>':'')
        : '<span style="color:#6e7681;">não observável</span>')+'</td>'
     +'<td>'+(L.bp2_w!=null
        ? '<b>'+Math.round(L.bp2_w)+' W</b>'
          +(L.bp2_bpm?' <span style="color:#8b949e;">'+L.bp2_bpm+' bpm</span>':'')
          +'<br><span style="color:#6e7681;font-size:10px;">'+(L.bp2_origem||'')
          +'</span>'
        : '<span style="color:#6e7681;">—</span>')+'</td>'
     +'<td style="color:#8b949e;">'+(L.smo2max!=null
        ? L.smo2max+'% → '+L.smo2min+'%' : '—')+'</td>'
     +'<td style="color:#8b949e;">'+(L.n_degraus||'—')+'</td></tr>';
   });
   h+='</table>';

   // variacao entre sessoes: e' isto que a comparacao serve para ver
   ['bp1_w','bp2_w'].forEach(function(k){
    const vs=comLim.map(x=>x.limiares[k]).filter(v=>v!=null);
    if(vs.length<2) return;
    const lo=Math.min.apply(null,vs), hi=Math.max.apply(null,vs);
    const med=vs.reduce((a,b)=>a+b,0)/vs.length;
    const amp=hi-lo, pct=med?Math.round(amp/med*100):0;
    h+='<p style="font-size:11px;color:'+(pct>15?'#F0883E':'#3FB950')+';">'
     +(k==='bp1_w'?'BP1':'BP2')+': '+Math.round(lo)+'–'+Math.round(hi)
     +' W em '+vs.length+' sessões · variação '+Math.round(amp)+' W ('+pct+'%)'
     +(pct>15 ? ' — variação grande demais para ser mudança de forma; '
        +'verifica se o protocolo foi o mesmo'
        : ' — estável entre sessões')+'</p>';
   });

   // leitura da forma da curva, do perfil mais frequente
   const cont={};
   comLim.forEach(x=>{ const p2=x.limiares.perfil;
    if(p2) cont[p2]=(cont[p2]||0)+1; });
   const dom=Object.keys(cont).sort((a,b)=>cont[b]-cont[a])[0];
   const lei=(comLim.find(x=>x.limiares.perfil===dom)||{}).limiares;
   if(dom && lei && lei.leitura && lei.leitura.o_que_mostra){
    const L2=lei.leitura;
    h+='<div style="border-left:3px solid '
     +(dom==='parabólico'?'#A371F7':'#79C0FF')+';padding:6px 10px;'
     +'margin-top:8px;font-size:11px;">'
     +'<b>Forma da curva: '+dom+'</b> <span style="color:#8b949e;">('
     +cont[dom]+' de '+comLim.length+' sessões)</span>'
     +'<br><b>O que mostra:</b> '+L2.o_que_mostra
     +'<br><b>O que permite concluir:</b> '+L2.o_que_permite
     +'<br><b>O que NÃO permite:</b> <span style="color:#F0883E;">'
     +L2.o_que_nao_permite+'</span>'
     +'<br><b>Prescrição:</b> '+L2.prescricao+'</div>';
   }
   h+='<p style="color:#8b949e;font-size:11px;margin-top:6px;">'
    +'Quatro estudos independentes — Osmani 2023, Possamai 2024 (remo), '
    +'Arnold, e Springer 2026 (triatletas) — concluem que o breakpoint de '
    +'SmO2 <b>não substitui</b> os limiares ventilatórios: a associação '
    +'existe em grupo e quebra no indivíduo, com variabilidade de ±100 W. '
    +'O que parece ser fiável é a <b>repetição no mesmo atleta com o mesmo '
    +'protocolo</b> — por isso esta tabela compara sessões em vez de dar um '
    +'número absoluto.</p>';
  }

  // ── sugestões de treino a partir do consenso entre sessões ────────
  const itv=d.intervencao||{};
  if(itv.ok){
   const iv=itv.intervencao||{};
   const cor = itv.estavel ? '#3FB950' : '#F0883E';
   h+='<div style="border:1px solid '+cor+';border-radius:6px;'
    +'padding:8px 10px;margin:12px 0;">'
    +'<span style="color:#8b949e;font-size:11px;">Limitador ao longo das '
    +'sessões</span><br>'
    +'<b style="font-size:16px;color:'+cor+';">'+iv.nome+'</b> '
    +'<span style="color:#8b949e;font-size:12px;">'+itv.n+' de '+itv.de
    +' sessões ('+itv.concordancia_pct+'%)'
    +(itv.unanime?' · unânime':'')+'</span>'
    +'<br><span style="font-size:11px;color:'+cor+';"><b>'
    +itv.o_que_fazer+'</b></span>'
    +'<br><span style="font-size:10px;color:#8b949e;">'
    +Object.keys(itv.contagem||{}).map(function(k){
      return k+': '+itv.contagem[k]; }).join(' · ')+'</span>';
   if(itv.partilhado)
    h+='<p style="color:#F0883E;font-size:11px;margin:6px 0 0 0;">⚠ '
     +itv.partilhado+'</p>';

   // limitador de cada sessão
   h+='<table style="border-collapse:collapse;font-size:11px;margin-top:8px;">'
    +'<tr style="color:#8b949e;text-align:left;">'
    +'<th style="padding-right:14px;">Sessão</th>'
    +'<th style="padding-right:14px;">Limitador</th>'
    +'<th>Concordância interna</th></tr>'
    +(itv.sessoes||[]).map(function(x, si){
      return '<tr><td style="padding-right:14px;color:'+mxCorSessao(si)+';">'
       +(x.data||x.id||'—')+'</td>'
       +'<td style="padding-right:14px;">'+(x.limitador||'—')+'</td>'
       +'<td style="color:#8b949e;">'+(x.concordancia||'—')
       +(x.confianca?' · '+x.confianca:'')+'</td></tr>'; }).join('')
    +'</table>';

   // plano por zona, com os números da sessão mais representativa do
   // grupo (a mesma "lei" já usada acima para a forma da curva)
   const lc2=(lei||{}).limiares_consenso||{};
   const p1z=lc2.primeiro||{}, p2z=lc2.segundo||{};
   h+='<div id="mxPlanoZonasComp" style="margin-top:8px;"></div>';
   if(itv.limitador_mais_comum){
    setTimeout(function(){
     mxPlanoPorZona(itv.limitador_mais_comum, {
      bp1_w: p1z.mediana, bp2_w: p2z.mediana,
      bp1_bpm: (p1z.estimativas||[]).find(e=>e.bpm) ?
        (p1z.estimativas||[]).find(e=>e.bpm).bpm : null,
      bp2_bpm: (p2z.estimativas||[]).find(e=>e.bpm) ?
        (p2z.estimativas||[]).find(e=>e.bpm).bpm : null,
     }, 'mxPlanoZonasComp');
    }, 0);
   }
   h+='<p style="font-size:10px;color:#8b949e;">'+(itv.nota||'')+'</p>';
   h+='</div>';
  }

  // cartoes por sessao
  h+='<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;">';
  (d.sessoes||[]).forEach(function(x, si){
   const s2=MX_SESSOES.find(y=>String(y.id)===String(x.activity_id))||{};
   h+='<div style="flex:1;min-width:300px;border:1px solid #30363d;'
    +'border-radius:6px;padding:8px 10px;">'
    +'<b style="color:'+mxCorSessao(si)+';">'+(si+1)+'· '+(s2.data||x.activity_id)
    +'</b> <span style="color:#8b949e;font-size:11px;">'
    +(s2.modalidade||'')+(x.pct_artefacto!=null
      ? ' · artefacto '+x.pct_artefacto+'%' : '')+'</span>';
   if(x.erro){ h+='<br><span style="color:#F85149;font-size:11px;">'+x.erro
     +'</span></div>'; return; }
   const L=(x.rede||{}).limitador;
   if(L && L.sistema){
    const cp=L.controlo_pct||{};
    h+='<div style="margin-top:6px;font-size:11px;">'
     +'<b style="color:#F85149;">LIMITADOR: '+L.sistema.toUpperCase()+'</b><br>'
     +'<span style="color:#8b949e;">'+L.leitura+'</span><br>'
     +'<span style="color:#8b949e;">'
     +Object.keys(cp).sort(function(a,b){return cp[b]-cp[a];})
       .map(function(k){ return k+' '+cp[k]+'%'; }).join(' · ')+'</span></div>';
   } else if(x.rede){
    h+='<div style="margin-top:6px;font-size:11px;color:#8b949e;">rede: '
     +(x.rede.motivo||x.rede.erro||'sem arestas')+'</div>';
   }
   const I=(x.i515||{}).interpretacao, P=(x.i515||{}).pontuacao;
   if(I && P){
    [['us','Utilização vs Fornecimento'],['pc','Pulmonar vs Cardíaco']]
     .forEach(function(par){
      const b=I[par[0]], pp=P[par[0]];
      if(!b) return;
      h+='<div style="margin-top:6px;font-size:11px;">'
       +'<span style="color:#8b949e;">'+par[1]+'</span><br>'
       +'<b>'+b.limitador+'</b> <span style="color:#8b949e;">'+pp.pontos
       +'/'+pp.max+' = '+pp.score+'</span><br>'
       +'<span style="color:#8b949e;">'+b.texto+'</span></div>';
     });
   } else if(x.i515 && x.i515.motivo){
    h+='<div style="margin-top:6px;font-size:11px;color:#8b949e;">5-1-5: '
     +x.i515.motivo+'</div>';
   }
   h+='</div>';
  });
  h+='</div>';
  box.innerHTML=h;
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

let MX_BP = null;        // breakpoints para desenhar no gráfico
// Dados de escala do ultimo desenho de cada grafico pequeno (Limiares,
// DFA-1, Dmax, Zonas), para o hover encontrar o ponto mais proximo do
// rato sem ter de recalcular a regressao outra vez.
let MX_HOVER = {};
// Ultimo resultado de cada calculo, para redesenhar sem novo pedido
// quando se troca de sub-tab -- os canvas de uma sub-tab escondida
// (display:none) ficam com largura zero no momento em que sao
// desenhados, e o desenho fica em branco ate' se forcar outra vez.
let MX_ULT_LIMIARES_D = null;
let MX_ULT_PLANO = null, MX_ULT_RPE_D = null, MX_ULT_ZONAS_D = null;
let MX_ULT_ORIGEM_BP = {bp1:'métodos existentes', bp2:'métodos existentes'};
let MX_RESERVAS = null;  // W′ e M′ balance ao longo da sessão
// resultados dos três métodos, para a síntese os poder cruzar
let MX_ULT_REDE=null, MX_ULT_US=null, MX_ULT_PC=null,
    MX_ULT_PERFIL=null, MX_ULT_HIPO=false;
// valores concretos do teste actual (watts, bpm, smo2, limitador
// fisiológico), para as intervenções mostrarem "a que carga" e não só
// receitas genéricas
let MX_ULT_VALORES=null;

function mxGravarTodas(){
 const est=document.getElementById('mxLimEstado');
 est.textContent='a gravar todas as sessões (pode demorar)...';
 fetch('/api/moxy/gravar_todas', {method:'POST'}).then(r=>r.json())
 .then(function(d){
  if(d.status!=='ok'){ est.textContent=d.mensagem||'erro'; return; }
  est.textContent=d.n_gravadas+' gravada(s) · '+d.n_saltadas
   +' já na versão '+d.versao_actual
   +(d.n_erros?' · '+d.n_erros+' com erro':'');
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

function mxGuardarAnalise(){
 const ids=Object.keys(MX_DADOS);
 const est=document.getElementById('mxLimEstado');
 if(!ids.length){ est.textContent='escolhe uma sessão'; return; }
 est.textContent='a gravar '+ids.length+' análise(s)...';
 Promise.all(ids.map(function(id){
  return fetch('/api/moxy/analise/'+id, {method:'POST'})
   .then(r=>r.json()).catch(e=>({status:'erro',mensagem:e.message}));
 })).then(function(res){
  const ok=res.filter(r=>r.status && r.status.indexOf('ok')>=0
                       || r.status==='gravado_sem_upload').length;
  const maus=res.filter(r=>r.status==='erro');
  est.textContent = ok+' gravada(s)'
   + (res[0] && res[0].versao ? ' · versão '+res[0].versao : '')
   + (maus.length ? ' · '+maus.length+' com erro: '+maus[0].mensagem : '');
 });
}

// SmO2 x watts, com os limiares de todos os metodos marcados por uma
// linha vertical -- adaptado do "Limiares de SmO2" do dashboard
// Streamlit (susigan/dashboard, tab_fit_analise.py:_grafico_limiares),
// no nosso proprio canvas em vez de Plotly.
function mxDesenharLimiaresSmo2(d){
 const o = ctx('chMxLimiares', 220); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const bp = d.bp_moxy_sem_restricao || d.bp_moxy || {};
 const pontos = bp.pontos || [];
 if(pontos.length < 3){ noData(g,W,H,'Sem pontos suficientes'); return; }

 const PL=52, PR=20, PT=36, PB=40;
 const w=W-PL-PR, h=H-PT-PB;
 const xs=pontos.map(p=>p.watts), ys=pontos.map(p=>p.smo2);
 const xa=Math.min.apply(null,xs), xb=Math.max.apply(null,xs);
 const ya=Math.min.apply(null,ys)*0.97, yb=Math.max.apply(null,ys)*1.03;
 const X=v=>PL+(v-xa)/(xb-xa||1)*w;
 const Y=v=>PT+h-(v-ya)/(yb-ya||1)*h;

 g.clearRect(0,0,W,H);
 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let i=0;i<=4;i++){
  const yv=ya+(yb-ya)*i/4, y=Y(yv);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(yv.toFixed(0), PL-6, y+4);
 }
 g.textAlign='center';
 xs.forEach(function(xv){
  const x=X(xv);
  g.fillText(Math.round(xv), x, PT+h+16);
 });
 g.fillText('Potência (W)', PL+w/2, PT+h+32);
 g.save(); g.translate(14, PT+h/2); g.rotate(-Math.PI/2);
 g.fillText('SmO₂ (%)', 0, 0); g.restore();

 // curva SmO2 x watts
 g.strokeStyle='#3FB950'; g.lineWidth=2;
 g.beginPath();
 pontos.forEach(function(p,i){
  const x=X(p.watts), y=Y(p.smo2);
  i?g.lineTo(x,y):g.moveTo(x,y);
 });
 g.stroke();
 g.fillStyle='#3FB950';
 pontos.forEach(function(p){
  g.beginPath(); g.arc(X(p.watts),Y(p.smo2),4,0,7); g.fill();
 });
 g.lineWidth=1;

 // limiares de cada metodo, uma linha vertical tracejada por metodo
 const dfa1lim = (((d.dfa1||{}).limiares||{}).HRVT1c||{}).watts||{};
 const metodos=[
  {v:(d.bp_moxy||{}).bp1_w, cor:'#5DADE2', nome:'BP1 (nosso)'},
  {v:(d.bp_moxy||{}).bp2_w, cor:'#5DADE2', nome:'BP2 (nosso)'},
  {v:(d.bp_moxy_sem_restricao||{}).bp1_w, cor:'#F0883E', nome:'BP1 (script)'},
  {v:(d.bp_moxy_sem_restricao||{}).bp2_w, cor:'#F0883E', nome:'BP2 (script)'},
  {v:(d.bp_dmax||{}).bp_w, cor:'#E74C3C', nome:'Dmax'},
  {v:((d.mlss_dessaturacao||{}).mlss_estimado), cor:'#A371F7', nome:'MLSS'},
  {v:(dfa1lim.ok?dfa1lim.valor:null), cor:'#CC79A7', nome:'DFA-α1'},
 ].filter(m=>m.v!=null && m.v>=xa && m.v<=xb);

 // agrupar por watts para nao empilhar rotulos identicos (BP1/BP2 do
 // mesmo metodo podem coincidir com outro metodo)
 let ultimoTxtY = PT-4;
 metodos.forEach(function(m,i){
  const x=X(m.v);
  g.strokeStyle=m.cor; g.setLineDash([5,4]); g.lineWidth=1.5;
  g.beginPath(); g.moveTo(x,PT); g.lineTo(x,PT+h); g.stroke();
  g.setLineDash([]); g.lineWidth=1;
  g.fillStyle=m.cor; g.font='10px sans-serif'; g.textAlign='center';
  const y = PT - 4 - (i%3)*11;   // desfasa em altura para nao sobrepor
  g.fillText(m.nome+' '+Math.round(m.v)+'W', x, y);
 });

 if(!metodos.length) return;
 // legenda pequena, canto superior direito
 g.fillStyle='#8b949e'; g.font='10px sans-serif'; g.textAlign='left';
 g.fillText('cada linha = um método diferente de achar o limiar', PL+4, PT+12);
 MX_HOVER.chMxLimiares = {pontos:pontos, xa:xa, xb:xb, PL:PL, w:w,
   campoX:'watts', campoY:'smo2', unidY:'%'};
}

// DFA-a1: HRVT1c (individualizado, Rogers 2024) ao lado dos classicos
// HRVT1s/HRVT2 para comparacao -- ver hrv_limiares.py.
function mxMostrarDfa1(dfa1){
 const box=document.getElementById('mxDfa1');
 if(!box) return;
 if(!dfa1 || !dfa1.ok){
  box.innerHTML='<p class="sub" style="font-size:12px;">'+
   ((dfa1&&dfa1.motivo)||'sem stream de DFA-α1 nesta sessão')+'</p>';
  return;
 }
 if(!dfa1.sessao_adequada){
  box.innerHTML='<p class="sub" style="font-size:12px;color:#F0883E;">'+
   (dfa1.nota_qualidade||'sessão não adequada para DFA-α1')+'</p>';
  return;
 }
 const lim=dfa1.limiares||{};
 const nomes={HRVT1c:'HRVT1c (individualizado)', HRVT1s:'HRVT1s (α1=0.75, clássico)',
             HRVT2:'HRVT2 (α1=0.50)'};
 let h='<div class="cards">';
 ['HRVT1c','HRVT1s','HRVT2'].forEach(function(k){
  const l=lim[k]; if(!l) return;
  const w=l.watts||{}, fc=l.heartrate||{};
  h+='<div class="card"><div class="label">'+nomes[k]+'</div>'+
   '<div class="value">'+(w.ok?w.valor+' W':'—')+'</div>'+
   '<div style="font-size:12px;color:#8b949e">'+
   (fc.ok?fc.valor+' bpm':'sem FC')+' · α1 alvo='+l.a1_alvo+'</div></div>';
 });
 h+='</div><p class="sub" style="font-size:11px;margin-top:6px;">'+dfa1.nota+'</p>';
 box.innerHTML=h;
}

// Cartoes simples na Principal: Limiar (segundo) - VO2max - Limitador.
// So' os tres numeros, sem explicacoes -- essas ficam nas sub-tabs.
function mxMostrarCartoesSimples(d){
 const box=document.getElementById('mxCartoesSimples');
 if(!box) return;
 const lc=d.limiares_consenso||{};
 const p1=lc.primeiro||{}, p2=lc.segundo||{};
 const vo2=d.vo2max_previsto||{};
 // Rede causal primeiro -- e' a mesma fonte que ja se usa ao gravar a
 // analise. O classificador simples (padrao SmO2/FC por degrau) so'
 // entra se a rede nao tiver dado resultado, para nao mostrar "vazio".
 const rl=d.rede_limitador||{};
 const lf=d.limitador_fisiologico||{};
 const limTxt = rl.rotulo || rl.sistema || (lf.candidatos||[])[0] || '\u2014';

 const bp1Txt = p1.mediana!=null ? Math.round(p1.mediana)+' W' : '\u2014';
 const bp2Txt = p2.mediana!=null ? Math.round(p2.mediana)+' W' : '\u2014';
 // vo2.ok sozinho nao chega -- a formula pode dar um numero
 // fisiologicamente impossivel (o proprio nirs_breakpoints.py documenta
 // um caso real de 2.5 ml/kg/min) quando a FC de "repouso" usada nao
 // era repouso a serio. Sem checar 'plausivel', o cartao mostrava esse
 // numero como se fosse uma medicao normal.
 const vo2Txt = (vo2.ok && vo2.plausivel) ? vo2.vo2max_estimado+' ml/min/kg'
   : (vo2.ok ? '\u2014 (implaus\u00edvel)' : '\u2014');

 box.innerHTML = '<div class="cards">'
  + '<div class="card"><div class="label">BP1</div>'
  + '<div class="value">'+bp1Txt+'</div></div>'
  + '<div class="card"><div class="label">BP2</div>'
  + '<div class="value">'+bp2Txt+'</div></div>'
  + '<div class="card"><div class="label">VO\u2082max calculado</div>'
  + '<div class="value">'+vo2Txt+'</div></div>'
  + '<div class="card"><div class="label">Limitador</div>'
  + '<div class="value" style="font-size:16px;">'+limTxt+'</div></div>'
  + '<div class="card"><div class="label">Zona · RPE</div>'
  + '<div class="value" id="mxCartaoZonaRpe" style="font-size:14px;">\u2014</div></div>'
  + '</div>';
}

// DFA-a1 x intensidade, com os tres limiares marcados -- adaptado do
// grafico do dashboard Streamlit (tab_fit_analise.py:_grafico_dfa1),
// mas com os NOSSOS alvos (HRVT1c individualizado, nao os 3 fixos).
function mxDesenharDfa1(dfa1){
 const o = ctx('chMxDfa1', 220); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 if(!dfa1 || !dfa1.ok || !dfa1.sessao_adequada){ noData(g,W,H,'Sem DFA-\u03b11 utiliz\u00e1vel'); return; }

 const lim=dfa1.limiares||{};
 const base=lim.HRVT1c || lim.HRVT1s || lim.HRVT2 || {};
 const bins=((base.watts||{}).bins)||[];
 if(bins.length < 3){ noData(g,W,H,'Poucos pontos de DFA-\u03b11'); return; }

 const PL=52, PR=20, PT=36, PB=40;
 const w=W-PL-PR, h=H-PT-PB;
 const xs=bins.map(b=>b.centro), ys=bins.map(b=>b.a1);
 const xa=Math.min.apply(null,xs), xb=Math.max.apply(null,xs);
 const ya=0, yb=Math.max(1.3, Math.max.apply(null,ys)*1.05);
 const X=v=>PL+(v-xa)/(xb-xa||1)*w;
 const Y=v=>PT+h-(v-ya)/(yb-ya||1)*h;

 g.clearRect(0,0,W,H);
 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let i=0;i<=4;i++){
  const yv=ya+(yb-ya)*i/4, y=Y(yv);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(yv.toFixed(2), PL-6, y+4);
 }
 g.textAlign='center';
 xs.forEach(function(xv){ g.fillText(Math.round(xv), X(xv), PT+h+16); });
 g.fillText('Potência (W)', PL+w/2, PT+h+32);
 g.save(); g.translate(14, PT+h/2); g.rotate(-Math.PI/2);
 g.fillText('DFA-\u03b11', 0, 0); g.restore();

 // pontos + recta ligando os bins (a regressao real ja' e' a base do
 // proprio calculo; aqui mostra-se a curva suavizada por bin)
 g.strokeStyle='#CC79A7'; g.lineWidth=2;
 g.beginPath();
 bins.forEach(function(b,i){ const x=X(b.centro), y=Y(b.a1); i?g.lineTo(x,y):g.moveTo(x,y); });
 g.stroke();
 g.fillStyle='#CC79A7';
 bins.forEach(function(b){ g.beginPath(); g.arc(X(b.centro),Y(b.a1),3,0,7); g.fill(); });
 g.lineWidth=1;

 // os nossos tres alvos (HRVT1c individualizado, HRVT1s=0.75, HRVT2=0.50)
 const alvos=[
  {k:'HRVT1c', cor:'#5DADE2', nome:'HRVT1c (individual)'},
  {k:'HRVT1s', cor:'#2ECC71', nome:'HRVT1s (\u03b11=0.75)'},
  {k:'HRVT2', cor:'#E74C3C', nome:'HRVT2 (\u03b11=0.50)'},
 ];
 alvos.forEach(function(al,i){
  const l=lim[al.k]; if(!l) return;
  const yAlvo=l.a1_alvo;
  g.strokeStyle=al.cor; g.setLineDash([5,4]); g.lineWidth=1;
  const yy=Y(yAlvo);
  g.beginPath(); g.moveTo(PL,yy); g.lineTo(PL+w,yy); g.stroke();
  if(l.watts && l.watts.ok && l.watts.valor>=xa && l.watts.valor<=xb){
   const xx=X(l.watts.valor);
   g.beginPath(); g.moveTo(xx,PT); g.lineTo(xx,PT+h); g.stroke();
  }
  g.setLineDash([]); g.lineWidth=1;
  g.fillStyle=al.cor; g.font='10px sans-serif'; g.textAlign='left';
  g.fillText(al.nome+' \u03b1='+yAlvo+(l.watts&&l.watts.ok?' ('+Math.round(l.watts.valor)+'W)':''),
             PL+w+4>W-4?PL+4:PL+w-140, yy-3);
 });
 MX_HOVER.chMxDfa1 = {pontos:bins.map(b=>({watts:b.centro, valor:b.a1})),
   xa:xa, xb:xb, PL:PL, w:w, campoX:'watts', campoY:'valor', unidY:''};
}

// Dmax (Cheng et al. 1992): curva SmO2xwatts + a recta de referencia
// 1o-ultimo ponto + o ponto de maior distancia perpendicular marcado.
function mxDesenharDmax(d){
 const o = ctx('chMxDmax', 220); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const bp = d.bp_moxy_sem_restricao || d.bp_moxy || {};
 const pontos = bp.pontos || [];
 const dm = d.bp_dmax || {};
 if(pontos.length < 3){ noData(g,W,H,'Sem pontos suficientes'); return; }

 const PL=52, PR=20, PT=20, PB=36;
 const w=W-PL-PR, h=H-PT-PB;
 const xs=pontos.map(p=>p.watts), ys=pontos.map(p=>p.smo2);
 const xa=Math.min.apply(null,xs), xb=Math.max.apply(null,xs);
 const ya=Math.min.apply(null,ys)*0.97, yb=Math.max.apply(null,ys)*1.03;
 const X=v=>PL+(v-xa)/(xb-xa||1)*w;
 const Y=v=>PT+h-(v-ya)/(yb-ya||1)*h;

 g.clearRect(0,0,W,H);
 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='10px sans-serif';
 g.textAlign='center';
 xs.forEach(function(xv){ g.fillText(Math.round(xv), X(xv), PT+h+14); });

 // curva
 g.strokeStyle='#3FB950'; g.lineWidth=2;
 g.beginPath();
 pontos.forEach(function(p,i){ const x=X(p.watts),y=Y(p.smo2); i?g.lineTo(x,y):g.moveTo(x,y); });
 g.stroke();
 g.fillStyle='#3FB950';
 pontos.forEach(function(p){ g.beginPath(); g.arc(X(p.watts),Y(p.smo2),3,0,7); g.fill(); });
 g.lineWidth=1;

 if(!dm.ok){
  g.fillStyle='#8b949e'; g.font='11px sans-serif'; g.textAlign='left';
  g.fillText(dm.motivo||'Dmax não calculado', PL+4, PT+14);
  return;
 }

 // recta de referencia, 1o ao ultimo ponto
 const ref=dm.reta_referencia||{};
 if(ref.x1!=null){
  g.strokeStyle='#8b949e'; g.setLineDash([4,3]); g.lineWidth=1;
  g.beginPath(); g.moveTo(X(ref.x1),Y(ref.y1)); g.lineTo(X(ref.x2),Y(ref.y2)); g.stroke();
  g.setLineDash([]);
 }

 // o ponto do Dmax, marcado
 const xdm=X(dm.bp_w), ydm=Y(dm.smo2_no_bp);
 g.strokeStyle='#E74C3C'; g.lineWidth=1.5;
 g.beginPath(); g.moveTo(xdm,PT); g.lineTo(xdm,PT+h); g.stroke();
 g.fillStyle='#E74C3C';
 g.beginPath(); g.arc(xdm,ydm,6,0,7); g.fill();
 g.lineWidth=1;
 g.font='11px sans-serif'; g.textAlign='center';
 g.fillText('Dmax '+Math.round(dm.bp_w)+'W (dist. '+dm.distancia_perpendicular+')', xdm, PT-4);
}

// Plano de zonas desta sessao (numeros reais, nao percentagens) --
// busca o plano por limitador e o RPE gravado, e desenha as tres zonas
// por cor (verde/amarelo/vermelho), com o RPE observado em cada uma.
function mxCarregarPlanoZonas(d, id, valores){
 const rl=d.rede_limitador||{};
 // A rede causal fala em periferico/cardiaco/respiratorio/autonomico
 // (SISTEMAS, rede_causal.py); o PLANO_ZONAS fala em
 // entrega/utilizacao/respiratorio (intervencoes.py). Sem esta ponte,
 // "cardiaco" nunca batia com nenhuma chave do plano -- era por isso
 // que nada aparecia: o pedido saia, o backend respondia "sem plano
 // para 'cardiaco'", e a mensagem ficava escondida.
 const MAPA_SISTEMA_PLANO = {
  cardiaco: 'entrega', periferico: 'utilizacao', respiratorio: 'respiratorio',
 };
 const sistema=MAPA_SISTEMA_PLANO[rl.sistema] || null;
 const box=document.getElementById('mxPlanoZonas');
 if(!sistema || valores.bp1_w==null){
  if(box) box.innerHTML='<p class="sub" style="font-size:12px;">'+
   'sem limitador ou limiares suficientes para propor zonas nesta sessão</p>';
  mxDesenharZonas(null,null,null);
  return;
 }

 // se houver verificacao VST consistente/parcial para este BP, usar o
 // valor observado no Dia 2 (esforco sustentado) como referencia
 // OPERACIONAL para as zonas -- NAO recalcula zonas, so' troca qual
 // watts entra no MESMO endpoint /api/moxy/intervencoes; se nao houver
 // verificacao valida, usa exactamente o valor original (comportamento
 // actual, sem alteracao)
 function _prosseguirComOperacionais(v1w, v1_origem, v2w, v2_origem){
  MX_ULT_ORIGEM_BP = {bp1: v1_origem, bp2: v2_origem};
  const vOp = Object.assign({}, valores, {bp1_w: v1w, bp2_w: v2w});
  const q=['plano_limitador='+encodeURIComponent(sistema),
          'bp1_w='+vOp.bp1_w, 'bp2_w='+vOp.bp2_w];
  if(vOp.bp1_bpm!=null) q.push('bp1_bpm='+vOp.bp1_bpm);
  if(vOp.bp2_bpm!=null) q.push('bp2_bpm='+vOp.bp2_bpm);
  fetch('/api/moxy/intervencoes?'+q.join('&'))
  .then(r=>r.json()).then(function(plano){
   if(plano.status!=='ok'){
    if(box) box.innerHTML='<p class="sub" style="font-size:12px;">'+
     (plano.motivo||'sem plano para este limitador')+'</p>';
    mxDesenharZonas(null,null,null);
    return;
   }
   fetch('/api/moxy/rpe/'+id).then(r=>r.json()).then(function(rpeD){
    MX_ULT_PLANO = plano; MX_ULT_RPE_D = rpeD; MX_ULT_ZONAS_D = d;
    mxDesenharZonas(plano, rpeD, d);
    mxMostrarPlanoZonasTexto(plano);
   }).catch(function(){ mxDesenharZonas(plano,null,d); mxMostrarPlanoZonasTexto(plano); });
  }).catch(function(){});
 }

 const idsMoxy=Object.keys(MX_DADOS||{});
 const moxyId=idsMoxy[0];
 function usaOperacional(status, verificadoW, origW){
  // so' passa a usar o verificado se CONSISTENTE ou PARCIALMENTE
  // CONSISTENTE (item 8/caso 3/caso 5) -- DIVERGENTE ou DADOS
  // INSUFICIENTES mantem o original, sem excepcao
  if((status==='CONSISTENTE' || status==='PARCIALMENTE CONSISTENTE') && verificadoW!=null)
   return [verificadoW, status==='CONSISTENTE'?'VST verificado':'VST parcial'];
  return [origW, 'métodos existentes'];
 }

 if(moxyId && MX_VST_VERIF_CACHE[moxyId] !== undefined){
  const dvst=MX_VST_VERIF_CACHE[moxyId];
  const ok = dvst && dvst.status==='ok' && dvst.sincronizado && dvst.analisado;
  const [b1w,b1o]=usaOperacional(ok&&dvst.bp1&&dvst.bp1.status, ok&&dvst.bp1?dvst.bp1.dia2_w:null, valores.bp1_w);
  const [b2w,b2o]=usaOperacional(ok&&dvst.bp2&&dvst.bp2.status, ok&&dvst.bp2?dvst.bp2.dia2_w:null, valores.bp2_w);
  _prosseguirComOperacionais(b1w,b1o,b2w,b2o);
 } else if(moxyId){
  fetch('/api/moxy/vst/verificacao_ativa/'+moxyId).then(r=>r.json()).then(function(dvst){
   MX_VST_VERIF_CACHE[moxyId]=dvst;
   const ok = dvst && dvst.status==='ok' && dvst.sincronizado && dvst.analisado;
   const [b1w,b1o]=usaOperacional(ok&&dvst.bp1&&dvst.bp1.status, ok&&dvst.bp1?dvst.bp1.dia2_w:null, valores.bp1_w);
   const [b2w,b2o]=usaOperacional(ok&&dvst.bp2&&dvst.bp2.status, ok&&dvst.bp2?dvst.bp2.dia2_w:null, valores.bp2_w);
   _prosseguirComOperacionais(b1w,b1o,b2w,b2o);
  }).catch(function(){ _prosseguirComOperacionais(valores.bp1_w,'métodos existentes',valores.bp2_w,'métodos existentes'); });
 } else {
  _prosseguirComOperacionais(valores.bp1_w,'métodos existentes',valores.bp2_w,'métodos existentes');
 }
}

function mxMostrarPlanoZonasTexto(plano){
 const box=document.getElementById('mxPlanoZonas');
 if(!box) return;
 const zonas=plano.zonas||{};
 const cores={zona1:'#2ECC71', zona2:'#F4D03F', zona3:'#E74C3C'};
 const nomes={zona1:'Zona 1 — baixa', zona2:'Zona 2 — moderada', zona3:'Zona 3 — alta'};
 const orig=MX_ULT_ORIGEM_BP||{};
 const origemTxt={
  zona1: 'BP1: '+(orig.bp1||'métodos existentes'),
  zona2: 'BP1/BP2: '+(orig.bp1||'—')+' / '+(orig.bp2||'—'),
  zona3: 'BP2: '+(orig.bp2||'métodos existentes'),
 };
 let h='';
 ['zona1','zona2','zona3'].forEach(function(k){
  const z=zonas[k]; if(!z) return;
  const w=z.watts||[null,null];
  h+='<div style="border-left:3px solid '+cores[k]+';padding:6px 10px;margin-bottom:6px;">'+
   '<b style="color:'+cores[k]+'">'+nomes[k]+'</b> '+
   '<span style="color:#8b949e">'+(w[0]||'—')+'–'+(w[1]||'—')+' W</span>'+
   (z.tem_numeros?'':' <span style="color:#8b949e;font-size:11px">(sem números suficientes)</span>')+
   '<div style="font-size:9px;color:#8b949e;margin-top:1px">'+origemTxt[k]+'</div>'+
   '<div style="font-size:11px;color:#8b949e;margin-top:2px">'+
   (z.protocolos||[]).map(p=>p.nome).join(' · ')+'</div></div>';
 });
 box.innerHTML=h;
}

// RPE por zona: media dos blocos da sessao cujo watts caia dentro da
// zona -- mesma logica ja usada no Perfil Metabolico (pmRpeDaZona).
function _rpeDaZona(blocosRpe, lo, hi){
 const rs=(blocosRpe||[]).filter(function(b){
  return b.rpe!=null && b.watts_medio!=null &&
   (lo==null||b.watts_medio>=lo) && (hi==null||b.watts_medio<hi);
 }).map(b=>b.rpe);
 if(!rs.length) return null;
 const mn=Math.min.apply(null,rs), mx=Math.max.apply(null,rs);
 return mn===mx ? String(mn) : (mn+' a '+mx);
}

function mxDesenharZonas(plano, rpeD, d){
 const o = ctx('chMxZonas', 220); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 g.clearRect(0,0,W,H);
 if(!plano || !plano.zonas){ noData(g,W,H,'Sem plano de zonas'); return; }

 const zonas=plano.zonas;
 const cores={zona1:'#2ECC71', zona2:'#F4D03F', zona3:'#E74C3C'};
 const nomes={zona1:'ZONA 1', zona2:'ZONA 2', zona3:'ZONA 3'};
 const blocosRpe=(rpeD&&rpeD.blocos)||[];

 // pontos de SmO2 (mesma fonte do grafico de Limiares) -- so' para
 // decidir a gama de watts tambem por eles, se forem mais larga que as
 // zonas (uma zona 3 sem tecto nunca da' um limite superior sozinha)
 const bp=(d&&(d.bp_moxy_sem_restricao||d.bp_moxy))||{};
 const pontos=bp.pontos||[];

 const limites=[];
 ['zona1','zona2','zona3'].forEach(function(k){
  const w=(zonas[k]||{}).watts||[]; if(w[0]!=null) limites.push(w[0]); if(w[1]!=null) limites.push(w[1]);
 });
 pontos.forEach(function(p){ limites.push(p.watts); });
 if(!limites.length){ noData(g,W,H,'Sem números de watts nas zonas'); return; }
 const xa=Math.max(0, Math.min.apply(null,limites)*0.9), xb=Math.max.apply(null,limites)*1.1;
 const PL=8, PR=36, PT=28, PB=24;
 const w=W-PL-PR, h=H-PT-PB;
 const X=v=>PL+(v-xa)/(xb-xa||1)*w;

 ['zona1','zona2','zona3'].forEach(function(k){
  const z=zonas[k]; if(!z) return;
  const wr=z.watts||[null,null];
  const lo = wr[0]!=null?wr[0]:xa, hi = wr[1]!=null?wr[1]:xb;
  const x0=X(lo), x1=X(hi);
  g.fillStyle=cores[k]; g.globalAlpha=0.18;
  g.fillRect(x0,PT,x1-x0,h);
  g.globalAlpha=1;
  g.strokeStyle=cores[k]; g.lineWidth=1;
  g.strokeRect(x0,PT,x1-x0,h);

  const rpeZ=_rpeDaZona(blocosRpe, wr[0], wr[1]);
  const rot=nomes[k]+(rpeZ?' (RPE '+rpeZ+')':'');
  g.fillStyle=cores[k]; g.font='bold 11px sans-serif'; g.textAlign='center';
  if(x1-x0>60) g.fillText(rot, (x0+x1)/2, PT-8);
  g.font='10px sans-serif'; g.fillStyle='#8b949e';
  if(x1-x0>50) g.fillText(Math.round(lo)+'–'+Math.round(hi)+'W', (x0+x1)/2, PT+h+14);
 });

 // curva de SmO2 sobreposta ao fundo de zonas, com eixo proprio a
 // direita -- a mesma fonte que ja alimenta o grafico de Limiares.
 if(pontos.length>=3){
  const ys=pontos.map(p=>p.smo2);
  const ya=Math.min.apply(null,ys)*0.97, yb=Math.max.apply(null,ys)*1.03;
  const Y=v=>PT+h-(v-ya)/(yb-ya||1)*h;
  g.strokeStyle='#c9d1d9'; g.lineWidth=2; g.globalAlpha=0.9;
  g.beginPath();
  pontos.forEach(function(p,i){ const x=X(p.watts),y=Y(p.smo2); i?g.lineTo(x,y):g.moveTo(x,y); });
  g.stroke();
  g.globalAlpha=1; g.fillStyle='#c9d1d9';
  pontos.forEach(function(p){ g.beginPath(); g.arc(X(p.watts),Y(p.smo2),3,0,7); g.fill(); });
  g.lineWidth=1;
  g.textAlign='left'; g.font='9px sans-serif'; g.fillStyle='#c9d1d9';
  g.fillText('SmO₂', PL+w+4, PT+10);
 }

 // BP1/BP2, verticais, por cima de tudo -- a mesma fonte de sempre
 const lc=(d&&d.limiares_consenso)||{};
 [['BP1', (lc.primeiro||{}).mediana, '#5DADE2'],
  ['BP2', (lc.segundo||{}).mediana, '#F0883E']].forEach(function(bp2){
  const nome=bp2[0], val=bp2[1], cor=bp2[2];
  if(val==null || val<xa || val>xb) return;
  const x=X(val);
  g.strokeStyle=cor; g.setLineDash([4,3]); g.lineWidth=1.5;
  g.beginPath(); g.moveTo(x,PT); g.lineTo(x,PT+h); g.stroke();
  g.setLineDash([]); g.lineWidth=1;
  g.fillStyle=cor; g.font='bold 10px sans-serif'; g.textAlign='center';
  g.fillText(nome, x, PT+h+22);
 });

 MX_HOVER.chMxZonas = {xa:xa, xb:xb, PL:PL, w:w,
  zonas:['zona1','zona2','zona3'].filter(k=>zonas[k]).map(function(k){
   const wr=zonas[k].watts||[null,null];
   return {lo:wr[0], hi:wr[1], nome:nomes[k],
     rpe:_rpeDaZona(blocosRpe, wr[0], wr[1])};
  })};

 // resumo compacto no cartao simples da Principal -- so' as zonas com
 // RPE observado, para nao poluir com "sem RPE" repetido tres vezes
 const cartao=document.getElementById('mxCartaoZonaRpe');
 if(cartao){
  const partes=MX_HOVER.chMxZonas.zonas
   .filter(function(z){ return z.rpe; })
   .map(function(z){ return 'Z'+z.nome.slice(-1)+' '+z.rpe; });
  cartao.textContent = partes.length ? partes.join(' · ') : '\u2014';
 }
}

// Hover generico para os graficos de pontos (Limiares, DFA-1) --
// encontra o ponto mais proximo do rato em X e mostra um tooltip.
function mxLigarHoverPontos(canvasId, tipId){
 const cv=document.getElementById(canvasId);
 const tip=document.getElementById(tipId);
 if(!cv || !tip) return;
 cv.addEventListener('mousemove', function(ev){
  const info=MX_HOVER[canvasId];
  if(!info || !info.pontos || !info.pontos.length){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const mx=(ev.clientX-r.left)*((cv.width/r.width)/(window.devicePixelRatio||1));
  const watts=info.xa+(mx-info.PL)/(info.w||1)*(info.xb-info.xa);
  let melhor=info.pontos[0], melhorD=Infinity;
  info.pontos.forEach(function(p){
   const dd=Math.abs(p[info.campoX]-watts);
   if(dd<melhorD){ melhorD=dd; melhor=p; }
  });
  tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+12, r.width-140)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-30)+'px';
  tip.textContent=Math.round(melhor[info.campoX])+'W · '+
    melhor[info.campoY].toFixed(2)+(info.unidY||'');
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

// Hover para o grafico de zonas: mostra a zona e o RPE sob o rato.
function mxLigarHoverZonas(){
 const cv=document.getElementById('chMxZonas');
 const tip=document.getElementById('mxTipZonas');
 if(!cv || !tip) return;
 cv.addEventListener('mousemove', function(ev){
  const info=MX_HOVER.chMxZonas;
  if(!info || !info.zonas){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const mx=(ev.clientX-r.left)*((cv.width/r.width)/(window.devicePixelRatio||1));
  const watts=info.xa+(mx-info.PL)/(info.w||1)*(info.xb-info.xa);
  const z=info.zonas.find(function(zz){
   return watts>=(zz.lo==null?-Infinity:zz.lo) && watts<(zz.hi==null?Infinity:zz.hi);
  });
  if(!z){ tip.style.display='none'; return; }
  tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+12, r.width-160)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-30)+'px';
  tip.textContent=z.nome+' · '+Math.round(z.lo||0)+'\u2013'+
    (z.hi!=null?Math.round(z.hi):'\u221e')+'W'+(z.rpe?' · RPE '+z.rpe:'');
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

// ═══════════════════════════════════════════════════════════════════
// VERIFICAÇÃO — protocolo VST. Intervalo 0 = aquecimento (nunca conta
// para BP1), 1-4 = BP1, 5-7 = BP2 -- tudo pelos intervalos REAIS da
// sessão, nunca watts/duração fixos.
// ═══════════════════════════════════════════════════════════════════

let MX_VST_ULT = null;
let MX_VST_ULT_COMP = null;  // ultima resposta do /comparar, para o selector de metrica do recovery
let MX_VST_STREAMS = null;  // tempo+canais completos do Dia 2, de /api/moxy/dados/<id> (endpoint ja existente)
let MX_VST_TEMPORAL_VISIVEL = {power:true, heartrate:true, respiration:true, smo2:true};

function mxVstToggleTemporal(chave, visivel){
 MX_VST_TEMPORAL_VISIVEL[chave] = visivel;
 if(MX_VST_ULT) mxDesenharVstTemporal(MX_VST_ULT);
}
let MX_VST_CONJUNTOS_SALVOS = [];  // ultima lista de /api/moxy/vst/conjuntos_salvos, para o botao ABRIR por indice
const DEBUG_VST_VERIFICACAO = false;  // true mostra a revisao completa (so' para desenvolvimento)

function mxVstCarregarConjuntosSalvos(){
 const box=document.getElementById('mxVstConjuntosSalvos');
 if(!box) return;
 box.innerHTML='<p class="sub" style="font-size:12px;">a carregar…</p>';
 fetch('/api/moxy/vst/conjuntos_salvos').then(r=>r.json()).then(function(d){
  if(d.status!=='ok' || !d.conjuntos || !d.conjuntos.length){
   box.innerHTML='<p class="sub" style="font-size:12px;">NENHUM CONJUNTO DE VERIFICAÇÃO ENCONTRADO</p>';
   return;
  }
  MX_VST_CONJUNTOS_SALVOS = d.conjuntos;
  box.innerHTML = '<div class="cards">' + d.conjuntos.map(function(c,ix){
   const data = (c.analisado_em||c.actualizado_em||'').slice(0,10).split('-').reverse().join('/');
   return '<div class="card" style="min-width:220px;">'
    +'<div class="label">Conjunto de verificação</div>'
    +'<div style="font-size:10px;color:#8b949e;margin-top:2px;">MOXY: '+c.moxy_activity_id+'<br>VST: '+c.vst_activity_id
    +(data?'<br>Data: '+data:'')+'</div>'
    +'<div style="font-size:11px;margin-top:4px;">'
    +(c.bp1_status?'BP1 <span style="color:'+_vstCorGeral(c.bp1_status)+';">'+c.bp1_status+'</span><br>':'')
    +(c.bp2_status?'BP2 <span style="color:'+_vstCorGeral(c.bp2_status)+';">'+c.bp2_status+'</span><br>':'')
    +((c.recovery_bp1_status||c.recovery_bp2_status)?'Recovery <span style="color:'+_vstCorGeral(c.recovery_bp1_status||c.recovery_bp2_status)+';">'
      +(c.recovery_bp1_status||c.recovery_bp2_status)+'</span>':'')
    +'</div>'
    +'<button style="margin-top:6px;font-size:11px;" onclick="mxVstAbrirVerificacao('+ix+')">ABRIR VERIFICAÇÃO</button>'
    +'</div>';
  }).join('') + '</div>';
 }).catch(function(e){
  box.innerHTML='<p class="sub" style="font-size:12px;">erro: '+e.message+'</p>';
 });
}

function mxVstAbrirVerificacao(ix){
 // usa o vinculo ja' salvo -- carrega o VST (que ja' auto-carrega a
 // Moxy vinculada, comportamento existente), sem recalcular nada
 const c = MX_VST_CONJUNTOS_SALVOS[ix];
 if(!c) return;
 const selVst=document.getElementById('mxVstSelect');
 if(selVst){ selVst.value=c.vst_activity_id; }
 mxVstCarregar();
}

function mxVstCarregarLista(){
 const sel=document.getElementById('mxVstSelect');
 const est=document.getElementById('mxVstEstado');
 if(!sel) return;
 fetch('/api/moxy/vst/lista').then(r=>r.json()).then(function(d){
  if(d.status!=='ok' || !d.actividades || !d.actividades.length){
   sel.innerHTML='<option value="">nenhuma sessão VST encontrada</option>';
   if(est) est.textContent=d.mensagem||'nenhuma actividade com a tag VST';
   return;
  }
  sel.innerHTML='<option value="">escolhe uma sessão</option>'+
   d.actividades.map(function(a){
    return '<option value="'+a.id+'">'+a.date+' · '+(a.name||a.type||a.id)+'</option>';
   }).join('');
  if(est) est.textContent=d.n+' sessão(ões) VST encontrada(s)';
 }).catch(function(){
  if(est) est.textContent='erro a carregar a lista de sessões VST';
 });
}

function mxVstCarregar(){
 const sel=document.getElementById('mxVstSelect');
 const est=document.getElementById('mxVstEstado');
 const id=sel&&sel.value;
 if(!id){
  document.getElementById('mxVstCartoes').innerHTML='';
  document.getElementById('mxVstTabela').innerHTML='';
  return;
 }
 if(est) est.textContent='a calcular…';
 fetch('/api/moxy/vst/'+id).then(r=>{
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json();
 }).then(function(d){
  if(d.status!=='ok'){
   if(est) est.textContent=d.mensagem||'sem dados';
   document.getElementById('mxVstCartoes').innerHTML=
    '<p class="sub">'+(d.mensagem||'sem dados suficientes')+'</p>';
   document.getElementById('mxVstTabela').innerHTML='';
   return;
  }
  MX_VST_ULT=d;
  if(est) est.textContent=(d.aviso_estrutura?'⚠ '+d.aviso_estrutura:
    d.n_intervalos_encontrados+' intervalos de trabalho encontrados');
  mxVstCartoes(d);
  mxVstTabela(d);
  mxDesenharVstTemporal(d);
  mxDesenharVstFisiologicoTodos(d);
  mxDesenharVstHeatmap(d);
  mxDesenharVstTiming(d);
  mxVstRpe(id);
  mxVstPopularMoxySelect();
  mxVstCarregarConjunto(id);

  // streams completos (tempo+canais) para HR/RF/SmO2 no grafico
  // temporal -- endpoint ja' existente, nenhum novo criado
  MX_VST_STREAMS = null;
  fetch('/api/moxy/dados/'+id).then(r=>r.json()).then(function(sd){
   if(sd && sd.status==='ok' && sd.tempo && sd.canais){
    MX_VST_STREAMS = sd;
   }
   mxDesenharVstTemporal(d);  // redesenha, agora com os streams se vieram
  }).catch(function(){ mxDesenharVstTemporal(d); });
 }).catch(function(e){
  if(est) est.textContent='erro: '+e.message;
 });
}

function _vstStatusCor(status){
 return {CONFIRMADO:'#3FB950', 'PARCIALMENTE CONFIRMADO':'#F4D03F',
        'NÃO CONFIRMADO':'#E74C3C', 'DADOS INSUFICIENTES':'#8b949e'}[status]
        || '#8b949e';
}

function _vstResumoMetrica(m, unidade){
 if(!m || !m.ok) return '—';
 return m.inicial+(unidade||'')+' → '+m.final+(unidade||'')+
   (m.delta_pct!=null?' ('+(m.delta_pct>=0?'+':'')+m.delta_pct+'%)':'');
}

function mxVstCartoes(d){
 const box=document.getElementById('mxVstCartoes');
 if(!box) return;
 const aq=(d.aquecimento&&d.aquecimento.metricas)||{};
 const bp1=d.bp1||{}, bp2=d.bp2||{};
 const vBp1=bp1.verificacao||{}, vBp2=bp2.verificacao||{};

 function cartaoPotencias(intervalos){
  const ps=(intervalos||[]).map(function(iv){
   const p=iv.potencia||{};
   return p.ok?Math.round(p.media)+'W':'—';
  });
  return ps.join(' · ');
 }

 box.innerHTML = '<div class="cards">'
  // CARD 1 -- Sessao
  + '<div class="card"><div class="label">Sessão</div>'
  + '<div class="value" style="font-size:14px;">'+(d.activity_id||'')+'</div>'
  + '<div style="font-size:11px;color:#8b949e">'
  + (d.n_intervalos_encontrados||0)+' intervalos de trabalho'
  + (d.aviso_estrutura?'<br><span style="color:#F0883E;">'+d.aviso_estrutura+'</span>':'')
  + '</div></div>'
  // CARD 2 -- Aquecimento
  + '<div class="card"><div class="label">Aquecimento</div>'
  + '<div class="value">'+((aq.potencia&&aq.potencia.ok)?Math.round(aq.potencia.media)+'W':'—')+'</div>'
  + '<div style="font-size:11px;color:#8b949e">'
  + 'SmO2 '+_vstResumoMetrica(aq.smo2,'%')+'<br>'
  + 'HR '+_vstResumoMetrica(aq.hr,'')+' · RF '+_vstResumoMetrica(aq.respiracao,'')
  + '</div></div>'
  // CARD 3 -- BP1 (padrao FISIOLOGICO dentro do proprio Dia 2 --
  // verificar_bloco -- NAO e' a reprodutibilidade Dia1xDia2, que fica
  // nos cartoes do topo/mxVstResumoCartoes)
  + '<div class="card"><div class="label">BP1 — padrão fisiológico (Dia 2)</div>'
  + '<div class="value" style="font-size:13px;color:'+_vstStatusCor(vBp1.status)+';">'+(vBp1.status||'')+'</div>'
  + '<div style="font-size:11px;color:#8b949e;margin-top:2px;">'+cartaoPotencias(bp1.metricas)+'</div>'
  + '<div style="font-size:11px;color:#8b949e">'+(vBp1.motivo||'')+'</div></div>'
  // CARD 4 -- BP2
  + '<div class="card"><div class="label">BP2 — padrão fisiológico (Dia 2)</div>'
  + '<div class="value" style="font-size:13px;color:'+_vstStatusCor(vBp2.status)+';">'+(vBp2.status||'')+'</div>'
  + '<div style="font-size:11px;color:#8b949e;margin-top:2px;">'+cartaoPotencias(bp2.metricas)+'</div>'
  + '<div style="font-size:11px;color:#8b949e">'+(vBp2.motivo||'')+'</div></div>'
  + '</div>';
}

function mxVstTabela(d){
 const box=document.getElementById('mxVstTabela');
 if(!box) return;
 const cobertura={};
 (d.cobertura_stream||[]).forEach(function(c){ cobertura[c.nome]=c; });
 const curtos=(d.duracao_curta||[]).map(function(c){ return c.t0; });
 const linhas=[];
 const aq=d.aquecimento&&d.aquecimento.metricas;
 if(aq) linhas.push({bloco:'Aquecimento', iv:aq, chave:'aquecimento'});
 (d.bp1&&d.bp1.metricas||[]).forEach(function(iv,i){
  linhas.push({bloco:'BP1', n:i+1, iv:iv, chave:'bp1#'+(i+1)});
 });
 (d.bp2&&d.bp2.metricas||[]).forEach(function(iv,i){
  linhas.push({bloco:'BP2', n:i+1, iv:iv, chave:'bp2#'+(i+1)});
 });

 function cel(m, unidade){
  if(!m || !m.ok) return '<td class="sub">—</td>';
  const delta=m.delta_pct!=null?' ('+(m.delta_pct>=0?'+':'')+m.delta_pct+'%)':'';
  return '<td>'+m.inicial+(unidade||'')+' → '+m.final+(unidade||'')+delta+'</td>';
 }

 let h='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;">'
  +'<th style="padding:4px 10px 4px 0;">Bloco</th>'
  +'<th style="padding:4px 10px;">Potência (W)</th>'
  +'<th style="padding:4px 10px;">SmO2 (%)</th>'
  +'<th style="padding:4px 10px;">THb</th>'
  +'<th style="padding:4px 10px;">RF</th>'
  +'<th style="padding:4px 10px;">HR</th>'
  +'<th style="padding:4px 10px;">DFA-α1</th></tr>';
 linhas.forEach(function(l){
  const iv=l.iv;
  const p=iv.potencia||{};
  const cob=cobertura[l.chave];
  const ehCurto = curtos.indexOf(iv.t0)>=0;
  const avisoCob = (cob && cob.coberto_pelo_stream===false)
    ? '<br><span style="color:#F0883E;font-size:9px;" title="'+cob.nota+'">'
      +'⚠ fora do alcance do stream</span>'
    : (ehCurto
      ? '<br><span style="color:#F0883E;font-size:9px;" '
        +'title="duração registada muito curta — a potência vem da API, '
        +'mas não há tempo suficiente para calcular a fisiologia">'
        +'⚠ duração curta (só potência)</span>'
      : '');
  h+='<tr style="border-top:1px solid #21262d;">'
   +'<td style="padding:4px 10px 4px 0;"><b>'+l.bloco+(l.n?' #'+l.n:'')+'</b>'+avisoCob+'</td>'
   +'<td style="padding:4px 10px;">'+(p.ok?Math.round(p.media):'—')+'</td>'
   +cel(iv.smo2,'%').replace('<td>','<td style="padding:4px 10px;">')
   +cel(iv.thb,'').replace('<td>','<td style="padding:4px 10px;">')
   +cel(iv.respiracao,'').replace('<td>','<td style="padding:4px 10px;">')
   +cel(iv.hr,'').replace('<td>','<td style="padding:4px 10px;">')
   +cel(iv.dfa1,'').replace('<td>','<td style="padding:4px 10px;">')
   +'</tr>';
 });
 h+='</table>';
 box.innerHTML=h;
}

// Selector da sessao Moxy correspondente -- reaproveita MX_SESSOES,
// ja carregada pela Principal, sem pedir outra vez ao servidor.
function mxVstPopularMoxySelect(){
 const sel=document.getElementById('mxVstMoxySelect');
 if(!sel) return;
 if(!MX_SESSOES.length){
  sel.innerHTML='<option value="">nenhuma sessão Moxy carregada ainda '
   +'-- abre a Principal primeiro</option>';
  return;
 }
 const actual=sel.value;
 sel.innerHTML='<option value="">escolhe a sessão Moxy</option>'+
  MX_SESSOES.map(function(s){
   return '<option value="'+s.id+'">'+(s.data||'')+' · '+(s.nome||s.id)+'</option>';
  }).join('');
 if(actual) sel.value=actual;
}

function mxVstMoxySelecionado(){
 // item 3.4: ao escolher a Moxy, procurar automaticamente o VST ja'
 // vinculado a ela (procura inversa) -- se existir, carrega os dois
 // sem pedir para escolher o VST outra vez.
 const selMoxy=document.getElementById('mxVstMoxySelect');
 const mid=selMoxy && selMoxy.value;
 if(!mid) return;
 fetch('/api/moxy/vst/conjunto_por_moxy/'+mid).then(r=>r.json()).then(function(d){
  if(d.status!=='ok' || !d.sincronizado) return;  // nada vinculado ainda, deixa o utilizador escolher/sincronizar
  const selVst=document.getElementById('mxVstSelect');
  if(selVst && selVst.value!==d.vst_activity_id){
   selVst.value=d.vst_activity_id;
   mxVstCarregar();
  }
 }).catch(function(){});
}

function mxVstCarregarConjunto(vstId){
 const box=document.getElementById('mxVstConjuntoEstado');
 const selMoxy=document.getElementById('mxVstMoxySelect');
 if(!box) return;
 fetch('/api/moxy/vst/conjunto/'+vstId).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ box.innerHTML=''; return; }
  if(!d.sincronizado){
   box.innerHTML='<p class="sub" style="font-size:12px;">ainda não '
    +'sincronizado com nenhuma sessão Moxy</p>';
   return;
  }
  if(selMoxy) selMoxy.value=d.moxy_activity_id;
  box.innerHTML='<div style="border-left:3px solid #3FB950;padding:6px 10px;">'
   +'<b>CONJUNTO DE VERIFICAÇÃO</b><br>'
   +'MOXY: '+d.moxy_activity_id+'<br>'
   +'VST: '+d.vst_activity_id+'<br>'
   +'<span style="color:#3FB950;">STATUS: SINCRONIZADO</span>'
   +'<div style="font-size:11px;color:#8b949e;margin-top:2px">desde '
   +d.criado_em+(d.actualizado_em!==d.criado_em?' · actualizado '+d.actualizado_em:'')
   +'</div></div>';
  mxVstCarregarComparacao(vstId);
 }).catch(function(){ box.innerHTML=''; });
}

function mxVstSincronizar(){
 const vstSel=document.getElementById('mxVstSelect');
 const moxySel=document.getElementById('mxVstMoxySelect');
 const vstId=vstSel&&vstSel.value, moxyId=moxySel&&moxySel.value;
 const box=document.getElementById('mxVstConjuntoEstado');
 if(!vstId){ if(box) box.innerHTML='<p class="sub">escolhe primeiro '
  +'uma sessão VST</p>'; return; }
 if(!moxyId){ if(box) box.innerHTML='<p class="sub">escolhe a sessão '
  +'Moxy correspondente</p>'; return; }
 fetch('/api/moxy/vst/conjunto', {
  method:'POST', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({vst_activity_id:vstId, moxy_activity_id:moxyId})
 }).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){
   if(box) box.innerHTML='<p class="sub">erro: '+(d.mensagem||'desconhecido')+'</p>';
   return;
  }
  mxVstCarregarConjunto(vstId);
  mxVstCarregarComparacao(vstId);
 }).catch(function(e){
  if(box) box.innerHTML='<p class="sub">erro de rede: '+e.message+'</p>';
 });
}

// Comparacao Dia1 x Dia2: Dia1 = a sessao Moxy vinculada, Dia2 = a
// propria sessao VST. Reaproveita tudo o que ja' esta calculado -- nao
// refaz deteccao de blocos nem limiares, so' pede ao endpoint que ja'
// junta os dois.
function mxVstCarregarComparacao(vstId){
 const box=document.getElementById('mxVstComparacao');
 if(!box) return;
 box.innerHTML='<p class="sub" style="font-size:12px;">a comparar…</p>';
 fetch('/api/moxy/vst/comparar/'+vstId).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){
   box.innerHTML='<p class="sub" style="font-size:12px;">'+
    (d.mensagem||'sem dados suficientes para comparar')+'</p>';
   return;
  }
  box.innerHTML=_vstTabelaComparacao('BP1', d.comparacao_bp1, d.comparacao_rpe_bp1)
   + _vstTabelaComparacao('BP2', d.comparacao_bp2, d.comparacao_rpe_bp2);
  mxVstRpeTabela(d);
  MX_VST_ULT_COMP = d;
  if(MX_VST_ULT) mxDesenharVstHeatmap(MX_VST_ULT);
  mxVstResumoCartoes(d);
  mxVstRecoveryCartoes(d);
  mxVstRecoveryTabela(d);
  mxVstRecoveryFinalMostrar(d.recuperacao_final_dia2);
  mxDesenharVstRecoveryTempo(d);
  if(typeof DEBUG_VST_VERIFICACAO!=='undefined' && DEBUG_VST_VERIFICACAO) mxVstRevisaoCritica(d);
  mxVstLimitacoes(d);
 }).catch(function(e){
  box.innerHTML='<p class="sub" style="font-size:12px;">erro: '+e.message+'</p>';
 });
}

function _vstCorConsistencia(c){
 return {'CONSISTENTE':'#3FB950', 'PARCIAL':'#F4D03F', 'DIVERGENTE':'#E74C3C',
        'SEM DADOS':'#8b949e'}[c] || '#8b949e';
}

function _vstCorStatus(s){
 return {'CONSISTENTE':'#3FB950', 'PARCIALMENTE CONSISTENTE':'#F4D03F',
        'DIVERGENTE':'#E74C3C', 'DADOS INSUFICIENTES':'#8b949e'}[s] || '#8b949e';
}

function _vstTabelaComparacao(titulo, comp, compRpe){
 if(!comp) return '';
 const pot=comp.potencia;
 const rob=comp.robustez||{};
 let h='<div style="margin-bottom:16px;">'
  +'<h4 style="font-size:13px;margin:10px 0 4px;">'+titulo
  +' — reprodutibilidade Dia 1 × Dia 2: <span style="color:'
  +_vstCorStatus(comp.status)+'">'+(comp.status||'')+'</span></h4>'
  +'<p class="sub" style="font-size:11px;margin:0 0 6px;">'+(comp.motivo||'')+'</p>';
 if(pot){
  h+='<p style="font-size:11px;color:#8b949e;margin:0 0 6px;">Potência — '
   +'Dia 1: '+pot.dia1_w+'W · Dia 2: '+pot.dia2_w+'W · diferença: '
   +(pot.diferenca_w>=0?'+':'')+pot.diferenca_w+'W ('
   +(pot.diferenca_pct>=0?'+':'')+pot.diferenca_pct+'%)</p>';
 }
 if(rob.aviso_poucos_pontos){
  h+='<p style="font-size:11px;color:#F0883E;margin:0 0 6px;">⚠ '
   +rob.aviso_poucos_pontos+'</p>';
 }
 const metricas=comp.metricas||{};
 h+='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;">'
  +'<th style="padding:3px 10px 3px 0;">Métrica</th>'
  +'<th style="padding:3px 10px;">Dia 1</th>'
  +'<th style="padding:3px 10px;">Dia 2</th>'
  +'<th style="padding:3px 10px;">Direcção</th>'
  +'<th style="padding:3px 10px;">Timing</th>'
  +'<th style="padding:3px 10px;">Consistência</th></tr>';
 Object.keys(metricas).forEach(function(k){
  const m=metricas[k];
  const d1=m.dia1, d2=m.dia2;
  const t1=m.timing_dia1, t2=m.timing_dia2;
  const timingTxt = (t1&&t2)
   ? ((t1.concentrado_no_alvo?'✓':'✗')+'D1 / '+(t2.concentrado_no_fim?'✓':'✗')+'D2')
   : '—';
  const nomeComPeso = m.nome + (m.peso==='complementar'
   ? ' <span class="sub" style="font-size:9px;" title="evidência autonómica complementar — não decide sozinha">(complementar)</span>' : '');
  h+='<tr style="border-top:1px solid #21262d;'
   +(m.peso==='complementar'?'opacity:0.75;':'')+'">'
   +'<td style="padding:3px 10px 3px 0;">'+nomeComPeso+'</td>'
   +'<td style="padding:3px 10px;">'+(d1?d1.inicial+m.unidade+' → '+d1.final+m.unidade:'—')+'</td>'
   +'<td style="padding:3px 10px;">'+(d2?d2.inicial+m.unidade+' → '+d2.final+m.unidade:'—')+'</td>'
   +'<td style="padding:3px 10px;">'+(m.direccao_dia1||'—')+' / '+(m.direccao_dia2||'—')+'</td>'
   +'<td style="padding:3px 10px;font-size:10px;" title="✓D1: mudança concentrada no bloco-alvo, não nos vizinhos · ✓D2: tendência significativa dentro do bloco (mesmo teste de permutação do Dia 2)">'
   +timingTxt+'</td>'
   +'<td style="padding:3px 10px;color:'+_vstCorConsistencia(m.consistencia)+';">'
   +m.consistencia+'</td></tr>';
 });
 // RPE -- mesma tabela, ultima linha, sempre marcada complementar; nao
 // vem de comp.metricas (fisiologia), vem de comparar_rpe() a parte
 if(compRpe){
  const semDados = !compRpe.dia1 || !compRpe.dia2;
  h+='<tr style="border-top:1px solid #21262d;opacity:0.75;">'
   +'<td style="padding:3px 10px 3px 0;">RPE <span class="sub" style="font-size:9px;" title="evidência perceptiva complementar — não decide sozinha">(complementar)</span></td>'
   +'<td style="padding:3px 10px;">'+(compRpe.dia1?compRpe.dia1.inicial+' → '+compRpe.dia1.final:'—')+'</td>'
   +'<td style="padding:3px 10px;">'+(compRpe.dia2?compRpe.dia2.inicial+' → '+compRpe.dia2.final:'—')+'</td>'
   +'<td style="padding:3px 10px;">'+(compRpe.dia1?compRpe.dia1.direccao:'—')+' / '+(compRpe.dia2?compRpe.dia2.direccao:'—')+'</td>'
   +'<td style="padding:3px 10px;font-size:10px;">'+(compRpe.dia2&&compRpe.dia2.maior_salto?'W'+compRpe.dia2.maior_salto.de_work+'→W'+compRpe.dia2.maior_salto.para_work:'—')+'</td>'
   +'<td style="padding:3px 10px;color:'+(semDados?'#8b949e':_vstCorGeral(compRpe.status))+';">'+(compRpe.status||'DADOS INSUFICIENTES')+'</td></tr>';
 }
 h+='</table>';
 if(rob.nota) h+='<p class="sub" style="font-size:10px;margin-top:4px;">'+rob.nota+'</p>';
 h+='</div>';
 return h;
}

// ═══════════════════════════════════════════════════════════════════
// Camada VISUAL de recovery -- consome comparar_recovery() tal como
// vem do backend, nunca recalcula. Nada aqui e' matematica nova.
// ═══════════════════════════════════════════════════════════════════

function _vstCorGeral(s){
 return {'CONSISTENTE':'#3FB950','CONVERGENTE':'#3FB950','RECUPEROU':'#3FB950','ESTÁVEL':'#3FB950',
        'PARCIALMENTE CONSISTENTE':'#F4D03F','PARCIALMENTE CONVERGENTE':'#F4D03F','PARCIAL':'#F4D03F',
        'INCONSISTENTE':'#F4D03F','PROGRESSIVAMENTE PIOR':'#F4D03F','PROGRESSIVAMENTE MELHOR':'#F4D03F',
        'DIVERGENTE':'#E74C3C','NÃO RECUPEROU':'#E74C3C','DADOS INSUFICIENTES':'#8b949e',
        'INDETERMINADA':'#8b949e'}[s] || '#8b949e';
}

function _vstCartaoSimples(titulo, valor, sub){
 return '<div class="card"><div class="label">'+titulo+'</div>'
  +'<div class="value" style="font-size:15px;color:'+_vstCorGeral(valor)+';">'+(valor||'—')+'</div>'
  +(sub?'<div style="font-size:10px;color:#8b949e;margin-top:2px;">'+sub+'</div>':'')
  +'</div>';
}

function _vstCartaoBP(titulo, comp){
 const pot = comp.potencia;
 const linhaPot = pot
  ? 'Dia 1: '+pot.dia1_w+'W · Dia 2: '+pot.dia2_w+'W · Δ: '
    +(pot.diferenca_w>=0?'+':'')+pot.diferenca_w+'W ('
    +(pot.diferenca_pct>=0?'+':'')+pot.diferenca_pct+'%)'
  : '';
 return '<div class="card"><div class="label">'+titulo+' — Reprodutibilidade Dia 1 × Dia 2</div>'
  +'<div class="value" style="font-size:15px;color:'+_vstCorGeral(comp.status)+';">'+(comp.status||'—')+'</div>'
  +(linhaPot?'<div style="font-size:10px;color:#c9d1d9;margin-top:2px;">'+linhaPot+'</div>':'')
  +(comp.motivo?'<div style="font-size:10px;color:#8b949e;margin-top:2px;">'+comp.motivo+'</div>':'')
  +'<div style="font-size:9px;color:#8b949e;margin-top:4px;">Indica reprodutibilidade do padrão fisiológico observado entre as sessões; não constitui confirmação estatística isolada do breakpoint.</div>'
  +'</div>';
}

function _vstCartaoRpe(d){
 const r1=d.comparacao_rpe_bp1, r2=d.comparacao_rpe_bp2;
 function linha(nome, r, compBp){
  if(!r || r.status==='DADOS INSUFICIENTES')
   return '<div style="margin-top:4px;"><b>'+nome+'</b><br><span class="sub" style="font-size:11px;">DADOS INSUFICIENTES</span></div>';
  return '<div style="margin-top:4px;"><b>'+nome+'</b><br>'
   +'<span style="font-size:11px;">D1: '+r.dia1.inicial+' → '+r.dia1.final+' (Δ'+(r.dia1.delta>=0?'+':'')+r.dia1.delta+')</span><br>'
   +'<span style="font-size:11px;">D2: '+r.dia2.inicial+' → '+r.dia2.final+' (Δ'+(r.dia2.delta>=0?'+':'')+r.dia2.delta+')</span><br>'
   +'<span style="font-size:11px;color:'+_vstCorGeral(r.status)+';">'+r.dia2.direccao+' '+r.status.toLowerCase()+'</span>'
   +'<div style="font-size:9px;color:#8b949e;margin-top:2px;">'+_vstFraseRpeFisiologia(compBp, r)+'</div></div>';
 }
 return '<div class="card"><div class="label">RPE — Dia 1 × Dia 2 <span class="sub" style="font-size:9px;">(complementar)</span></div>'
  + linha('BP1', r1, d.comparacao_bp1) + linha('BP2', r2, d.comparacao_bp2) + '</div>';
}

function mxVstResumoCartoes(d){
 const box=document.getElementById('mxVstResumoCartoes');
 if(!box) return;
 const bp1=d.comparacao_bp1||{}, bp2=d.comparacao_bp2||{};
 const rec1=d.comparacao_recovery_bp1||{}, rec2=d.comparacao_recovery_bp2||{};
 // "resultado geral" e' so' uma apresentacao lado a lado do pior dos
 // dois BP -- nao e' um score novo, e diz-se isso explicitamente
 const ordem={'DIVERGENTE':0,'PARCIALMENTE CONSISTENTE':1,'DADOS INSUFICIENTES':1,'CONSISTENTE':2};
 const pior = (ordem[bp1.status]??1) <= (ordem[bp2.status]??1) ? bp1.status : bp2.status;
 const nRec = (rec1.n_validas||0)+(rec2.n_validas||0);
 box.innerHTML = '<div class="cards">'
  + _vstCartaoSimples('Resultado geral', pior, 'o mais cauteloso entre BP1 e BP2 — não é um score novo')
  + _vstCartaoBP('BP1', bp1)
  + _vstCartaoBP('BP2', bp2)
  + _vstCartaoSimples('Recovery', (ordem[rec1.status]??1)<=(ordem[rec2.status]??1)?rec1.status:rec2.status,
      'BP1: '+(rec1.status||'—')+' · BP2: '+(rec2.status||'—'))
  + '<div class="card"><div class="label">Robustez</div>'
  + '<div class="value" style="font-size:13px;">'+nRec+' observações de recovery</div>'
  + '<div style="font-size:10px;color:#8b949e;margin-top:2px;">p-permutação por métrica na secção de recovery abaixo — nunca um score único</div></div>'
  + '</div>'
  + _vstCartaoRpe(d);
}

function _vstFraseTiming(padraoInfo){
 if(!padraoInfo || padraoInfo.padrao==='DADOS INSUFICIENTES')
  return 'Poucos recoveries disponíveis para determinar timing.';
 if(padraoInfo.padrao==='PROGRESSIVAMENTE PIOR')
  return 'Recovery apresenta deterioração progressiva nos WORKs de maior potência.';
 if(padraoInfo.padrao==='PROGRESSIVAMENTE MELHOR')
  return 'Recovery melhora ao longo dos WORKs — sem sinal de deterioração com a carga.';
 if(padraoInfo.padrao==='INCONSISTENTE')
  return 'Recovery varia sem um padrão contínuo claro entre os WORKs.';
 return 'Não foi identificada alteração temporal consistente (recovery estável).';
}

function _vstFraseInterpretacaoRecovery(statusComp, padraoTxt, nomeMetrica){
 // duas frases, cada uma respondendo so' a UMA das perguntas -- nenhum
 // calculo novo, so' o texto fixo pedido em torno dos valores ja'
 // calculados (statusComp e padraoTxt)
 const dirTxt = statusComp==='CONVERGENTE'
  ? 'a direção da resposta de recuperação é compatível entre as sessões.'
  : statusComp==='PARCIALMENTE CONVERGENTE'
  ? 'a direção da resposta de recuperação é parcialmente compatível entre as sessões.'
  : statusComp==='DADOS INSUFICIENTES'
  ? 'não há dados suficientes para comparar a direção da resposta entre as sessões.'
  : 'a direção da resposta de recuperação não é compatível entre as sessões.';
 const padTxt = !padraoTxt || padraoTxt==='DADOS INSUFICIENTES'
  ? 'não há dados suficientes para avaliar progressão temporal dentro do bloco.'
  : padraoTxt==='ESTÁVEL'
  ? 'não foi identificada alteração temporal consistente entre os recoveries.'
  : padraoTxt==='INCONSISTENTE'
  ? 'os recoveries individuais não apresentam progressão temporal consistente entre os WORKs.'
  : padraoTxt==='PROGRESSIVAMENTE PIOR'
  ? 'os recoveries pioram progressivamente ao longo dos WORKs.'
  : 'os recoveries melhoram progressivamente ao longo dos WORKs.';
 return 'Dia 1 × Dia 2: '+dirTxt+' Dentro do Dia 2, '+padTxt;
}

function _vstCartaoRecoveryBloco(titulo, comp){
 if(!comp || !comp.metricas) return '<div class="card"><div class="label">'+titulo+'</div>'
  +'<div class="value" style="font-size:13px;">DADOS INSUFICIENTES</div></div>';
 // usa HR como referencia principal para o padrao do bloco mostrado no
 // cartao (a tabela completa, abaixo, mostra todas as metricas)
 const ref = comp.metricas.hr || comp.metricas.respiracao || Object.values(comp.metricas)[0];
 const pb = (ref||{}).dia2_padrao_bloco;
 const padraoTxt = pb && pb.padrao;
 // DUAS perguntas diferentes, nunca misturadas na mesma linha:
 // A) Dia1 x Dia2 -- direccao compativel? (comp.status)
 // B) dentro do Dia2 -- ha' progressao temporal entre os recoveries? (padraoTxt)
 let h = '<div class="card"><div class="label">'+titulo+'</div>'
  +'<div style="font-size:10px;color:#8b949e;margin-top:4px;">Dia 1 × Dia 2</div>'
  +'<div class="value" style="font-size:14px;color:'+_vstCorGeral(comp.status)+';">'+(comp.status||'—')+'</div>'
  +'<div style="font-size:10px;color:#8b949e;margin-top:6px;">Padrão temporal dentro do Dia 2</div>'
  +'<div style="font-size:13px;color:'+_vstCorGeral(padraoTxt)+';">'+(padraoTxt||'DADOS INSUFICIENTES')+'</div>';
 if(pb && pb.fracoes && pb.fracoes.length)
  h += '<div style="font-size:10px;color:#8b949e;margin-top:2px;">Frações ('+((ref||{}).nome||'')+'): '+pb.fracoes.map(f=>f.toFixed(2)).join(' → ')+'</div>';
 if(pb && pb.p_permutacao!=null)
  h += '<div style="font-size:10px;color:#8b949e;">p-permutação: '+pb.p_permutacao+(pb.significativo?'':' (não significativo)')+'</div>';
 h += '<div style="font-size:10px;color:#c9d1d9;margin-top:6px;">'
  +_vstFraseInterpretacaoRecovery(comp.status, padraoTxt, (ref||{}).nome)+'</div>';
 h += '</div>';
 return h;
}

function mxVstRecoveryCartoes(d){
 const box=document.getElementById('mxVstRecoveryCartoes');
 if(!box) return;
 box.innerHTML = '<div class="cards">'
  + _vstCartaoRecoveryBloco('Recovery BP1', d.comparacao_recovery_bp1)
  + _vstCartaoRecoveryBloco('Recovery BP2', d.comparacao_recovery_bp2)
  + '</div>'
  + _vstCartaoDia1Dia2Recovery('BP1', d.comparacao_recovery_bp1)
  + _vstCartaoDia1Dia2Recovery('BP2', d.comparacao_recovery_bp2);
}

function _vstSetaDireccao(estado){
 // so' apresentacao -- deriva de 'estado' (recuperou/nao_recuperou) ja
 // calculado por metricas_recuperacao, nao e' um criterio novo
 if(estado==='recuperou') return '↓/↑ (na direcção esperada)';
 if(estado==='nao_recuperou') return '↔ (não recuperou)';
 return '—';
}

function _vstFraccaoTexto(fracoes){
 const validas=(fracoes||[]).filter(f=>f!=null);
 if(!validas.length) return 'não disponível';
 return validas.map(f=>f.toFixed(2)).join(' → ');
}

function _vstAuditoriaLinha(canal, m){
 const d1=m.dia1;
 const dia1Texto = d1
  ? (d1.inicial+' → '+d1.final+m.unidade+' (Δ='+d1.delta+', '+_vstSetaDireccao(d1.estado)+')')
  : 'DIA 1 — sem recuperação/transição válida disponível';
 const individuais = m.dia2_recuperacoes_individuais||[];
 const dia2Linhas = individuais.map(function(c2,i){
  if(!c2) return 'REC'+(i+1)+': sem dados';
  return 'REC'+(i+1)+': '+c2.inicial+' → '+c2.final+m.unidade;
 }).join('<br>');
 const pb = m.dia2_padrao_bloco;
 const padraoTxt = pb ? pb.padrao : 'DADOS INSUFICIENTES';
 const fraccoesTxt = _vstFraccaoTexto(m.dia2_fracoes);

 // as DUAS janelas, lado a lado, nunca uma escondendo a outra -- e' a
 // unica forma de ver directamente se um OVERSHOOT vem so' da janela
 // completa ou ja' aparece nos primeiros 60s (pedido explicito)
 const j60=m.dia2_ultima, jComp=m.dia2_ultima_completa;
 let janelaTxt = '—';
 if(j60 || jComp){
  const t60 = j60 && j60.fracao!=null
   ? '0–60s: '+j60.fracao.toFixed(2)+' → <b>'+j60.completude+'</b>'+(j60.duracao_s!=null&&j60.duracao_s<60?' <span style="color:#F0883E;">(janela &lt; 60s)</span>':'')
   : '0–60s: sem dados suficientes';
  const tComp = jComp && jComp.fracao!=null
   ? 'Completo: '+jComp.fracao.toFixed(2)+' → <b>'+jComp.completude+'</b>'+(jComp.duracao_s!=null?' ('+Math.round(jComp.duracao_s)+'s)':'')
   : 'Completo: sem dados suficientes';
  janelaTxt = t60+'<br>'+tComp;
  if(j60 && jComp && j60.completude!==jComp.completude)
   janelaTxt += '<br><span style="color:#F4D03F;font-size:9px;">⚠ classificação difere entre janelas</span>';
 }

 let obs = '';
 if(m.peso==='complementar') obs = 'DFA1 (complementar) — evidência autonómica, não decide sozinha.';
 else if(canal==='thb') obs = 'THb (contextual) — interpretado junto com SmO2, não é prova independente.';
 else if(m.consistencia==='DIVERGENTE') obs = 'Discordante — mostrado, não escondido.';
 else if(m.consistencia==='INDETERMINADA') obs = 'Sem dados suficientes para comparar.';

 return '<tr style="border-top:1px solid #21262d;'+(m.peso!=='principal'?'opacity:0.75;':'')+'">'
  +'<td style="padding:4px 10px 4px 0;"><b>'+m.nome+'</b></td>'
  +'<td style="padding:4px 10px;font-size:10px;">'+dia1Texto+'</td>'
  +'<td style="padding:4px 10px;font-size:10px;">'+(dia2Linhas||'sem dados')+'</td>'
  +'<td style="padding:4px 10px;font-size:10px;">Padrão: <b>'+padraoTxt+'</b><br>Frações: '+fraccoesTxt+'</td>'
  +'<td style="padding:4px 10px;font-size:10px;">'+janelaTxt+'</td>'
  +'<td style="padding:4px 10px;color:'+_vstCorConsistencia(m.consistencia)+';">'+m.consistencia+'</td>'
  +'<td style="padding:4px 10px;font-size:10px;">'+(m.peso==='principal'?'principal':m.peso)+'</td>'
  +'<td style="padding:4px 10px;font-size:10px;color:#8b949e;">'+obs+'</td>'
  +'</tr>';
}

function mxVstAuditoriaRecovery(titulo, comp){
 if(!comp) return '';
 if(comp.status==='DADOS INSUFICIENTES' && !Object.keys(comp.metricas||{}).length){
  return '<div style="margin:10px 0;"><h4 style="font-size:13px;">Recovery — Dia 1 × Dia 2 ('+titulo+')</h4>'
   +'<p class="sub" style="font-size:12px;">DADOS INSUFICIENTES — '+(comp.motivo||
     'não existe recuperação válida num dos dois dias para comparação.')+'</p></div>';
 }

 const metricas = comp.metricas||{};
 const chaves = Object.keys(metricas);
 const convergentes = chaves.filter(k=>metricas[k].consistencia==='CONVERGENTE');
 const divergentes = chaves.filter(k=>metricas[k].consistencia==='DIVERGENTE');
 const indeterminadas = chaves.filter(k=>metricas[k].consistencia==='INDETERMINADA');

 const nota = comp.status==='CONVERGENTE'
  ? 'Este resultado indica reprodutibilidade do padrão observado, não uma confirmação estatística do breakpoint em si.'
  : (comp.motivo||'');

 let h = '<div style="margin:10px 0;">';
 h += '<h4 style="font-size:13px;">Recovery — Dia 1 × Dia 2 ('+titulo+'): '
   +'<span style="color:'+_vstCorGeral(comp.status)+';">'+(comp.status||'—')+'</span></h4>';
 h += '<p style="font-size:11px;color:#8b949e;margin:2px 0 8px;">'+nota+'</p>';

 // tabela de auditoria, uma linha por metrica -- nada escondido
 h += '<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;">'
  +'<th style="padding:4px 10px 4px 0;">Métrica</th>'
  +'<th style="padding:4px 10px;">Dia 1</th>'
  +'<th style="padding:4px 10px;">Dia 2 (recoveries individuais)</th>'
  +'<th style="padding:4px 10px;">Padrão D2 (dentro do bloco)</th>'
  +'<th style="padding:4px 10px;">Janela (comparação vs completo)</th>'
  +'<th style="padding:4px 10px;">Comparação</th>'
  +'<th style="padding:4px 10px;">Peso</th>'
  +'<th style="padding:4px 10px;">Observação</th></tr>';
 chaves.forEach(function(k){ h += _vstAuditoriaLinha(k, metricas[k]); });
 h += '</table>';

 // "Porque?" -- explicacao objectiva, so' lendo o que ja calculamos
 h += '<details style="margin-top:8px;"><summary style="cursor:pointer;font-size:12px;color:#8b949e;">Porquê?</summary>'
  +'<div style="font-size:11px;margin-top:6px;padding-left:4px;">';
 chaves.forEach(function(k){
  const m=metricas[k];
  h += '<div>'+m.nome+(m.peso!=='principal'?' ('+m.peso+')':'')+': <b style="color:'
    +_vstCorConsistencia(m.consistencia)+';">'+m.consistencia.toLowerCase()+'</b></div>';
 });
 h += '</div></details>';
 h += '</div>';
 return h;
}

function _vstCartaoDia1Dia2Recovery(titulo, comp){
 // mantida por compatibilidade -- a versao completa e' mxVstAuditoriaRecovery
 return mxVstAuditoriaRecovery(titulo, comp);
}

// Tabela detalhada RPE -- Bloco/WORK/Potencia D1/RPE D1/Potencia D2/
// RPE D2/DeltaRPE. Dia1 so' tem UM par (base->alvo) por BP -- mostra-se
// so' na primeira linha desse bloco, "—" nas restantes (nunca inventar
// uma correspondencia por-WORK que nao existe).
function mxVstRpeTabela(d){
 const box=document.getElementById('mxVstRpeTabela');
 if(!box) return;
 const linhas=[];
 [['BP1', (d.bp1&&d.bp1.metricas)||[], d.comparacao_rpe_bp1],
  ['BP2', (d.bp2&&d.bp2.metricas)||[], d.comparacao_rpe_bp2]]
 .forEach(function(t){
  const bloco=t[0], metricas=t[1], compRpe=t[2];
  const valores = compRpe && compRpe.dia2 ? compRpe.dia2.valores : null;
  metricas.forEach(function(iv,i){
   const pot2 = iv.potencia && iv.potencia.ok ? Math.round(iv.potencia.media) : null;
   const rpe2 = valores ? valores[i] : null;
   const primeira = i===0;
   const pot1 = primeira && compRpe && compRpe.dia1 ? null : null;  // Dia1 nao tem potencia propria aqui, so' RPE
   const rpe1 = primeira && compRpe && compRpe.dia1 ? compRpe.dia1.final : null;
   const deltaRpe = (rpe1!=null && rpe2!=null) ? rpe2-rpe1 : null;
   linhas.push({bloco:bloco, work:'W'+(i+1), pot1:pot1, rpe1:rpe1, pot2:pot2, rpe2:rpe2, deltaRpe:deltaRpe});
  });
 });
 if(!linhas.length){ box.innerHTML=''; return; }
 let h='<h4 style="font-size:13px;">RPE — Dia 1 × Dia 2</h4>'
  +'<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;">'
  +'<th style="padding:3px 10px 3px 0;">Bloco</th><th style="padding:3px 10px;">WORK</th>'
  +'<th style="padding:3px 10px;">RPE D1</th>'
  +'<th style="padding:3px 10px;">Potência D2</th><th style="padding:3px 10px;">RPE D2</th>'
  +'<th style="padding:3px 10px;">ΔRPE</th></tr>';
 linhas.forEach(function(l){
  h+='<tr style="border-top:1px solid #21262d;">'
   +'<td style="padding:3px 10px 3px 0;">'+l.bloco+'</td><td style="padding:3px 10px;">'+l.work+'</td>'
   +'<td style="padding:3px 10px;">'+(l.rpe1!=null?l.rpe1:'—')+'</td>'
   +'<td style="padding:3px 10px;">'+(l.pot2!=null?l.pot2+'W':'—')+'</td>'
   +'<td style="padding:3px 10px;">'+(l.rpe2!=null?l.rpe2:'—')+'</td>'
   +'<td style="padding:3px 10px;">'+(l.deltaRpe!=null?(l.deltaRpe>=0?'+':'')+l.deltaRpe:'—')+'</td></tr>';
 });
 h+='</table>'
  +'<p class="sub" style="font-size:9px;margin-top:4px;">RPE D1 mostrado só na primeira linha de cada bloco — o Dia 1 fornece um único par base→alvo por BP, não um valor por WORK; não se inventa correspondência que não existe.</p>';
 box.innerHTML=h;
}

// RPE x fisiologia -- interpretacao automatica, so' quando ha dados
// suficientes. Compara a direccao do RPE (Dia2) com a direccao
// predominante de HR/RF/SmO2 (Dia2, ja calculada em comparar_bp) --
// nunca decide BP, so' descreve o que ja esta calculado.
function _vstFraseRpeFisiologia(compBp, compRpe){
 if(!compRpe || compRpe.status==='DADOS INSUFICIENTES' || !compRpe.dia2)
  return 'RPE: DADOS INSUFICIENTES.';
 const metricas=(compBp&&compBp.metricas)||{};
 const chaves=['hr','respiracao','smo2'];
 const direccoes=chaves.map(k=>metricas[k]&&metricas[k].direccao_dia2).filter(Boolean);
 if(!direccoes.length) return 'RPE: dados fisiológicos insuficientes para comparar.';
 const nSubindo=direccoes.filter(d=>d==='↑').length;
 const predominante = nSubindo>direccoes.length/2 ? '↑' : (direccoes.every(d=>d==='→')?'→':'misto');
 const rpeDir=compRpe.dia2.direccao;
 if(predominante==='↑' && rpeDir==='↑')
  return 'RPE compatível com a resposta fisiológica: o aumento da percepção de esforço acompanha a mudança observada nos principais indicadores fisiológicos.';
 if(predominante==='↑' && rpeDir!=='↑')
  return 'RPE divergente dos indicadores fisiológicos: a percepção subjectiva permaneceu relativamente estável apesar das alterações observadas nos indicadores fisiológicos.';
 return 'RPE: padrão sem convergência clara com os indicadores fisiológicos nesta janela.';
}

function mxVstRecoveryTabela(d){
 const box=document.getElementById('mxVstRecoveryTabela');
 if(!box) return;
 let h='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;">'
  +'<th style="padding:3px 10px 3px 0;">Bloco</th>'
  +'<th style="padding:3px 10px;">Recovery #</th>'
  +'<th style="padding:3px 10px;">Métrica</th>'
  +'<th style="padding:3px 10px;">Inicial → Final</th>'
  +'<th style="padding:3px 10px;">Δ</th>'
  +'<th style="padding:3px 10px;">Fracção</th>'
  +'<th style="padding:3px 10px;">Completude</th></tr>';
 let linhas = 0;
 [['BP1', d.comparacao_recovery_bp1], ['BP2', d.comparacao_recovery_bp2]].forEach(function(par){
  const bloco=par[0], comp=par[1];
  if(!comp || !comp.metricas) return;
  Object.keys(comp.metricas).forEach(function(k){
   const m=comp.metricas[k];
   const individuais=m.dia2_recuperacoes_individuais||[];
   const fracoes=m.dia2_fracoes||[];
   individuais.forEach(function(c2, i){
    if(!c2) return;
    const frac=fracoes[i];
    const completude = (typeof _vstCompletudeTexto==='function') ? '' : '';
    linhas++;
    h+='<tr style="border-top:1px solid #21262d;'+(m.peso==='complementar'?'opacity:0.7;':'')+'">'
     +'<td style="padding:3px 10px 3px 0;">'+bloco+'</td>'
     +'<td style="padding:3px 10px;">'+(i+1)+'</td>'
     +'<td style="padding:3px 10px;">'+m.nome+(m.peso==='complementar'?' <span class="sub" style="font-size:9px;">(complementar)</span>':'')+'</td>'
     +'<td style="padding:3px 10px;">'+c2.inicial+' → '+c2.final+'</td>'
     +'<td style="padding:3px 10px;">'+c2.delta+'</td>'
     +'<td style="padding:3px 10px;">'+(frac!=null?frac.toFixed(3):'—')+'</td>'
     +'<td style="padding:3px 10px;">'+(frac!=null?_vstClassificacaoFraccaoTexto(frac):'DADOS INSUFICIENTES')+'</td>'
     +'</tr>';
   });
  });
 });
 h+='</table>';
 if(!linhas) h = '<p class="sub" style="font-size:12px;">sem recuperações individuais disponíveis</p>';
 else h += '<p class="sub" style="font-size:10px;margin-top:4px;">Classificação operacional baseada na fração de recuperação utilizada pela metodologia atual — não são limiares fisiológicos validados.</p>';
 box.innerHTML = h;
}

// mesma classificacao de classificar_recovery_completeness (Python),
// replicada aqui so' para nao ter de ir ao servidor por cada celula da
// tabela -- os LIMIARES sao os mesmos, nao uma segunda metodologia
function _vstClassificacaoFraccaoTexto(f){
 if(f==null) return 'DADOS INSUFICIENTES';
 if(f>1) return 'OVERSHOOT';
 if(f<0) return 'SEM RECUPERAÇÃO / CONTINUAÇÃO';
 if(f>=0.75) return 'RECUPERAÇÃO COMPLETA';
 if(f>=0.4) return 'RECUPERAÇÃO PARCIAL';
 return 'RECUPERAÇÃO MÍNIMA';
}

function mxVstRecoveryFinalMostrar(rf){
 const box=document.getElementById('mxVstRecoveryFinal');
 if(!box) return;
 if(!rf || !rf.ok){
  box.innerHTML='';
  return;
 }
 const pc = rf.por_canal||{};
 const nomes={hr:'HR', respiracao:'RF', smo2:'SmO2', thb:'THb', dfa1:'DFA1'};
 const ordem=['hr','respiracao','smo2','thb','dfa1'];
 const validos = ordem.filter(k=>pc[k] && pc[k].estado!=='sem_dados');
 // "Status" e' so' um resumo de apresentacao dos estados por-canal
 // ja calculados (recuperou/nao_recuperou) -- nao e' um valor novo
 const recuperaram = validos.filter(k=>pc[k].estado==='recuperou');
 const status = !validos.length ? 'DADOS INSUFICIENTES'
  : recuperaram.length===validos.length ? 'RECUPEROU'
  : recuperaram.length===0 ? 'NÃO RECUPEROU' : 'PARCIAL';

 let linhas = '';
 ordem.forEach(function(k){
  const c=pc[k];
  if(!c || c.estado==='sem_dados') return;
  linhas += '<div style="display:flex;justify-content:space-between;font-size:11px;padding:1px 0;">'
   +'<span style="color:#8b949e;">'+nomes[k]+'</span>'
   +'<span>'+c.inicial+' → '+c.final+'</span></div>';
 });

 box.innerHTML = '<div class="card" style="max-width:260px;">'
  +'<div class="label">RECOVERY FINAL</div>'
  + linhas
  +'<div style="font-size:11px;margin-top:4px;color:'+_vstCorGeral(status)+';"><b>Status: '+status+'</b></div>'
  +'<div style="font-size:9px;color:#8b949e;margin-top:4px;">Após o último WORK; não comparável à completude dos recoveries intermediários.</div>'
  +'</div>';
}

function mxVstBlocoAuditoria(titulo, comp){
 // item 18: "COMO O RECOVERY FOI CLASSIFICADO?" -- so' organiza o que
 // ja esta em comp, nao calcula nada de novo
 if(!comp) return '';
 const metricas = comp.metricas||{};
 const chaves = Object.keys(metricas);
 const convergentes = chaves.filter(k=>metricas[k].consistencia==='CONVERGENTE'&&metricas[k].peso==='principal');
 const divergentes = chaves.filter(k=>metricas[k].consistencia==='DIVERGENTE'&&metricas[k].peso==='principal');
 const ref = metricas.hr||metricas.respiracao||Object.values(metricas)[0]||{};
 const pb = ref.dia2_padrao_bloco;
 const n1 = chaves.filter(k=>metricas[k].dia1).length;
 const n2 = chaves.length ? (metricas[chaves[0]].dia2_recuperacoes_individuais||[]).length : 0;

 return '<details style="margin:8px 0;"><summary style="cursor:pointer;font-size:12px;color:#8b949e;">'
  +'Como o recovery foi classificado? ('+titulo+')</summary>'
  +'<div style="font-size:11px;margin-top:6px;padding-left:6px;">'
  +'<div><b>1. Evidência Dia 1</b>: '+n1+' métrica(s) com dados de recuperação/transição válidos.</div>'
  +'<div><b>2. Evidência Dia 2</b>: '+n2+' recovery(s) intermediário(s) por métrica.</div>'
  +'<div><b>3. Comparação por métrica</b>: '+convergentes.length+' convergente(s), '+divergentes.length+' divergente(s) (só métricas principais contam para o resultado).</div>'
  +'<div><b>4. Timing</b>: '+_vstFraseTiming(pb)+'</div>'
  +'<div><b>5. Recovery progressivo (Dia 2)</b>: '+(pb?pb.padrao:'DADOS INSUFICIENTES')+'.</div>'
  +'<div><b>6. Limitações</b>: DFA1 é complementar e THb é contextual — nenhum dos dois decide sozinho; com poucos WORKs, a robustez estatística é necessariamente limitada.</div>'
  +'</div></details>';
}

function mxVstRevisaoCritica(d){
 const box=document.getElementById('mxVstRevisaoCritica');
 if(!box) return;
 const rec1=d.comparacao_recovery_bp1||{}, rec2=d.comparacao_recovery_bp2||{};
 const pb1=(rec1.metricas&&(rec1.metricas.hr||rec1.metricas.respiracao)||{}).dia2_padrao_bloco;
 const pb2=(rec2.metricas&&(rec2.metricas.hr||rec2.metricas.respiracao)||{}).dia2_padrao_bloco;
 const temDados = (rec1.n_validas||0)+(rec2.n_validas||0) > 0;

 function n2Recoveries(rec){
  const m=rec.metricas||{}; const k=Object.keys(m)[0];
  return k ? (m[k].dia2_recuperacoes_individuais||[]).length : 0;
 }
 function convDiv(rec){
  const m=rec.metricas||{};
  const conv=Object.keys(m).filter(k=>m[k].consistencia==='CONVERGENTE');
  const div=Object.keys(m).filter(k=>m[k].consistencia==='DIVERGENTE');
  return {conv:conv, div:div};
 }
 const cd1=convDiv(rec1), cd2=convDiv(rec2);

 function resp(txt){ return '<li style="margin-bottom:4px;">'+txt+'</li>'; }
 let h = '<h4 style="font-size:13px;">Revisão crítica — Recovery</h4>';
 h += mxVstBlocoAuditoria('BP1', rec1) + mxVstBlocoAuditoria('BP2', rec2);
 h += '<ol style="font-size:11px;color:#c9d1d9;padding-left:18px;margin-top:10px;">';
 h += resp('Qual recovery existe no Dia 1? '+(rec1.metricas&&Object.values(rec1.metricas).some(m=>m.dia1)?'Uma transição/recovery válida, logo a seguir ao bloco escolhido como BP.':'Nenhum disponível ou insuficiente.'));
 h += resp('Quantos recoveries existem no Dia 2? BP1: '+n2Recoveries(rec1)+' · BP2: '+n2Recoveries(rec2)+'.');
 h += resp('Quais métricas têm dados válidos em ambos? BP1: '+(cd1.conv.concat(cd1.div).join(', ')||'nenhuma')+' · BP2: '+(cd2.conv.concat(cd2.div).join(', ')||'nenhuma')+'.');
 h += resp('Quais convergem? BP1: '+(cd1.conv.join(', ')||'—')+' · BP2: '+(cd2.conv.join(', ')||'—')+'.');
 h += resp('Quais divergem? BP1: '+(cd1.div.join(', ')||'nenhuma')+' · BP2: '+(cd2.div.join(', ')||'nenhuma')+'.');
 h += resp('Padrão do Dia 2: BP1 = '+(pb1?pb1.padrao:'DADOS INSUFICIENTES')+' · BP2 = '+(pb2?pb2.padrao:'DADOS INSUFICIENTES')+'.');
 h += resp('Padrão do Dia 1: uma resposta pontual (transição), não um bloco — direcção mostrada na tabela de auditoria acima, não uma tendência ao longo de vários WORKs.');
 h += resp('O timing é comparável? '+_vstFraseTiming(pb1)+' (BP1) / '+_vstFraseTiming(pb2)+' (BP2) — nota: Dia 1 é uma transição pontual, Dia 2 é um bloco sustentado; não são a mesma coisa, só compatíveis em padrão.');
 h += resp('Porque o resultado final foi '+(rec1.status||'—')+' (BP1) / '+(rec2.status||'—')+' (BP2)? Ver "Como o recovery foi classificado?" acima, e o "Porquê?" em cada tabela de auditoria.');
 h += resp('Limitações: DFA1 complementar, THb contextual, poucos WORKs por bloco limitam a robustez estatística — ver p-permutação nos cartões.');
 h += '</ol><p class="sub" style="font-size:10px;">Esta revisão é descritiva. Não constitui diagnóstico, não afirma causalidade, e não afirma que o breakpoint foi validado.</p>';
 box.innerHTML = h;
}

// Grafico temporal principal do Dia 2 -- desenhado a partir dos
// blocos ja calculados (t0/t1/watts_medio por WORK/RECOVERY), sem
// pedir o stream completo outra vez. Mostra potencia ao longo do
// tempo real da sessao, com WORK/RECOVERY/aquecimento/BP1/BP2
// identificados por cor, e hover com os detalhes de cada bloco.
function mxDesenharVstTemporal(d){
 const o = ctx('chMxVstTemporal', 240); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const aq = d.aquecimento && d.aquecimento.bloco;
 const bp1blocos = (d.bp1 && d.bp1.blocos) || [];
 const bp2blocos = (d.bp2 && d.bp2.blocos) || [];
 const todos = (aq?[Object.assign({},aq,{grupo:'aquecimento'})]:[])
  .concat(bp1blocos.map(b=>Object.assign({},b,{grupo:'bp1'})))
  .concat(bp2blocos.map(b=>Object.assign({},b,{grupo:'bp2'})))
  .sort((a,b)=>a.t0-b.t0);
 if(!todos.length){ noData(g,W,H,'Sem blocos para desenhar'); return; }

 const cores={aquecimento:'#8b949e', bp1:'#5DADE2', bp2:'#F0883E'};
 const PL=48, PR=16, PT=16, PB=42;
 const w=W-PL-PR, h=H-PT-PB;
 const tMin=Math.min.apply(null, todos.map(b=>b.t0));
 const tMax=Math.max.apply(null, todos.map(b=>b.t1));
 const potMax=Math.max.apply(null, todos.map(b=>b.watts_medio_da_api||b.watts_medio||0))*1.15;
 const X=t=>PL+(t-tMin)/((tMax-tMin)||1)*w;
 const Y=p=>PT+h-(p/(potMax||1))*h;

 g.clearRect(0,0,W,H);
 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='10px sans-serif';
 for(let i=0;i<=4;i++){
  const pv=potMax*i/4, y=Y(pv);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(Math.round(pv), PL-6, y+3);
 }
 g.textAlign='center';
 [0,0.25,0.5,0.75,1].forEach(function(f){
  const tv=tMin+(tMax-tMin)*f;
  g.fillText(Math.round(tv/60)+'min', X(tv), PT+h+16);
 });

 // faixa de fundo por grupo (aquecimento/bp1/bp2), para se ver a
 // ESTRUTURA do protocolo de relance, nao so' a potencia
 const rects=[];
 const contagem={};
 todos.forEach(function(b){
  const pot=b.watts_medio_da_api||b.watts_medio||0;
  const x0=X(b.t0), x1=X(b.t1);
  g.fillStyle=cores[b.grupo]; g.globalAlpha=0.12;
  g.fillRect(x0,PT,Math.max(1,x1-x0),h);
  g.globalAlpha=1;
  contagem[b.grupo]=(contagem[b.grupo]||0)+1;
  rects.push({x0:x0,x1:x1,t0:b.t0,t1:b.t1,pot:pot,grupo:b.grupo,tipo:'WORK',
    numero:contagem[b.grupo]});
 });
 // faixa das RECOVERY, entre cada par de blocos consecutivos -- e' o
 // espaco que sobra, mostrado a potencia baixa (nao temos o stream
 // segundo a segundo aqui, mas a ESTRUTURA fica visivel)
 for(let i=0;i<todos.length-1;i++){
  const fimA=todos[i].t1, inicioB=todos[i+1].t0;
  if(inicioB<=fimA) continue;
  const x0=X(fimA), x1=X(inicioB);
  g.fillStyle='#30363d'; g.globalAlpha=0.5;
  g.fillRect(x0,PT,Math.max(1,x1-x0),h);
  g.globalAlpha=1;
  rects.push({x0:x0,x1:x1,t0:fimA,t1:inicioB,pot:null,
    grupo:todos[i].grupo,tipo:'RECOVERY',numero:contagem[todos[i].grupo]});
 }

 // LINHA de potencia -- sobe no WORK (do inicio ao fim do bloco, no
 // mesmo nivel, ja que usamos a media), desce para perto de zero na
 // RECOVERY e volta a subir no proximo WORK. E' isto que da' a leitura
 // de "estrutura da sessao", nao barras soltas.
 if(MX_VST_TEMPORAL_VISIVEL.power){
  g.strokeStyle='#c9d1d9'; g.lineWidth=2; g.beginPath();
  let primeiro=true;
  todos.forEach(function(b,i){
   const pot=b.watts_medio_da_api||b.watts_medio||0;
   const x0=X(b.t0), x1=X(b.t1), y=Y(pot);
   if(primeiro){ g.moveTo(x0,Y(0)); primeiro=false; }
   g.lineTo(x0,y); g.lineTo(x1,y);
   const proximo=todos[i+1];
   if(proximo && proximo.t0>b.t1) g.lineTo(X(proximo.t0), Y(0));
  });
  g.stroke(); g.lineWidth=1;
 }

 // HR/RF/SmO2 -- so' se os streams completos (Dia 2) ja' chegaram.
 // Normalizados para 0-100% da AMPLITUDE DA PROPRIA VARIAVEL (opcao B
 // do pedido) -- nunca na mesma escala numerica da potencia, e a
 // legenda diz explicitamente que estao normalizados.
 const fisioCores={heartrate:'#E3B341', respiration:'#79C0FF', smo2:'#F85149'};
 const fisioNomes={heartrate:'HR', respiration:'RF', smo2:'SmO2'};
 let temFisio=false;
 if(MX_VST_STREAMS && MX_VST_STREAMS.tempo && MX_VST_STREAMS.canais){
  const tempo=MX_VST_STREAMS.tempo;
  const passo=Math.max(1, Math.floor(tempo.length/600));  // amostragem para performance, nao invencao
  Object.keys(fisioCores).forEach(function(canal){
   const serie=MX_VST_STREAMS.canais[canal];
   if(!serie || !serie.length) return;
   const validos=[]; for(let i=0;i<serie.length;i+=passo){ if(serie[i]!=null) validos.push(serie[i]); }
   if(validos.length<2) return;
   temFisio=true;
   if(!MX_VST_TEMPORAL_VISIVEL[canal]) return;
   const vMin=Math.min.apply(null,validos), vMax=Math.max.apply(null,validos);
   const Yn=v=>PT+h-((v-vMin)/((vMax-vMin)||1))*h;
   g.strokeStyle=fisioCores[canal]; g.lineWidth=1; g.globalAlpha=0.85; g.beginPath();
   let comecou=false;
   for(let i=0;i<serie.length;i+=passo){
    const tv=tempo[i], val=serie[i];
    if(val==null){ comecou=false; continue; }
    const x=X(tv), y=Yn(val);
    if(!comecou){ g.moveTo(x,y); comecou=true; } else g.lineTo(x,y);
   }
   g.stroke(); g.globalAlpha=1;
  });
 }

 g.font='10px sans-serif'; g.textAlign='left';
 let lx=PL;
 g.fillStyle=cores.aquecimento; g.fillRect(lx,2,8,8); g.fillText('Aquecimento',lx+11,10); lx+=82;
 g.fillStyle=cores.bp1; g.fillRect(lx,2,8,8); g.fillText('BP1',lx+11,10); lx+=48;
 g.fillStyle=cores.bp2; g.fillRect(lx,2,8,8); g.fillText('BP2',lx+11,10); lx+=48;
 g.fillStyle='#c9d1d9'; g.fillRect(lx,2,8,8); g.fillText('Power (W)',lx+11,10); lx+=70;
 if(temFisio){
  Object.keys(fisioCores).forEach(function(canal){
   g.fillStyle=fisioCores[canal]; g.fillRect(lx,2,8,8);
   g.fillText(fisioNomes[canal],lx+11,10); lx+=52;
  });
 }
 if(temFisio){
  g.fillStyle='#8b949e'; g.font='9px sans-serif';
  g.fillText('HR/RF/SmO2 normalizados para 0–100% da própria amplitude — só a potência é em W reais', PL, PT+h+26);
 } else {
  g.fillStyle='#8b949e'; g.font='9px sans-serif';
  g.fillText('DADOS TEMPORAIS FISIOLÓGICOS NÃO DISPONÍVEIS PARA ESTA SESSÃO', PL, PT+h+26);
 }

 MX_HOVER.chMxVstTemporal = {rects:rects, PL:PL, w:w, xa:tMin, xb:tMax,
   streams: MX_VST_STREAMS, X:X};
}

function mxVstLimitacoes(d){
 const box=document.getElementById('mxVstLimitacoes');
 if(!box) return;
 const rec1=d.comparacao_recovery_bp1||{}, rec2=d.comparacao_recovery_bp2||{};
 const bp1=d.comparacao_bp1||{}, bp2=d.comparacao_bp2||{};
 const pontosCurtos = (bp1.n_validas||0)<3 || (bp2.n_validas||0)<3
  || (rec1.n_validas||0)<3 || (rec2.n_validas||0)<3;
 const itens = [
  'Timing do Dia 1 é pontual (transição na rampa); o Dia 2 é um bloco sustentado — compatibilidade de padrão, não igualdade.',
  'DFA1-α1 é evidência complementar — nunca decide sozinho CONSISTENTE/DIVERGENTE.',
  'THb é contextual — interpretado junto com SmO2, nunca como prova independente.',
  'A comparação directa de recovery usa o primeiro minuto do Dia 2 (janela comparável à transição de ~1min do Dia 1); o recovery completo continua disponível em "Detalhes".',
 ];
 if(pontosCurtos) itens.push('Poucos pontos nalgum bloco — a robustez do teste de permutação é necessariamente limitada.');
 box.innerHTML = '<ul style="font-size:11px;color:#c9d1d9;padding-left:18px;margin:4px 0;">'
  + itens.map(t=>'<li style="margin-bottom:3px;">'+t+'</li>').join('') + '</ul>';
}

function _vstFormatarMin(seg){
 const m=Math.floor(seg/60), s=Math.round(seg%60);
 return (m<10?'0':'')+m+':'+(s<10?'0':'')+s;
}

function mxLigarHoverVstTemporal(){
 const cv=document.getElementById('chMxVstTemporal');
 const tip=document.getElementById('mxTipVstTemporal');
 if(!cv || !tip) return;
 cv.addEventListener('mousemove', function(ev){
  const info=MX_HOVER.chMxVstTemporal;
  if(!info || !info.rects.length){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
  const mx=(ev.clientX-r.left)*esc;
  const bloco=info.rects.find(function(rr){ return mx>=rr.x0 && mx<=rr.x1; });
  if(!bloco){ tip.style.display='none'; return; }
  const tempoHover = bloco.t0 + (bloco.t1-bloco.t0) * Math.max(0,Math.min(1,(mx-bloco.x0)/((bloco.x1-bloco.x0)||1)));

  let linhas = ['TIME&nbsp;&nbsp;'+_vstFormatarMin(tempoHover)];
  // valores reais dos streams, no ponto mais proximo do tempo do hover
  // (indice mais proximo, nunca interpolado/inventado)
  const st=info.streams;
  let potTxt = bloco.pot!=null ? Math.round(bloco.pot)+' W' : '—';
  if(st && st.tempo && st.tempo.length){
   let melhorI=0, melhorD=Infinity;
   for(let i=0;i<st.tempo.length;i+=Math.max(1,Math.floor(st.tempo.length/2000))){
    const dd=Math.abs(st.tempo[i]-tempoHover);
    if(dd<melhorD){ melhorD=dd; melhorI=i; }
   }
   const c=st.canais||{};
   if(c.watts && c.watts[melhorI]!=null) potTxt=Math.round(c.watts[melhorI])+' W';
   linhas.push('POWER&nbsp;&nbsp;'+potTxt);
   if(c.heartrate && c.heartrate[melhorI]!=null) linhas.push('HR&nbsp;&nbsp;'+c.heartrate[melhorI].toFixed(1)+' bpm');
   if(c.respiration && c.respiration[melhorI]!=null) linhas.push('RF&nbsp;&nbsp;'+c.respiration[melhorI].toFixed(1)+' resp/min');
   if(c.smo2 && c.smo2[melhorI]!=null) linhas.push('SmO2&nbsp;&nbsp;'+c.smo2[melhorI].toFixed(1)+' %');
  } else {
   linhas.push('POWER&nbsp;&nbsp;'+potTxt);
  }
  const nomeGrupo={aquecimento:'Aquecimento',bp1:'BP1',bp2:'BP2'}[bloco.grupo]||bloco.grupo;
  linhas.push('BLOCO&nbsp;&nbsp;'+nomeGrupo+(bloco.tipo==='RECOVERY'?' Recovery #'+bloco.numero:(bloco.numero?' #'+bloco.numero:'')));
  linhas.push('TIPO&nbsp;&nbsp;'+bloco.tipo);

  tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+12, r.width-175)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-70)+'px';
  tip.innerHTML=linhas.join('<br>');
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

// ═══════════════════════════════════════════════════════════════════
// Graficos fisiologicos (Power x metrica), heatmap, timing, recovery
// x tempo -- tudo a partir de d.bp1.metricas[i]/d.bp2.metricas[i], ja'
// calculados por metricas_intervalo(). Nao recalcula nada.
// ═══════════════════════════════════════════════════════════════════

const MX_VST_CANAIS = [
 {chave:'hr', canvas:'chMxVstHR', tip:'mxTipVstHR', unidade:'bpm', cor:'#E3B341'},
 {chave:'respiracao', canvas:'chMxVstRF', tip:'mxTipVstRF', unidade:'', cor:'#79C0FF'},
 {chave:'smo2', canvas:'chMxVstSmO2', tip:'mxTipVstSmO2', unidade:'%', cor:'#F85149'},
 {chave:'thb', canvas:'chMxVstTHb', tip:'mxTipVstTHb', unidade:'', cor:'#58A6FF'},
 {chave:'dfa1', canvas:'chMxVstDFA1', tip:'mxTipVstDFA1', unidade:'', cor:'#D2A8FF'},
];

function mxDesenharVstFisiologico(canal, canvasId, unidade, cor, d){
 const o = ctx(canvasId, 170); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const bp1=(d.bp1&&d.bp1.metricas)||[], bp2=(d.bp2&&d.bp2.metricas)||[];
 const pontos=[];
 bp1.forEach(function(iv,i){
  const p=iv.potencia, m=iv[canal];
  if(p&&p.ok&&m&&m.ok) pontos.push({pot:p.media, inicial:m.inicial, final:m.final,
    delta_pct:m.delta_pct, grupo:'bp1', numero:i+1});
 });
 bp2.forEach(function(iv,i){
  const p=iv.potencia, m=iv[canal];
  if(p&&p.ok&&m&&m.ok) pontos.push({pot:p.media, inicial:m.inicial, final:m.final,
    delta_pct:m.delta_pct, grupo:'bp2', numero:i+1});
 });
 if(!pontos.length){ noData(g,W,H,'DADOS INSUFICIENTES'); return; }

 const cores={bp1:'#5DADE2', bp2:'#F0883E'};
 const PL=42, PR=12, PT=10, PB=24;
 const w=W-PL-PR, h=H-PT-PB;
 const xs=pontos.map(p=>p.pot);
 const ys=pontos.map(p=>p.inicial).concat(pontos.map(p=>p.final));
 const xa=Math.min.apply(null,xs)*0.95, xb=Math.max.apply(null,xs)*1.05;
 const ya=Math.min.apply(null,ys)*0.95, yb=Math.max.apply(null,ys)*1.05;
 const X=v=>PL+(v-xa)/((xb-xa)||1)*w;
 const Y=v=>PT+h-(v-ya)/((yb-ya)||1)*h;

 g.clearRect(0,0,W,H);
 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='9px sans-serif';
 g.textAlign='center';
 xs.forEach(function(xv){ g.fillText(Math.round(xv), X(xv), PT+h+12); });

 const rects=[];
 pontos.forEach(function(p){
  const x=X(p.pot);
  g.strokeStyle=cores[p.grupo]; g.lineWidth=2;
  g.beginPath(); g.moveTo(x,Y(p.inicial)); g.lineTo(x,Y(p.final)); g.stroke();
  g.fillStyle=cores[p.grupo];
  g.beginPath(); g.arc(x,Y(p.inicial),2.5,0,7); g.fill();
  g.beginPath(); g.arc(x,Y(p.final),4,0,7); g.fill();
  g.lineWidth=1;
  rects.push({x0:x-6,x1:x+6,pot:p.pot,inicial:p.inicial,final:p.final,
    delta_pct:p.delta_pct,grupo:p.grupo,numero:p.numero,unidade:unidade});
 });
 MX_HOVER[canvasId] = {rects:rects};
}

function mxDesenharVstFisiologicoTodos(d){
 MX_VST_CANAIS.forEach(function(c){
  mxDesenharVstFisiologico(c.chave, c.canvas, c.unidade, c.cor, d);
 });
 mxDesenharVstPowerRPE(d);
}

// Power x RPE -- um ponto por WORK (RPE ja e' um so' valor por WORK,
// nao inicial/final dentro do bloco como as fisiologicas), lido de
// comparacao_rpe_bpX.dia2.valores (ja calculado em /comparar). Sem
// normalizacao -- RPE fica em 1-10, como pedido.
function mxDesenharVstPowerRPE(d){
 const o = ctx('chMxVstRPE', 170); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const comp = MX_VST_ULT_COMP;
 const cores={bp1:'#5DADE2', bp2:'#F0883E'};
 const pontos=[];
 [['bp1', (d.bp1&&d.bp1.metricas)||[], comp&&comp.comparacao_rpe_bp1],
  ['bp2', (d.bp2&&d.bp2.metricas)||[], comp&&comp.comparacao_rpe_bp2]]
 .forEach(function(t){
  const grupo=t[0], metricas=t[1], compRpe=t[2];
  const valores = compRpe && compRpe.dia2 ? compRpe.dia2.valores : null;
  if(!valores) return;
  metricas.forEach(function(iv,i){
   const p=iv.potencia;
   if(p && p.ok && valores[i]!=null) pontos.push({pot:p.media, rpe:valores[i], grupo:grupo, numero:i+1});
  });
 });
 if(!pontos.length){ noData(g,W,H,'RPE — DADOS INSUFICIENTES'); return; }

 const PL=30, PR=12, PT=10, PB=24;
 const w=W-PL-PR, h=H-PT-PB;
 const xs=pontos.map(p=>p.pot);
 const xa=Math.min.apply(null,xs)*0.95, xb=Math.max.apply(null,xs)*1.05;
 const X=v=>PL+(v-xa)/((xb-xa)||1)*w;
 const Y=v=>PT+h-(v-1)/9*h;  // RPE 1-10, escala fixa, valor real

 g.clearRect(0,0,W,H);
 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='9px sans-serif';
 g.textAlign='right';
 [1,4,7,10].forEach(function(v){ g.fillText(v, PL-4, Y(v)+3); });
 g.textAlign='center';
 xs.forEach(function(xv){ g.fillText(Math.round(xv), X(xv), PT+h+12); });

 const rects=[];
 pontos.forEach(function(p){
  const x=X(p.pot), y=Y(p.rpe);
  g.fillStyle=cores[p.grupo];
  g.beginPath(); g.arc(x,y,4,0,7); g.fill();
  rects.push({x0:x-6,x1:x+6,pot:p.pot,rpe:p.rpe,grupo:p.grupo,numero:p.numero});
 });
 MX_HOVER.chMxVstRPE = {rects:rects};
}

function mxLigarHoverVstFisiologico(){
 MX_VST_CANAIS.forEach(function(c){
  const cv=document.getElementById(c.canvas), tip=document.getElementById(c.tip);
  if(!cv||!tip) return;
  cv.addEventListener('mousemove', function(ev){
   const info=MX_HOVER[c.canvas];
   if(!info||!info.rects.length){ tip.style.display='none'; return; }
   const r=cv.getBoundingClientRect();
   const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
   const mx=(ev.clientX-r.left)*esc;
   const p=info.rects.find(function(rr){ return mx>=rr.x0&&mx<=rr.x1; });
   if(!p){ tip.style.display='none'; return; }
   tip.style.display='block';
   tip.style.left=Math.min(ev.clientX-r.left+12, r.width-170)+'px';
   tip.style.top=Math.max(4, ev.clientY-r.top-38)+'px';
   tip.innerHTML=p.grupo.toUpperCase()+' #'+p.numero+'<br>Power: '+Math.round(p.pot)+' W<br>'
     +p.inicial+' → '+p.final+p.unidade
     +(p.delta_pct!=null?'<br>Δ: '+(p.delta_pct>=0?'+':'')+p.delta_pct+'%':'');
  });
  cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
 });
 // RPE tem forma diferente (um valor por WORK, nao inicial/final) --
 // hover proprio, mesma mecanica
 const cvR=document.getElementById('chMxVstRPE'), tipR=document.getElementById('mxTipVstRPE');
 if(cvR && tipR){
  cvR.addEventListener('mousemove', function(ev){
   const info=MX_HOVER.chMxVstRPE;
   if(!info||!info.rects.length){ tipR.style.display='none'; return; }
   const r=cvR.getBoundingClientRect();
   const esc=(cvR.width/r.width)/(window.devicePixelRatio||1);
   const mx=(ev.clientX-r.left)*esc;
   const p=info.rects.find(function(rr){ return mx>=rr.x0&&mx<=rr.x1; });
   if(!p){ tipR.style.display='none'; return; }
   tipR.style.display='block';
   tipR.style.left=Math.min(ev.clientX-r.left+12, r.width-170)+'px';
   tipR.style.top=Math.max(4, ev.clientY-r.top-38)+'px';
   tipR.innerHTML=p.grupo.toUpperCase()+' #'+p.numero+'<br>Power: '+Math.round(p.pot)+' W<br>RPE: '+p.rpe;
  });
  cvR.addEventListener('mouseleave', function(){ tipR.style.display='none'; });
 }
}

// Heatmap: linhas=metricas, colunas=WORK 1..N, valor=delta_pct ja'
// calculado por metricas_intervalo(). Cor por intensidade do delta.
function _vstCorCelula(frac){
 // mesmo espirito de corCel (tab PMC): amarelo (baixo) -> vermelho (alto)
 const u=Math.max(0,Math.min(1,frac));
 return 'rgb('+Math.round(234+(220-234)*u)+','+Math.round(179+(38-179)*u)+','+
  Math.round(8+(38-8)*u)+')';
}

// Tensor unico para Heatmap e Timing -- MESMA grelha de dados (canais x
// WORKs), so' muda a enfase: heatmap usa so' a cor continua (como
// drawMatriz), timing acrescenta um contorno a celula de maior
// |delta_pct| por linha (onde a mudanca foi maior). RPE entra como mais
// uma linha, em escala propria (1-10), nunca misturada com os delta_pct
// das outras metricas.
function _vstDesenharTensor(canvasId, metricas, rpeValores, destacarMaximo){
 const o=ctx(canvasId,200); if(!o) return;
 const g=o.g,W=o.W,H=o.H;
 if(!metricas || !metricas.length){ noData(g,W,H,'DADOS INSUFICIENTES'); return; }
 const canais=[['hr','HR'],['respiracao','RF'],['smo2','SmO2'],['thb','THb'],['dfa1','DFA1']];
 const nLinhas=canais.length+(rpeValores?1:0);
 const nCols=metricas.length;
 const PL=52,PT=18,PR=10,PB=20;
 const celW=(W-PL-PR)/nCols, celH=(H-PT-PB)/nLinhas;

 g.clearRect(0,0,W,H);
 const linhasVals=canais.map(function(c){
  return metricas.map(iv=>(iv[c[0]]&&iv[c[0]].ok)?iv[c[0]].delta_pct:null);
 });
 let mx=0;
 linhasVals.forEach(l=>l.forEach(v=>{ if(v!=null && Math.abs(v)>mx) mx=Math.abs(v); }));
 mx = mx||1;

 function celula(i, valores, corDeFn, textoDeFn, destacar){
  const validos=valores.map((v,j)=>v!=null?Math.abs(v):null).filter(v=>v!=null);
  const maxIdx = validos.length ? valores.map(v=>v==null?-1:Math.abs(v)).indexOf(Math.max.apply(null,valores.map(v=>v==null?-1:Math.abs(v)))) : -1;
  valores.forEach(function(v,j){
   const x=PL+j*celW, y=PT+i*celH;
   g.fillStyle = v==null ? '#21262d' : corDeFn(v);
   g.fillRect(x,y,celW-1,celH-1);
   if(destacar && j===maxIdx && v!=null){
    g.strokeStyle='#F4D03F'; g.lineWidth=2;
    g.strokeRect(x,y,celW-1,celH-1);
   }
   if(v!=null && celW>28){
    g.fillStyle='#0d1117'; g.font='9px sans-serif'; g.textAlign='center';
    g.fillText(textoDeFn(v), x+celW/2, y+celH/2+3);
   }
  });
 }

 canais.forEach(function(c,i){
  celula(i, linhasVals[i], v=>_vstCorCelula(Math.abs(v)/mx), v=>Math.round(v)+'%', destacarMaximo);
 });
 if(rpeValores){
  const rpeMax=Math.max.apply(null, rpeValores.filter(v=>v!=null).concat([10]));
  celula(canais.length, rpeValores, v=>_vstCorCelula(v/rpeMax), v=>String(v), destacarMaximo);
 }

 g.fillStyle='#8b949e'; g.font='10px sans-serif'; g.textAlign='right';
 canais.forEach((c,i)=>g.fillText(c[1], PL-6, PT+i*celH+celH/2+3));
 if(rpeValores) g.fillText('RPE', PL-6, PT+canais.length*celH+celH/2+3);
 g.textAlign='center';
 metricas.forEach((_,j)=>g.fillText('W'+(j+1), PL+j*celW+celW/2, PT-6));
 g.textAlign='left';

 registarTip(canvasId, function(mxp,myp,rw){
  const esc=rw/W, x=mxp/esc, y=myp/esc;
  const j=Math.floor((x-PL)/celW), i=Math.floor((y-PT)/celH);
  if(j<0||j>=nCols||i<0||i>=nLinhas) return '';
  const nome = i<canais.length ? canais[i][1] : 'RPE';
  const v = i<canais.length ? linhasVals[i][j] : (rpeValores?rpeValores[j]:null);
  if(v==null) return '';
  return '<div class="th">'+nome+' · W'+(j+1)+'</div>'
   +'<div class="tr"><span>'+(i<canais.length?'Δ%':'RPE')+'</span><b>'+
   (i<canais.length?(v>=0?'+':'')+v.toFixed(1)+'%':v)+'</b></div>';
 });
}

function mxDesenharVstHeatmap(d){
 const comp = MX_VST_ULT_COMP;
 const rpe1 = comp && comp.comparacao_rpe_bp1 && comp.comparacao_rpe_bp1.dia2 ? comp.comparacao_rpe_bp1.dia2.valores : null;
 const rpe2 = comp && comp.comparacao_rpe_bp2 && comp.comparacao_rpe_bp2.dia2 ? comp.comparacao_rpe_bp2.dia2.valores : null;
 _vstDesenharTensor('chVstHeatBP1', (d.bp1&&d.bp1.metricas)||[], rpe1, false);
 _vstDesenharTensor('chVstHeatBP2', (d.bp2&&d.bp2.metricas)||[], rpe2, false);
}

function mxDesenharVstTiming(d){
 const comp = MX_VST_ULT_COMP;
 const rpe1 = comp && comp.comparacao_rpe_bp1 && comp.comparacao_rpe_bp1.dia2 ? comp.comparacao_rpe_bp1.dia2.valores : null;
 const rpe2 = comp && comp.comparacao_rpe_bp2 && comp.comparacao_rpe_bp2.dia2 ? comp.comparacao_rpe_bp2.dia2.valores : null;
 _vstDesenharTensor('chVstTimingBP1', (d.bp1&&d.bp1.metricas)||[], rpe1, true);
 _vstDesenharTensor('chVstTimingBP2', (d.bp2&&d.bp2.metricas)||[], rpe2, true);
}

// Recovery x tempo: cada recovery mostrado como dois pontos (inicial em
// t=0, final em t=duracao_s), com a janela 0-60s sombreada -- e' a
// resolucao que os dados ja calculados permitem (metricas_recuperacao
// nao guarda a serie completa segundo a segundo, so' inicial/final).
function mxDesenharVstRecoveryTempo(compData){
 const o = ctx('chMxVstRecoveryTempo', 180); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 if(!compData){ noData(g,W,H,'Sincroniza uma sessão VST primeiro'); return; }
 const sel=document.getElementById('mxVstRecMetrica');
 const canal = sel ? sel.value : 'hr';
 const unidadeMap={hr:'bpm',respiracao:'',smo2:'%',thb:'',dfa1:''};

 const series=[];
 [['bp1',compData.comparacao_recovery_bp1,'#5DADE2'],
  ['bp2',compData.comparacao_recovery_bp2,'#F0883E']].forEach(function(par){
  const bloco=par[0], comp=par[1], cor=par[2];
  const m = comp && comp.metricas && comp.metricas[canal];
  if(!m) return;
  (m.dia2_recuperacoes_individuais||[]).forEach(function(c,i){
   if(!c) return;
   series.push({bloco:bloco, rec:i+1, inicial:c.inicial, final:c.final, cor:cor});
  });
 });
 if(!series.length){ noData(g,W,H,'DADOS INSUFICIENTES para '+canal); return; }

 const PL=42, PR=12, PT=14, PB=24;
 const w=W-PL-PR, h=H-PT-PB;
 const durMax=60*3;  // assume-se ate 3min para desenhar; ajusta-se se precisar
 const ys=series.map(s=>s.inicial).concat(series.map(s=>s.final));
 const ya=Math.min.apply(null,ys)*0.95, yb=Math.max.apply(null,ys)*1.05;
 const X=t=>PL+Math.min(t,durMax)/durMax*w;
 const Y=v=>PT+h-(v-ya)/((yb-ya)||1)*h;

 g.clearRect(0,0,W,H);
 // janela 0-60s sombreada -- a janela de comparacao directa Dia1xDia2
 g.fillStyle='#F4D03F'; g.globalAlpha=0.1;
 g.fillRect(X(0),PT,X(60)-X(0),h);
 g.globalAlpha=1;
 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='center';
 [0,60,120,180].forEach(function(tv){ g.fillText(tv+'s', X(tv), PT+h+12); });

 series.forEach(function(s){
  const x0=X(0), x1=X(60);
  g.strokeStyle=s.cor; g.lineWidth=1.5;
  g.beginPath(); g.moveTo(x0,Y(s.inicial)); g.lineTo(x1,Y(s.final)); g.stroke();
  g.fillStyle=s.cor;
  g.beginPath(); g.arc(x0,Y(s.inicial),3,0,7); g.fill();
  g.beginPath(); g.arc(x1,Y(s.final),3,0,7); g.fill();
  g.lineWidth=1;
 });
 g.textAlign='left'; g.font='9px sans-serif'; g.fillStyle='#8b949e';
 g.fillText('janela de comparação directa (0–60s)', PL+4, PT+10);
}

// ═══════════════════════════════════════════════════════════════════
// Integração VST na tab Limiares -- so' LE' o snapshot ja' gravado por
// /api/moxy/vst/comparar (via /verificacao_ativa), nunca recalcula
// BP1/BP2 nem reanalisa nada. "VERIFICADO" segue o status ja' produzido
// pela Verificacao, nunca "confirmado".
// ═══════════════════════════════════════════════════════════════════

function _vstSimboloVerificacao(status){
 if(!status) return {simbolo:'?', texto:'DADOS INSUFICIENTES', cor:'#8b949e'};
 if(status==='CONSISTENTE') return {simbolo:'✓', texto:'VERIFICADO', cor:'#3FB950'};
 if(status==='PARCIALMENTE CONSISTENTE') return {simbolo:'~', texto:'PARCIALMENTE VERIFICADO', cor:'#F4D03F'};
 if(status==='DADOS INSUFICIENTES') return {simbolo:'?', texto:'DADOS INSUFICIENTES', cor:'#8b949e'};
 return {simbolo:'✗', texto:'NÃO VERIFICADO', cor:'#E74C3C'};
}

let MX_VST_VERIF_CACHE = {};  // {moxyId: resposta de /verificacao_ativa} -- evita repetir o pedido a cada mxDraw()
let MX_HOVER_BP_FAIXAS = [];  // faixas de BP1/BP2 desenhadas no grafico principal, para hover

function mxPrincipalVerificacaoMostrar(){
 const ids=Object.keys(MX_DADOS||{});
 const linhaBP1=document.getElementById('mxCartaoBP1Linha'), linhaBP2=document.getElementById('mxCartaoBP2Linha');
 if(!ids.length || !linhaBP1 || !linhaBP2) return;
 const moxyId=ids[0];

 function pintar(d){
  if(!d || d.status!=='ok' || !d.sincronizado || !d.analisado) return;  // mantem a linha original (ponto), como ja' esta
  function pinta(linha, mxbpVal, cor, nome, vst){
   if(!vst || !vst.status) return;
   const s=_vstSimboloVerificacao(vst.status);
   // so' troca a linha por um RANGE se for CONSISTENTE ou PARCIALMENTE
   // CONSISTENTE e houver range_verificado -- caso contrario mantem a
   // linha original (ponto), ja renderizada por mxDraw
   if((vst.status==='CONSISTENTE' || vst.status==='PARCIALMENTE CONSISTENTE') && vst.range_verificado){
    const rv=vst.range_verificado;
    linha.innerHTML='<b style="color:'+cor+';">'+nome+'</b> '+Math.round(rv[0])+'–'+Math.round(rv[1])+'W'
      +' <span style="font-size:11px;color:'+s.cor+';">'+s.simbolo+' VST'+(s.texto==='PARCIALMENTE VERIFICADO'?' verificado (parcial)':' verificado')+'</span>';
   } else if(mxbpVal!=null){
    // sem range utilizavel: mantem o ponto, so' acrescenta o selo de status
    linha.innerHTML='<b style="color:'+cor+';">'+nome+'</b> '+Math.round(mxbpVal)+'W'
      +' <span style="font-size:11px;color:'+s.cor+';">'+s.simbolo+' VST'
      +(s.texto==='NÃO VERIFICADO'?' não verificado':s.texto==='DADOS INSUFICIENTES'?' insuficiente':'')+'</span>';
   }
  }
  pinta(linhaBP1, MX_BP&&MX_BP.bp1, '#3FB950', 'BP1', d.bp1);
  pinta(linhaBP2, MX_BP&&MX_BP.bp2, '#F85149', 'BP2', d.bp2);
 }

 if(MX_VST_VERIF_CACHE[moxyId] !== undefined){ pintar(MX_VST_VERIF_CACHE[moxyId]); return; }
 fetch('/api/moxy/vst/verificacao_ativa/'+moxyId).then(r=>r.json()).then(function(d){
  MX_VST_VERIF_CACHE[moxyId]=d; pintar(d);
  mxDraw();  // a faixa de BP1/BP2 no grafico so' aparece depois da cache existir
 }).catch(function(){ MX_VST_VERIF_CACHE[moxyId]=null; });
}

function mxLimVerificacaoMostrar(moxyId, lc, valores){
 const box=document.getElementById('mxLimVstBox');
 if(!box || !moxyId) return;
 box.innerHTML='';
 fetch('/api/moxy/vst/verificacao_ativa/'+moxyId).then(r=>r.json()).then(function(d){
  if(d.status!=='ok' || !d.sincronizado){
   box.innerHTML='<p class="sub" style="font-size:10px;color:#8b949e;">Nenhuma verificação VST associada a esta sessão.</p>';
   return;
  }
  if(!d.analisado){
   box.innerHTML='<p class="sub" style="font-size:10px;color:#8b949e;">'+(d.mensagem||'Conjunto sincronizado, ainda sem análise')+'</p>';
   return;
  }
  const p1=(lc&&lc.primeiro)||{}, p2=(lc&&lc.segundo)||{};
  function linha(nome, consenso, vst){
   const s=_vstSimboloVerificacao(vst.status);
   const rangeMetodos = consenso.n>1 ? Math.round(consenso.de)+'–'+Math.round(consenso.ate)+' W' :
    (consenso.mediana!=null ? Math.round(consenso.mediana)+' W' : '—');
   const rangeVst = vst.range_verificado ? Math.round(vst.range_verificado[0])+'–'+Math.round(vst.range_verificado[1])+' W' : '—';
   return '<div style="border:1px solid '+s.cor+';border-radius:6px;padding:6px 10px;margin-bottom:8px;">'
    +'<b style="font-size:12px;">'+nome+'</b><br>'
    +'<span style="font-size:11px;color:#8b949e;">Range dos métodos: '+rangeMetodos+'</span><br>'
    +'<span style="font-size:11px;color:'+s.cor+';">'+s.simbolo+' '+s.texto
    +(vst.range_verificado?' — '+rangeVst:'')+'</span>'
    +'<div style="font-size:9px;color:#8b949e;margin-top:2px;">Status Dia 1 × Dia 2: '+(vst.status||'—')+'</div>'
    +'</div>';
  }
  box.innerHTML = '<div style="font-size:11px;color:#8b949e;margin-bottom:4px;">Verificação VST</div>'
   + linha('BP1', p1, d.bp1||{}) + linha('BP2', p2, d.bp2||{});
 }).catch(function(){ box.innerHTML=''; });
}

function mxLimiares(){
 const ids=Object.keys(MX_DADOS);
 const est=document.getElementById('mxLimEstado');
 const box=document.getElementById('mxLimiares');
 if(!ids.length){ est.textContent='escolhe uma sessão'; return; }
 const id=ids[0], c=mxCorteDe(id);
 est.textContent='a calcular...';
 fetch('/api/moxy/limiares/'+id+'?inicio='+Math.round(c[0])
       +'&fim='+Math.round(c[1])
       +(document.getElementById('mxExaustao').value
         ? '&exaustao='+document.getElementById('mxExaustao').value : ''))
 .then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ est.textContent=d.mensagem||'sem dados';
   box.innerHTML=''; MX_BP=null; mxDraw(); return; }
  const bp=d.breakpoints||{}, f=bp.fiabilidade||{};
  // declarado AQUI, antes do primeiro uso: estava mais abaixo e o MX_BP
  // lia-o na zona morta temporal, ficando sempre nulo -- era por isso que
  // o BP2 nao aparecia no grafico
  const lc=d.limiares_consenso||{};
  MX_ULT_LIMIARES_D = d;
  mxDesenharLimiaresSmo2(d);
  mxDesenharDmax(d);
  mxMostrarDfa1(d.dfa1);
  mxDesenharDfa1(d.dfa1);
  mxMostrarCartoesSimples(d);
  // Valores encontrados NESTE teste, para aparecerem dentro do dropdown
  // de intervenções em vez de só watts/paces genéricos. Sem isto, a
  // sugestão de treino não dizia a que carga a limitação apareceu.
  const p1=lc.primeiro||{}, p2=lc.segundo||{};
  // Escolher a estimativa mais PRÓXIMA da mediana, não a primeira com
  // bpm. ".find(e=>e.bpm)" pegava na primeira do array, que podia ser
  // de um método com watts bem diferentes da mediana escolhida.
  function _bpmDaMediana(consenso){
   const es=(consenso.estimativas||[]).filter(e=>e.bpm!=null);
   if(!es.length || consenso.mediana==null) return null;
   return es.reduce(function(a,b){
     return Math.abs(a.watts-consenso.mediana)<=Math.abs(b.watts-consenso.mediana)?a:b;
   }).bpm;
  }
  MX_ULT_VALORES = {
   bp1_w: p1.mediana, bp1_bpm: _bpmDaMediana(p1),
   bp2_w: p2.mediana, bp2_bpm: _bpmDaMediana(p2),
   smo2_min: Math.min.apply(null,
     (d.blocos_usados||[]).map(b=>b.smo2_min).filter(v=>v!=null)) || null,
   fc_max_teste: null,
   modalidade: d.modalidade,
   limitador_fisiologico: d.limitador_fisiologico,
   vo2max_previsto: d.vo2max_previsto,
   smo2_derivadas: d.smo2_derivadas,
  };
  mxCarregarPlanoZonas(d, id, MX_ULT_VALORES);
  MX_ULT_HIPO = MX_ULT_HIPO || false;
  mxDerivadasEVo2(d);
  mxEstilosRecentes();
  MX_RPE_EDITAR=false;
  mxRpe(d.activity_id);
  est.textContent=(d.modalidade||'')+' · '+(f.n_degraus||0)+' degraus';
  // No grafico vai o resultado do SCRIPT do Intervals.icu, para bater
  // certo com o que ves la'. As tabelas continuam a mostrar todos os
  // metodos, e os cartoes o consenso -- o grafico e' so' a referencia
  // comum entre as duas ferramentas.
  const bl0=d.bp_moxy_sem_restricao||{};
  const c1=(lc.primeiro||{}), c2=(lc.segundo||{});
  // MX_BP tem de ficar atribuído ANTES do mxDraw() mais abaixo (dentro do
  // bloco das reservas). Estava DEPOIS, e por isso a primeira vez que o
  // gráfico desenhava — logo a seguir a carregar os limiares — fazia-o
  // sem os marcadores de BP1/BP2, porque MX_BP ainda tinha o valor
  // anterior (ou null). Só apareciam depois de qualquer outra interacção
  // que chamasse mxDraw() de novo.
  MX_BP = (bl0.bp1_w!=null || bl0.bp2_w!=null) ? {
    bp1: bl0.bp1_w, bp2: bl0.bp2_w,
    bp1_bpm: bl0.bp1_bpm, bp2_bpm: bl0.bp2_bpm,
    bp1_disp: null, bp2_disp: null,
    fonte: 'script Intervals.icu', fiavel: true
  } : ((c1.ok || c2.ok) ? {
    bp1: c1.ok ? c1.mediana : null,
    bp2: c2.ok ? c2.mediana : null,
    bp1_bpm: c1.ok ? (c1.estimativas.find(x=>x.bpm)||{}).bpm : null,
    bp2_bpm: c2.ok ? (c2.estimativas.find(x=>x.bpm)||{}).bpm : null,
    bp1_disp: c1.dispersao_pct, bp2_disp: c2.dispersao_pct,
    fonte: 'mediana dos métodos', fiavel: !(lc.avisos||[]).length
  } : null);
  MX_RESERVAS = d.reservas || {};
  MX_ULT_PERFIL=(d.perfil_resposta||{}).perfil||null;
  MX_ULT_HIPO=!!((d.hipocapnia||{}).suspeita);
  if(typeof mxSintese==='function') mxSintese();
  // Injectar as reservas como CANAIS, para passarem pela máquina que já
  // existe: aparecem nas caixas de métricas, no gráfico e no hover, sem
  // código de desenho novo. Estavam a ser guardadas e nunca desenhadas.
  const dd0 = MX_DADOS[id];
  if(dd0){
   // Ajustar ao comprimento do tempo antes de entrar. As séries são
   // desenhadas por índice contra a grelha de tempo; um comprimento
   // diferente desloca a curva progressivamente ao longo da sessão.
   const nT = (dd0.tempo||[]).length;
   function ajustarN(v, n){
    if(!n || v.length===n) return v.slice();
    const out=[];
    for(let i=0;i<n;i++){
     const p=(v.length-1)*i/(n-1);
     const a=Math.floor(p), b=Math.min(v.length-1, a+1), f=p-a;
     const va=v[a], vb=v[b];
     out.push((typeof va==='number' && typeof vb==='number')
              ? va+(vb-va)*f : (typeof va==='number'?va:null));
    }
    return out;
   }
   ['wprime','mprime'].forEach(function(k){
    const r=(d.reservas||{})[k];
    delete dd0.canais[k];
    if(dd0.canais_contexto)
     dd0.canais_contexto = dd0.canais_contexto.filter(x=>x!==k);
    if(r && r.ok && (r.serie||[]).length){
     dd0.canais[k] = ajustarN(r.serie, nT);
     dd0.canais_contexto = (dd0.canais_contexto||[]).concat([k]);
     // ligar por omissão: se foi calculado, é para ser visto
     if(MX_ON[k] === undefined) MX_ON[k] = true;
    }
   });
   mxCanaisEdit();
   mxDraw();
  }

  let h='';
  if(d.fc_valida===false)
   h+='<p style="border-left:3px solid #F85149;padding-left:8px;'
    +'font-size:11px;margin:0 0 8px 0;">'
    +'<b style="color:#F85149;">FC descartada nesta sessão</b> — '
    +(d.aviso_fc||'')
    +(d.canais_invalidos&&d.canais_invalidos.length
      ? '<br><span style="color:#8b949e;">apagados: '
        +d.canais_invalidos.join(', ')+'</span>' : '')
    +'</p>';

  // ── que protocolo foi este ────────────────────────────────────────
  const ts=d.tipo_sessao||{};
  if(ts.ok){
   const serve=ts.serve_para_breakpoints;
   h+='<div style="border-left:3px solid '+(serve?'#3FB950':'#F0883E')
    +';padding:6px 10px;margin-bottom:10px;">'
    +'<b style="color:'+(serve?'#3FB950':'#F0883E')+';font-size:14px;">'
    +ts.tipo+'</b> <span style="color:#8b949e;font-size:11px;">'
    +ts.n_blocos_trabalho+' blocos de trabalho'
    +(ts.blocos_fora_do_corte?' · '+ts.blocos_fora_do_corte
      +' fora do corte (aquecimento)':'')+'</span>'
    +'<br><span style="font-size:11px;">'+ts.descricao+'</span>'
    +'<br><span style="font-size:11px;color:'+(serve?'#8b949e':'#F0883E')+';">'
    +(serve?'✓ ':'⚠ ')+ts.porque+'</span>';
   const tr=ts.trabalho||{}, rc=ts.recuperacao||{};
   h+='<br><span style="font-size:10px;color:#8b949e;">trabalho '
    +tr.duracao_min_s+'–'+tr.duracao_max_s+' s (cv '+tr.cv+')'
    +(rc.duracao_media_s!=null
      ? ' · recuperação '+rc.duracao_min_s+'–'+rc.duracao_max_s+' s (cv '
        +rc.cv+')' : '')
    +((ts.carga||{}).subida_pct!=null
      ? ' · carga sobe '+ts.carga.subida_pct+'%' : '')
    +((ts.distancia||{}).media_m
      ? ' · ~'+ts.distancia.media_m+' m por repetição' : '')
    +'</span></div>';
  }

  // ── coerencia com o resto do perfil ───────────────────────────────
  const co=d.coerencia||{};
  if(co.motivo && !co.ok){
   h+='<p style="color:#8b949e;font-size:11px;">'+co.motivo+'</p>';
  } else if(co.ok){
   const bom=!(co.avisos||[]).length;
   h+='<div style="border:1px solid '+(bom?'#3FB950':'#F0883E')
    +';border-radius:6px;padding:8px 10px;margin-bottom:10px;">'
    +'<b style="color:'+(bom?'#3FB950':'#F0883E')+';">Coerência: '
    +co.veredicto+'</b> <span style="color:#8b949e;font-size:11px;">'
    +co.n_passou+' de '+co.n_testes+' testes</span>';
   if(Object.keys(co.zona_de||{}).length)
    h+='<br><span style="font-size:11px;">'
     +Object.keys(co.zona_de).map(function(k){
       const z=co.zona_de[k];
       return '<b>'+k+'</b> cai em <b>'+z.zona+'</b> ('+z.de_w+'–'+z.ate_w
        +' W)'; }).join(' · ')+'</span>';
   h+='<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
    +(co.testes||[]).map(function(t){
      return '<tr><td style="padding-right:8px;color:'
       +(t.ok?'#3FB950':'#F0883E')+';">'+(t.ok?'✓':'⚠')+'</td>'
       +'<td style="color:'+(t.ok?'#8b949e':'#c9d1d9')+';">'+t.detalhe
       +'</td></tr>'; }).join('')
    +'</table>'
    +'<span style="font-size:10px;color:#8b949e;">'+(co.nota||'')+'</span>'
    +'</div>';
  }

  // ── dois cartoes: um por limiar ───────────────────────────────────
  // Antes era um painel por metodo -- quatro numeros soltos sem dizer qual
  // respondia a que pergunta. Agora cada limiar tem um cartao com todas as
  // suas estimativas, e o detalhe de cada metodo fica recolhido.
  [['primeiro','#A371F7'],['segundo','#3FB950']].forEach(function(par){
   const r=lc[par[0]];
   if(!r || !r.ok) return;
   const disp=r.dispersao_pct||0;
   const cor = disp<10 ? par[1] : disp<20 ? '#F0883E' : '#F85149';
   h+='<div style="border:1px solid '+cor+';border-radius:6px;'
    +'padding:8px 10px;margin-bottom:10px;">'
    +'<span style="color:#8b949e;font-size:11px;">'+r.nome+'</span><br>'
    +'<b style="font-size:18px;color:'+cor+';">'+Math.round(r.mediana)
    +' W</b> <span style="color:#8b949e;font-size:12px;">'
    +(r.n>1 ? '('+Math.round(r.de)+'–'+Math.round(r.ate)+' W · '
      +r.n+' métodos · dispersão '+disp+'%)' : '(1 método)')
    +(r.n_marcadas ? ' <span style="color:#F0883E;">· '+r.n_marcadas
      +' fora por implausível</span>' : '')+'</span>'
    +'<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
    +r.estimativas.map(function(x){
      // implausíveis ficam VISÍVEIS com asterisco: apagar esconderia que
      // houve medição; o asterisco diz que houve e que não é de confiança
      const mau = x.plausivel===false;
      return '<tr'+(mau?' style="opacity:.55;"':'')+'>'
       +'<td style="padding-right:14px;'+(mau?'color:#F0883E;':'')+'"><b>'
       +Math.round(x.watts)+' W'+(mau?' *':'')+'</b></td>'
       +'<td style="padding-right:14px;color:#8b949e;"'
       +(x.fc_origem?' title="'+x.fc_origem+'"':'')+'>'
       +(x.bpm?x.bpm+' bpm':'—')+'</td>'
       +'<td style="padding-right:14px;">'+x.metodo+'</td>'
       +'<td style="color:#6e7681;">'
       +(mau ? '<span style="color:#F0883E;">'+x.motivo_implausivel+'</span>'
             : x.rota)+'</td></tr>'; }).join('')
    +'</table></div>';
  });
  (lc.avisos||[]).forEach(function(a){
   h+='<p style="color:#F0883E;font-size:11px;margin:-4px 0 8px 0;">⚠ '+a
    +'</p>'; });
  if(lc.nota) h+='<p style="color:#8b949e;font-size:11px;">'+lc.nota+'</p>';

  h+='<div id="mxLimVstBox" style="margin:4px 0 10px;"></div>';

  // tudo o resto vai para dentro de um dropdown
  h+='<details style="margin-top:10px;"><summary style="cursor:pointer;'
   +'font-size:12px;color:#8b949e;padding:4px 0;">Detalhe de cada método'
   +'</summary><div style="margin-top:6px;">';
  const fecharDetalhe=true;

  const pf=d.perfil_resposta||{};
  if(pf.ok){
   const par = pf.perfil==='parabólico';
   h+='<div style="border-left:3px solid '+(par?'#A371F7':'#79C0FF')
    +';padding:6px 10px;margin-bottom:10px;">'
    +'<b style="font-size:15px;color:'+(par?'#A371F7':'#79C0FF')+';">'
    +'Perfil '+pf.perfil+'</b> '
    +'<span style="color:#8b949e;font-size:11px;">SmO2 de '+pf.smo2min
    +'% a '+pf.smo2max+'% · amplitude '+pf.amplitude+'</span>'
    +'<br><span style="font-size:11px;color:#8b949e;">'+pf.metodo+'</span>';
   if(pf.bp1_watts!=null)
    h+='<br><b style="color:#A371F7;">BP1 '+pf.bp1_watts+' W</b> '
     +'<span style="color:#8b949e;font-size:11px;">≈ FatMax / LT1 · o '
     +'PRIMEIRO limiar</span>';
   h+='<br><span style="font-size:11px;">'+pf.bp1_leitura+'</span>'
    +'<br><span style="font-size:10px;color:#8b949e;">'+pf.fenotipo+'</span>'
    +'<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
    +'<tr style="color:#8b949e;text-align:left;">'
    +'<th style="padding-right:12px;">Carga</th>'
    +'<th>SmO2 no último minuto</th></tr>'
    +(pf.degraus||[]).map(function(x){
      const top = x.watts===pf.smo2max_watts;
      return '<tr><td style="padding-right:12px;">'+x.watts+' W</td>'
       +'<td style="color:'+(top?'#A371F7':'#c9d1d9')+';">'+x.smo2_fim+'%'
       +(top?' ← máximo':'')+'</td></tr>'; }).join('')
    +'</table></div>';
  } else if(pf.motivo){
   h+='<p style="color:#8b949e;font-size:11px;">Perfil: '+pf.motivo+'</p>';
  }
  // ── primeiro limiar pela reoxigenação ─────────────────────────────
  const lr=d.lt1_reoxigenacao||{};
  if(lr.ok){
   h+='<div style="border-left:3px solid #A371F7;padding:6px 10px;'
    +'margin-bottom:10px;">'
    +'<b style="font-size:16px;color:#A371F7;">LT1 '+lr.lt1_estimado+' W</b>'
    +' <span style="color:#8b949e;font-size:11px;">entre '
    +lr.lt1_entre.join(' e ')+' W · ±'+lr.incerteza+' · '+lr.metodo+'</span>'
    +'<br><span style="font-size:11px;">'+lr.leitura+'</span>'
    +'<br><span style="font-size:10px;color:#3FB950;">'+lr.independente
    +'</span>'
    +'<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
    +'<tr style="color:#8b949e;text-align:left;">'
    +'<th style="padding-right:12px;">Carga</th>'
    +'<th style="padding-right:12px;">2.ª metade</th>'
    +'<th style="padding-right:12px;">Padrão</th><th>Domínio</th></tr>'
    +(lr.blocos||[]).map(function(x){
      if(!x.padrao) return '';
      const c2 = x.padrao==='reoxigena' ? '#3FB950'
               : x.padrao==='estabiliza' ? '#F0883E' : '#F85149';
      return '<tr><td style="padding-right:12px;">'+Math.round(x.watts)
       +' W</td><td style="padding-right:12px;color:#8b949e;">'
       +x.declive_2a_metade+'</td>'
       +'<td style="padding-right:12px;color:'+c2+';">'+x.padrao+'</td>'
       +'<td style="color:#8b949e;">'+x.dominio+'</td></tr>'; }).join('')
    +'</table>'
    +'<span style="font-size:10px;color:#8b949e;">'+lr.nota+'</span>'
    +'</div>';
  } else if(lr.motivo){
   h+='<p style="color:#8b949e;font-size:11px;">LT1 por reoxigenação: '
    +lr.motivo+'</p>';
  }

  const ml=d.mlss_dessaturacao||{};
  if(ml.ok){
   h+='<div style="border-left:3px solid #3FB950;padding:6px 10px;'
    +'margin-bottom:10px;">'
    +'<b style="font-size:16px;color:#3FB950;">MLSS '+ml.mlss_estimado
    +' W</b> <span style="color:#8b949e;font-size:11px;">≈ VT2 / RCP / LT2 · '
    +'o SEGUNDO limiar</span> <span style="color:#8b949e;font-size:11px;">entre '
    +ml.mlss_entre.join(' e ')+' W · ±'+ml.incerteza+'</span>'
    +'<br><span style="font-size:11px;color:#8b949e;">'+ml.metodo+'</span>'
    +'<br><span style="font-size:11px;color:#8b949e;">'+ml.nota+'</span>'
    +(ml.aviso_sequencia
      ? '<br><span style="font-size:11px;color:#F0883E;">⚠ '
        +ml.aviso_sequencia+'</span>' : '')
    +'<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
    +'<tr style="color:#8b949e;text-align:left;">'
    +'<th style="padding-right:12px;">Carga</th>'
    +'<th style="padding-right:12px;">Declive total</th>'
    +'<th style="padding-right:12px;">2.ª metade</th><th>Padrão</th></tr>'
    +(ml.blocos||[]).map(function(x){
      if(!x.padrao) return '<tr><td style="padding-right:12px;">'
       +(x.watts!=null?Math.round(x.watts)+' W':'—')+'</td>'
       +'<td colspan="3" style="color:#6e7681;">'+(x.motivo||'')+'</td></tr>';
      const c2=x.acima_do_mlss?'#F85149':'#3FB950';
      return '<tr'+(x.contradiz?' style="background:rgba(240,136,62,0.08);"':'')
       +'><td style="padding-right:12px;">'+Math.round(x.watts)
       +' W</td><td style="padding-right:12px;color:#8b949e;">'
       +x.declive_total+'</td><td style="padding-right:12px;color:#8b949e;">'
       +x.declive_2a_metade+'</td><td style="color:'+c2+';">'+x.padrao
       +(x.contradiz?' <span style="color:#F0883E;font-size:10px;">⚠ '
         +x.contradiz+'</span>':'')
       +'</td></tr>';
     }).join('')
    +'</table><span style="font-size:10px;color:#8b949e;">declives em % de '
    +'SmO2 por minuto, ignorando os primeiros '
    +(ml.criterio||{}).transiente_ignorado_pct+'% do bloco (transiente de '
    +'arranque). Estável = |declive| abaixo de '
    +(ml.criterio||{}).estavel_abaixo_de+'</span></div>';
  } else if(ml.motivo){
   h+='<p style="color:#F0883E;font-size:12px;">MLSS: '+ml.motivo+'</p>';
  }
  if(ml.aviso_duracao)
   h+='<p style="color:#F0883E;font-size:11px;margin:-4px 0 8px 0;">⚠ '
    +ml.aviso_duracao+'</p>';

  // ── reservas W′ e M′ ──────────────────────────────────────────────
  const rv=d.reservas||{};
  ['wprime','mprime'].forEach(function(k){
   const r=rv[k];
   if(!r) return;
   const nome = k==='wprime' ? 'W′ (potência)' : 'M′ (SmO2)';
   if(!r.ok){
    h+='<p style="font-size:11px;color:#8b949e;">'+nome+': '
     +(r.motivo||r.erro||'indisponível')+'</p>';
    if(r.diagnostico_desta_sessao)
     h+='<p style="font-size:11px;color:#F0883E;margin:-4px 0 4px 0;">⚠ '
      +r.diagnostico_desta_sessao+'</p>';
    if(r.o_que_seria_preciso)
     h+='<p style="font-size:11px;color:#8b949e;margin:-2px 0 6px 0;">'
      +'<b>Para o obter:</b> '+r.o_que_seria_preciso+'</p>';
    return;
   }
   const pct=r.minimo_pct;
   const cor = r.vezes_esgotado ? '#F85149' : pct<15 ? '#F0883E' : '#3FB950';
   h+='<div style="border-left:3px solid '+cor+';padding:6px 10px;'
    +'margin-bottom:8px;">'
    +'<b style="color:'+cor+';">'+nome+' — mínimo '+pct+'%</b> '
    +'<span style="color:#8b949e;font-size:11px;">'
    +(r.vezes_esgotado?r.vezes_esgotado+'× a zero · ':'')
    +'aos '+Math.floor((r.instante_do_minimo_s||0)/60)+' min</span>'
    +'<br><span style="font-size:11px;">'+r.leitura+'</span>'
    +'<br><span style="font-size:10px;color:#8b949e;">'+r.modelo
    +(r.nota?' · '+r.nota:'')+(r.limite?' · '+r.limite:'')+'</span></div>';
  });

  const bm=d.bp_moxy||{};
  if(bm.ok || bm.bp1_w!=null){
   const val=bm.ok;
   h+='<div style="border-left:3px solid '+(val?'#3FB950':'#F0883E')
    +';padding:6px 10px;margin-bottom:10px;">'
    +'<b style="font-size:16px;color:'+(val?'#3FB950':'#F0883E')+';">'
    +'BP1 '+bm.bp1_w+' W'+(bm.bp1_bpm?' · '+bm.bp1_bpm+' bpm':'')
    +(bm.bp2_w!=null?'  ·  BP2 '+bm.bp2_w+' W'
      +(bm.bp2_bpm?' · '+bm.bp2_bpm+' bpm':''):'')+'</b>'
    +((bm.bp1_fc_origem||bm.bp2_fc_origem)
      ? '<br><span style="font-size:10px;color:#8b949e;">FC: '
        +[bm.bp1_fc_origem,bm.bp2_fc_origem].filter(Boolean)
          .map(function(o,i2){ return (i2?'BP2':'BP1')+' — '+o.nota; })
          .join(' · ')+'</span>' : '')
    +' <span style="color:#8b949e;font-size:11px;">F='+(bm.f_vs_recta||'—')
    +(bm.p_vs_recta!=null?' p='+bm.p_vs_recta:'')
    +' · '+bm.n_intervalos+' intervalos</span>'
    +'<br><span style="font-size:11px;color:#8b949e;">'+bm.metodo+'</span>'
    +'<br><span style="font-size:10px;color:#8b949e;">'
    +bm.nota_interpolacao+'</span>'
    +(bm.aviso_gl?'<br><span style="font-size:11px;color:#F0883E;">⚠ '
      +bm.aviso_gl+'</span>':'')
    +(!val&&bm.motivo?'<br><span style="font-size:11px;color:#F0883E;">⚠ '
      +bm.motivo+'</span>':'')
    +'<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
    +'<tr style="color:#8b949e;text-align:left;">'
    +'<th style="padding-right:12px;">Carga</th>'
    +'<th style="padding-right:12px;">SmO2 médio</th><th>FC</th></tr>'
    +(bm.pontos||[]).map(function(x){
      return '<tr><td style="padding-right:12px;">'+Math.round(x.watts)
       +' W</td><td style="padding-right:12px;">'+x.smo2.toFixed(1)+'%</td>'
       +'<td style="color:#8b949e;">'+(x.hr!=null?x.hr+' bpm':'—')
       +'</td></tr>'; }).join('')
    +'</table></div>';
  } else if(bm.motivo){
   h+='<p style="color:#8b949e;font-size:11px;">Método Moxy: '+bm.motivo
    +'</p>';
  }
  const bl2=d.bp_moxy_sem_restricao||{};
  if(bl2.bp1_w!=null){
   h+='<p style="font-size:11px;color:#8b949e;margin:-6px 0 10px 0;">'
    +'<b>Sem mínimo por troço</b> (reproduz o script do Intervals.icu): '
    +'BP1 <b>'+bl2.bp1_w+' W</b>'+(bl2.bp1_bpm?' · '+bl2.bp1_bpm+' bpm':'')
    +(bl2.bp2_w!=null?' · BP2 <b>'+bl2.bp2_w+' W</b>'
      +(bl2.bp2_bpm?' · '+bl2.bp2_bpm+' bpm':''):'')
    +'. A diferença para o valor acima é só o critério: eu exijo 2 degraus '
    +'medidos por troço, o script não exige nenhum, e por isso o breakpoint '
    +'dele pode cair entre dois degraus quaisquer. Com poucos degraus a '
    +'diferença chega a 10 W.</p>';
  }

  // ── deoxy-Hb ──────────────────────────────────────────────────────
  const bh=d.bp_hhb||{}, hh=d.hhb||{};
  if(bh.ok || bh.bp1_w!=null){
   h+='<div style="border-left:3px solid #A371F7;padding:6px 10px;'
    +'margin-bottom:10px;">'
    +'<b style="color:#A371F7;font-size:15px;">deoxy-Hb: BP1 '+bh.bp1_w+' W'
    +(bh.bp1_bpm?' · '+bh.bp1_bpm+' bpm':'')
    +(bh.bp2_w!=null?'  ·  BP2 '+bh.bp2_w+' W':'')+'</b>'
    +' <span style="color:#8b949e;font-size:11px;">F='+(bh.f_vs_recta||'—')
    +' · '+bh.n_intervalos+' degraus</span>'
    +(bh.leitura_bp1
      ? '<br><span style="font-size:11px;">'+bh.leitura_bp1+'</span>':'')
    +(hh.formula
      ? '<br><span style="font-size:10px;color:#8b949e;">'+hh.formula
        +' · '+(hh.origem||'')+'</span>':'')
    +(bh.nota_sinal
      ? '<br><span style="font-size:10px;color:#8b949e;">'+bh.nota_sinal
        +'</span>':'')
    +'</div>';
  } else if(bh.motivo){
   h+='<p style="font-size:11px;color:#8b949e;">deoxy-Hb: '+bh.motivo+'</p>';
  }

  const bt=d.bp_taxa||{};
  if(bt.ok){
   h+='<div style="border-left:3px solid #58A6FF;padding:6px 10px;'
    +'margin-bottom:10px;">'
    +'<b style="font-size:16px;color:#58A6FF;">Breakpoint na taxa '
    +bt.bp_watts+' W</b>'
    +' <span style="color:#8b949e;font-size:11px;">≈ VT2 / RCP · F='
    +bt.f_vs_recta+' · '+bt.n_degraus+' degraus · '+(bt.padrao||'')+'</span>'
    +'<br><span style="font-size:11px;color:#8b949e;">'
    +(bt.plateia
      ? 'a queda deixa de se agravar aqui: a extracção chegou ao limite'
      : 'a queda agrava-se aqui')
    +' — declive da taxa '+bt.taxa_antes+' → '+bt.taxa_depois
    +' %/min por watt</span>'
    +'<br><span style="font-size:11px;color:#8b949e;">'+bt.nota+'</span>'
    +(bt.nota_padrao
      ? '<br><span style="font-size:10px;color:#8b949e;">'+bt.nota_padrao
        +'</span>' : '')
    +'<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
    +'<tr style="color:#8b949e;text-align:left;">'
    +'<th style="padding-right:12px;">Carga</th><th>Taxa (%/min)</th>'
    +'<th style="padding-left:12px;">Δ</th></tr>'
    +(bt.degraus||[]).map(function(x, i){
      const acima = bt.bp_watts!=null && x.watts>=bt.bp_watts;
      return '<tr><td style="padding-right:12px;">'+x.watts+' W</td>'
       +'<td style="color:'+(acima?'#F85149':'#3FB950')+';">'+x.taxa
       +'</td><td style="padding-left:12px;color:#6e7681;">'
       +(i>0 ? ((x.taxa-bt.degraus[i-1].taxa)>0?'+':'')
               +(x.taxa-bt.degraus[i-1].taxa).toFixed(2) : '')
       +'</td></tr>'; }).join('')
    +'</table></div>';
  } else if(bt.motivo){
   h+='<p style="color:#8b949e;font-size:11px;">Breakpoint na taxa: '
    +bt.motivo
    +((bt.degraus||[]).length
      ? '<br>taxas por degrau: '+bt.degraus.map(function(x){
          return x.watts+'W '+x.taxa; }).join(' · ') : '')
    +'</p>';
  }
  if(bp.ok){
   const conf = f.usar_para_prescrever===false ? '#F0883E' : '#3FB950';
   h+='<div style="border-left:3px solid '+conf+';padding:6px 10px;">'
    +'<b style="font-size:15px;">BP1 '+bp.bp1.tau+' W'
    +(bp.bp1.smo2!=null?' <span style="color:#8b949e;font-size:11px;">SmO2 '
      +bp.bp1.smo2+'%</span>':'')
    +(bp.bp2&&bp.bp2.ok?' · BP2 '+bp.bp2.tau+' W'
      +(bp.bp2.smo2!=null?' <span style="color:#8b949e;font-size:11px;">SmO2 '
        +bp.bp2.smo2+'%</span>':''):'')+'</b>'
    +'<br><span style="font-size:11px;color:#8b949e;">'+bp.metodo
    +' · declives '+(bp.declives||[]).join(' → ')
    +' · F vs recta '+(bp.f_vs_recta||'—')
    +(bp.p_vs_recta!=null?' (p='+bp.p_vs_recta+')':'')+'</span>'
    +'<br><span style="font-size:11px;color:'+conf+';">Fiabilidade '
    +(f.nivel||'?')+(f.fonte?' · '+f.fonte:'')+'</span>'
    +(f.vies?'<br><span style="font-size:11px;color:#8b949e;">'+f.vies
      +'</span>':'')
    +(f.aviso_n?'<br><span style="font-size:11px;color:#8b949e;">'+f.aviso_n
      +'</span>':'')
    +(f.usar_para_prescrever===false
      ? '<br><span style="font-size:11px;color:#F0883E;">⚠ o cálculo é o '
        +'mesmo de todas as modalidades; o que muda é a confiança. Nesta, '
        +'a literatura desaconselha usar o valor para prescrever zonas '
        +'sem o confirmar contra CP ou MLSS</span>':'')
    +'</div>';
  } else {
   h+='<p style="color:#8b949e;font-size:11px;">Breakpoints por regressão: '
    +(bp.motivo||'sem quebra')
    +' — este método foi desenhado para <b>rampa contínua</b>, onde a queda '
    +'do SmO2 acelera. Num protocolo de blocos com descanso, a informação '
    +'está na forma de cada bloco, não na envolvente dos mínimos. É esperado '
    +'que falhe aqui.</p>';
  }
  const pl=d.plato;
  if(pl&&pl.ok) h+='<p style="font-size:11px;color:#8b949e;">Platô de SmO2 a '
   +pl.x+' s ('+pl.janelas_planas+' janelas de '+pl.janela_s+' s abaixo de '
   +pl.corte+' unidades).</p>';

  const hp=d.hipocapnia||{};
  if(hp.ok){
   const cor=hp.suspeita?'#F85149':'#3FB950';
   h+='<div style="border-left:3px solid '+cor+';padding:6px 10px;'
    +'margin-top:8px;"><b style="color:'+cor+';">Hipocapnia: '
    +(hp.suspeita?'SUSPEITA':'sem sinal')+'</b> '
    +'<span style="color:#8b949e;font-size:11px;">z='+hp.z_maximo
    +' (limiar '+hp.limiar_z+')</span>'
    +'<br><span style="font-size:11px;">'+hp.leitura+'</span>'
    +'<br><span style="font-size:10px;color:#8b949e;">'+hp.limite
    +'</span></div>';
  }
  const ce=d.cer||{};
  if(ce.ok){
   h+='<div style="border-left:3px solid '+(ce.valido?'#3FB950':'#F0883E')
    +';padding:6px 10px;margin-top:8px;">'
    +'<b>CER '+ce.cer_pct_por_s+' %/s</b> '
    +'<span style="color:#8b949e;font-size:11px;">M′='+ce.m_linha
    +' · r²='+ce.r2+' · '+ce.n_ensaios+' ensaios</span>'
    +(ce.aviso?'<br><span style="font-size:11px;color:#F0883E;">⚠ '+ce.aviso
      +'</span>':'')
    +'<br><span style="font-size:10px;color:#8b949e;">'+ce.nota+'</span></div>';
  } else if(ce.motivo){
   h+='<p style="font-size:11px;color:#8b949e;">CER: '+ce.motivo
    +(ce.como_activar
      ? '<br><b style="color:'+(ce.possivel_com_estes_blocos?'#3FB950':'#F0883E')
        +';">'+ce.como_activar+'</b>' : '')
    +((ce.duracoes_distintas||[]).length
      ? '<br><span style="color:#8b949e;">durações dos blocos: '
        +ce.duracoes_distintas.join('s, ')+'s</span>' : '')
    +'</p>';
  }
  if(fecharDetalhe) h+='</div></details>';
  box.innerHTML=h;
  mxDraw();
  mxLimVerificacaoMostrar(id, lc, MX_ULT_VALORES);
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

// O que treinar, conforme o limitador encontrado. Fica em dropdown e
// ligado ao resultado: sem limitador identificado não há intervenção a
// propor -- dar um protocolo de entrega a quem tem limitação de
// utilização não é ineficiente, é inútil.
// Cruza a rede causal, a 5-1-5 e o perfil. Um limitador só de um método
// não chega -- e quando discordam, dizer isso é mais útil do que escolher
// um deles à sorte.
// VO2max previsto e SmO2'/SmO2'' — estavam a ser calculados no backend e
// nunca chegavam a aparecer na tab. Painel pequeno, sem gráfico: o valor
// e o aviso são o que importa aqui, não uma curva ao longo do tempo.
function mxDerivadasEVo2(d){
 const box=document.getElementById('mxDerivadas');
 if(!box) return;
 let h='';
 const vo2=d.vo2max_previsto||{};
 if(vo2.ok){
  const cor = vo2.plausivel===false ? '#F85149' : '#A371F7';
  h+='<p style="font-size:11px;border-left:2px solid '+cor+';'
   +'padding-left:8px;margin:6px 0;">'
   +'<b style="color:'+cor+';'
   +(vo2.plausivel===false?'text-decoration:line-through;':'')+'">'
   +'VO2max previsto: '+vo2.vo2max_estimado+' ml/kg/min</b>'
   +(vo2.plausivel===false?' <span style="color:'+cor+';">(implausível)</span>':'')
   +'<br><span style="color:#8b949e;">'+vo2.formula+'</span>';
  if(vo2.motivo_implausivel)
   h+='<br><span style="color:'+cor+';">'+vo2.motivo_implausivel+'</span>';
  h+='<br><span style="color:#F0883E;">'+vo2.aviso+'</span></p>';
 } else if(vo2.motivo || vo2.erro){
  h+='<p style="font-size:11px;color:#8b949e;">VO2max previsto: '
   +(vo2.motivo||vo2.erro)+'</p>';
 }
 const dv=d.smo2_derivadas||{};
 if(dv.ok){
  h+='<p style="font-size:11px;border-left:2px solid #58A6FF;'
   +'padding-left:8px;margin:6px 0;">'
   +'<b style="color:#58A6FF;">Taxa de variacao do SmO2 agora: </b>'
   +(dv.estado_actual||'\u2014')
   +'<br><span style="color:#8b949e;">(janela de '+dv.janela_s+'s)</span>'
   +'<br><span style="color:#8b949e;font-size:10px;">'+dv.metodo+'</span>'
   +'</p>';
 } else if(dv.motivo||dv.erro){
  h+='<p style="font-size:11px;color:#8b949e;">Taxa de variacao do SmO2: '
   +(dv.motivo||dv.erro)+'</p>';
 }
 box.innerHTML=h;
}

// O que o atleta tem treinado de facto, nos últimos 60 dias, por
// modalidade — para comparar com o que estamos a sugerir em cima. Um
// atleta que só faz contínuo não precisa de ouvir "faz D1 contínuo".
// RPE (1-10) por bloco de trabalho. Se algum bloco ainda não tem RPE,
// mostra a caixa para escrever; se JÁ TODOS têm, mostra só "re-gravar" e
// esconde as caixas até se clicar — para não ficar sempre a mostrar
// inputs de algo que já está gravado.
let MX_RPE_EDITAR = false;

function mxRpe(id){
 const box=document.getElementById('mxRpe');
 if(!box || !id) return;
 fetch('/api/moxy/rpe/'+id).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ box.innerHTML=''; return; }
  if(!(d.blocos||[]).length){
   box.innerHTML='<p class="sub" style="font-size:11px;">sem blocos de '
    +'trabalho identificados nesta sessão para pedir RPE.</p>';
   return;
  }
  MX_RPE_ULTIMOS = d.blocos;
  // a tabela de degraus (mxBlocosTabelaUnica) le MX_RPE_ULTIMOS, mas e'
  // desenhada por outro fluxo, sincrono, que corre ANTES deste fetch
  // terminar. Sem isto, ficava sempre com o RPE da sessao anterior (ou
  // vazio) ate' se clicar "actualizar sessao" e a corrida acontecer
  // por sorte na ordem certa.
  if(typeof mxBlocosTabelaUnica==='function') mxBlocosTabelaUnica(id);
  let h='<div style="border:1px solid #30363d;border-radius:6px;'
   +'padding:8px 10px;">'
   +'<b style="font-size:12px;">RPE por bloco de trabalho</b> '
   +'<span class="sub" style="font-size:10px;">(1 fácil — 10 esforço máximo)</span>';

  if(d.todos_gravados && !MX_RPE_EDITAR){
   h+='<br><span class="sub" style="font-size:11px;">'
    +d.blocos.map(function(b){
      return b.watts_medio+'W → RPE '+b.rpe; }).join(' · ')+'</span>'
    +'<br><button id="mxRpeBtnRegravar" data-id="'+id+'" '
    +'style="margin-top:6px;font-size:11px;padding:3px 10px;'
    +'border-radius:6px;border:1px solid #58A6FF;background:transparent;'
    +'color:#58A6FF;cursor:pointer;">↻ Re-gravar RPE</button>';
   box.innerHTML=h+'</div>';
   const btnR=document.getElementById('mxRpeBtnRegravar');
   if(btnR) btnR.addEventListener('click', function(){
    MX_RPE_EDITAR=true; mxRpe(btnR.getAttribute('data-id'));
   });
   return;
  }

  h+='<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
   +'<tr class="sub" style="text-align:left;">'
   +'<th style="padding-right:12px;">Bloco</th>'
   +'<th style="padding-right:12px;">Watts médio</th>'
   +'<th style="padding-right:12px;">Duração</th><th>RPE</th></tr>'
   +d.blocos.map(function(b,i){
     return '<tr><td style="padding-right:12px;">#'+(i+1)+'</td>'
      +'<td style="padding-right:12px;"><b>'+b.watts_medio+' W</b></td>'
      +'<td style="padding-right:12px;" class="sub">'+b.duracao_s+' s</td>'
      +'<td><input type="number" min="1" max="10" step="1" '
      +'id="mxRpeInput'+i+'" value="'+(b.rpe!=null?b.rpe:'')+'" '
      +'style="width:44px;background:#0d1117;border:1px solid #30363d;'
      +'color:#c9d1d9;border-radius:4px;padding:2px 4px;"></td></tr>';
    }).join('')
   +'</table>'
   +'<button id="mxRpeBtnGravar" data-id="'+id+'" style="margin-top:8px;'
   +'font-size:11px;padding:4px 12px;border-radius:6px;'
   +'border:1px solid #3FB950;background:transparent;color:#3FB950;'
   +'cursor:pointer;">💾 Gravar RPE</button>'
   +' <span id="mxRpeEstado" class="sub" style="font-size:11px;"></span>';
  box.innerHTML=h+'</div>';
  const btnG=document.getElementById('mxRpeBtnGravar');
  if(btnG) btnG.addEventListener('click', function(){
   mxRpeGravar(btnG.getAttribute('data-id'));
  });
 }).catch(function(){ box.innerHTML=''; });
}

let MX_RPE_ULTIMOS = [];
let MX_VST_RPE_ULTIMOS = [];
let MX_VST_RPE_EDITAR = false;  // flag propria, independente da da Principal

function mxVstRpe(vid){
 const box=document.getElementById('mxVstRpe');
 if(!box || !vid) return;
 fetch('/api/moxy/vst/rpe/'+vid).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ box.innerHTML=''; return; }
  if(!(d.blocos||[]).length){
   box.innerHTML='<p class="sub" style="font-size:11px;">sem WORKs de BP1/BP2 '
    +'identificados nesta sessão para pedir RPE.</p>';
   return;
  }
  MX_VST_RPE_ULTIMOS = d.blocos;
  const rotulo=b=>b.grupo.toUpperCase()+' #'+b.numero;

  let h='<div style="border:1px solid #30363d;border-radius:6px;'
   +'padding:8px 10px;">'
   +'<b style="font-size:12px;">RPE — Dia 2 / VST</b> '
   +'<span class="sub" style="font-size:10px;">(1 fácil — 10 esforço máximo, só WORKs de BP1/BP2 — aquecimento e recovery não recebem RPE)</span>';

  if(d.todos_gravados && !MX_VST_RPE_EDITAR){
   h+='<br><span class="sub" style="font-size:11px;">'
    +d.blocos.map(function(b){
      return rotulo(b)+' '+b.watts_medio+'W → RPE '+b.rpe; }).join(' · ')+'</span>'
    +'<br><button id="mxVstRpeBtnRegravar" data-id="'+vid+'" '
    +'style="margin-top:6px;font-size:11px;padding:3px 10px;'
    +'border-radius:6px;border:1px solid #58A6FF;background:transparent;'
    +'color:#58A6FF;cursor:pointer;">↻ Re-gravar RPE</button>';
   box.innerHTML=h+'</div>';
   const btnR=document.getElementById('mxVstRpeBtnRegravar');
   if(btnR) btnR.addEventListener('click', function(){
    MX_VST_RPE_EDITAR=true; mxVstRpe(btnR.getAttribute('data-id'));
   });
   return;
  }

  h+='<table style="border-collapse:collapse;font-size:11px;margin-top:6px;">'
   +'<tr class="sub" style="text-align:left;">'
   +'<th style="padding-right:12px;">WORK</th>'
   +'<th style="padding-right:12px;">Potência média (W)</th><th>RPE</th></tr>'
   +d.blocos.map(function(b,i){
     return '<tr><td style="padding-right:12px;">'+rotulo(b)+'</td>'
      +'<td style="padding-right:12px;"><b>'+b.watts_medio+' W</b></td>'
      +'<td><input type="number" min="1" max="10" step="1" '
      +'id="mxVstRpeInput'+i+'" value="'+(b.rpe!=null?b.rpe:'')+'" '
      +'style="width:44px;background:#0d1117;border:1px solid #30363d;'
      +'color:#c9d1d9;border-radius:4px;padding:2px 4px;"></td></tr>';
    }).join('')
   +'</table>'
   +'<button id="mxVstRpeBtnGravar" data-id="'+vid+'" style="margin-top:8px;'
   +'font-size:11px;padding:4px 12px;border-radius:6px;'
   +'border:1px solid #3FB950;background:transparent;color:#3FB950;'
   +'cursor:pointer;">💾 Gravar RPE</button>'
   +' <span id="mxVstRpeEstado" class="sub" style="font-size:11px;"></span>';
  box.innerHTML=h+'</div>';
  const btnG=document.getElementById('mxVstRpeBtnGravar');
  if(btnG) btnG.addEventListener('click', function(){
   mxVstRpeGravar(btnG.getAttribute('data-id'));
  });
 }).catch(function(){ box.innerHTML=''; });
}

function mxVstRpeGravar(vid){
 const est=document.getElementById('mxVstRpeEstado');
 const blocos=[];
 for(let i=0;i<MX_VST_RPE_ULTIMOS.length;i++){
  const el=document.getElementById('mxVstRpeInput'+i);
  const v=el?parseInt(el.value,10):NaN;
  if(isNaN(v) || v<1 || v>10){
   if(est) est.textContent='WORK '+(i+1)+' precisa de um RPE entre 1 e 10';
   return;
  }
  const b=MX_VST_RPE_ULTIMOS[i];
  blocos.push({bloco_indice:i, watts_medio:b.watts_medio,
              t0_s:b.t0_s, t1_s:b.t1_s, rpe:v});
 }
 if(est) est.textContent='a gravar...';
 fetch('/api/moxy/vst/rpe/'+vid, {method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({blocos:blocos})})
 .then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){
   if(est) est.textContent=d.mensagem||'erro';
   return;
  }
  MX_VST_RPE_EDITAR=false;
  mxVstRpe(vid);
 }).catch(function(e){ if(est) est.textContent='erro: '+e.message; });
}


function mxRpeGravar(id){
 const est=document.getElementById('mxRpeEstado');
 const blocos=[];
 for(let i=0;i<MX_RPE_ULTIMOS.length;i++){
  const el=document.getElementById('mxRpeInput'+i);
  const v=el?parseInt(el.value,10):NaN;
  if(isNaN(v) || v<1 || v>10){
   if(est) est.textContent='bloco #'+(i+1)+' precisa de um RPE entre 1 e 10';
   return;
  }
  const b=MX_RPE_ULTIMOS[i];
  blocos.push({bloco_indice:i, watts_medio:b.watts_medio,
              t0_s:b.t0_s, t1_s:b.t1_s, rpe:v});
 }
 if(est) est.textContent='a gravar...';
 fetch('/api/moxy/rpe/'+id, {method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({blocos:blocos})})
 .then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){
   if(est) est.textContent=d.mensagem||'erro';
   return;
  }
  MX_RPE_EDITAR=false;
  mxRpe(id);
  // gravar o RPE sem actualizar a análise deixava o RPE guardado mas a
  // análise gravada continuar sem ele até se clicar noutro botão à
  // parte — o utilizador tinha de se lembrar de fazer as duas coisas.
  // Um "gravar RPE" já implica querer a análise actualizada com ele.
  if(est) est.textContent='RPE gravado. A actualizar a análise...';
  mxGuardarAnalise();
 }).catch(function(e){ if(est) est.textContent='erro: '+e.message; });
}

function mxEstilosRecentes(){
 const box=document.getElementById('mxEstilosRecentes');
 if(!box) return;
 fetch('/api/moxy/estilos_recentes?dias=60').then(r=>r.json())
 .then(function(d){
  if(d.status!=='ok'){ box.innerHTML=''; return; }
  let h='<details style="margin-top:4px;"><summary style="cursor:pointer;'
   +'font-size:12px;color:#8b949e;padding:4px 0;">Estilos treinados nos '
   +'últimos '+d.janela_dias+' dias</summary><div style="margin-top:6px;'
   +'font-size:11px;">';
  Object.keys(d.por_modalidade||{}).forEach(function(mod){
   const m=d.por_modalidade[mod];
   h+='<p><b>'+mod+'</b> — '+m.n_sessoes+' sessões, '+m.variedade
    +' estilo(s): '
    +m.tipos.map(function(t){ return t.tipo+' ('+t.n+')'; }).join(' · ')
    +'</p>';
  });
  if((d.estilos_ausentes||[]).length)
   h+='<p style="color:#F0883E;">Ausentes nos últimos '+d.janela_dias
    +' dias: '+d.estilos_ausentes.join(' · ')+'</p>';
  h+='<p class="sub" style="color:#8b949e;font-size:10px;">'+d.nota+'</p>'
   +'</div></details>';
  box.innerHTML=h;
 }).catch(function(){ box.innerHTML=''; });
}

// Plano por zona, com os watts/bpm DESTE teste — substitui a antiga
// tabela de "Métodos" (genérica, em %) e o "nível dois" solto no fim.
// Cada zona tem opção contínua e intervalada, e o "evitar" só aparece
// onde é relevante para ESTE limitador — o mesmo protocolo que é a
// ferramenta principal noutro limitador.
function mxPlanoPorZona(limitador, valores, destino){
 const box=document.getElementById(destino||'mxPlanoZonas');
 if(!box || !limitador) return;
 const vv=valores||{};
 const q=['plano_limitador='+encodeURIComponent(limitador)];
 ['bp1_w','bp2_w','bp1_bpm','bp2_bpm','smo2_min'].forEach(function(k){
  if(vv[k]!=null) q.push(k+'='+vv[k]);
 });
 fetch('/api/moxy/intervencoes?'+q.join('&')).then(r=>r.json())
 .then(function(d){
  if(d.status!=='ok'){ box.innerHTML=''; return; }
  let h='';
  if((d.faltam||[]).length)
   h+='<p style="color:#8b949e;font-size:10px;">em falta para completar '
    +'todas as zonas: '+d.faltam.join(', ')+'</p>';
  [['zona1','Zona 1'],['zona2','Zona 2'],['zona3','Zona 3']].forEach(
   function(par){
   const chave=par[0], nome=par[1];
   const z=(d.zonas||{})[chave]; if(!z) return;
   const alvo = (z.watts && z.watts[0]!=null)
     ? (z.watts[0]+'–'+(z.watts[1]||'?')+' W'
        +(z.bpm && z.bpm[0]!=null ? ' · '+z.bpm[0]+'–'+(z.bpm[1]||'?')+' bpm' : ''))
     : (z.bpm && z.bpm[1]!=null ? 'até '+z.bpm[1]+' bpm' : 'sem números');
   // RPE que o utilizador já gravou para blocos DESTA sessão cujos watts
   // caem dentro desta zona — só aparece se já houver algum gravado.
   let rpeTxt='';
   if(z.watts && z.watts[0]!=null && (MX_RPE_ULTIMOS||[]).length){
    const lo=z.watts[0], hi=z.watts[1]==null?1e9:z.watts[1];
    const rs=(MX_RPE_ULTIMOS||[]).filter(function(b){
      return b.rpe!=null && b.watts_medio!=null
        && b.watts_medio>=lo && b.watts_medio<=hi;
    }).map(function(b){ return b.rpe; });
    if(rs.length){
     const rmin=Math.min.apply(null,rs), rmax=Math.max.apply(null,rs);
     rpeTxt=' · RPE '+(rmin===rmax?rmin:rmin+'–'+rmax);
    }
   }
   h+='<div style="border:1px solid #30363d;border-radius:6px;'
    +'padding:6px 10px;margin-top:6px;">'
    +'<b style="font-size:12px;">'+nome+'</b> '
    +'<span style="color:#8b949e;font-size:11px;">'+alvo+rpeTxt+'</span>';
   ['continuo','intervalado'].forEach(function(tipo){
    const opcoes=(z.protocolos||[]).filter(function(p){
      return p.tipo===(tipo==='continuo'?'contínuo':'intervalado'); });
    if(!opcoes.length) return;
    h+='<div style="margin-top:4px;font-size:11px;">'
     +'<span style="color:'+(tipo==='continuo'?'#5DADE2':'#3FB950')
     +';">'+(tipo==='continuo'?'Contínuo':'Intervalado')+'</span>: '
     +opcoes.map(function(p){ return '<b>'+p.nome+'</b> — '+p.como; })
       .join(' &nbsp;|&nbsp; ')+'</div>';
   });
   if((z.evitar||[]).length){
    h+='<div style="margin-top:4px;font-size:11px;color:#F0883E;">'
     +z.evitar.map(function(e){
       return '<b>Evitar:</b> '+e.o_que
        +' <span style="color:#8b949e;">— '+e.porque+'</span>'; })
       .join('<br>')+'</div>';
   }
   h+='</div>';
  });
  h+='<p class="sub" style="font-size:10px;margin-top:4px;">'+(d.nota||'')
   +'</p>';
  box.innerHTML=h;
 }).catch(function(){ box.innerHTML=''; });
}

function mxSintese(){
 const box=document.getElementById('mxIntervencoes');
 if(!box) return;
 const q=[];
 if(MX_ULT_REDE) q.push('rede='+encodeURIComponent(MX_ULT_REDE));
 if(MX_ULT_US)   q.push('us='+encodeURIComponent(MX_ULT_US));
 if(MX_ULT_PC)   q.push('pc='+encodeURIComponent(MX_ULT_PC));
 if(MX_ULT_PERFIL) q.push('perfil='+encodeURIComponent(MX_ULT_PERFIL));
 if(MX_ULT_HIPO) q.push('hipocapnia=1');
 if(!q.length){ box.innerHTML=''; return; }
 fetch('/api/moxy/intervencoes?'+q.join('&')).then(r=>r.json())
 .then(function(d){
  if(!d.ok){
   box.innerHTML='<p style="color:#8b949e;font-size:11px;">'
    +(d.motivo||'')+'</p>';
   (d.avisos||[]).forEach(function(a){
    box.innerHTML+='<p style="color:#F0883E;font-size:11px;">⚠ '+a+'</p>'; });
   return;
  }
  // sem limitador dominante: mostrar na mesma. "Misto" é um resultado,
  // não ausência de resultado
  if(d.indeterminado){
   let hm='<div style="border:1px solid #58A6FF;border-radius:6px;'
    +'padding:8px 10px;margin-top:10px;">'
    +'<b style="color:#58A6FF;font-size:14px;">'+d.nome+'</b>'
    +(d.lidos&&d.lidos.length
      ? ' <span style="color:#8b949e;font-size:11px;">lido: '
        +d.lidos.join(' · ')+'</span>' : '')
    +'<br><span style="font-size:11px;">'+d.o_que_significa+'</span>'
    +'<br><span style="font-size:11px;color:#58A6FF;"><b>Agora:</b> '
    +d.o_que_fazer_agora+'</span>'
    +'<br><span style="font-size:11px;color:#8b949e;">'
    +d.quando_reavaliar+'</span>'
    +'<br><span style="font-size:10px;color:#8b949e;">Fundações: '
    +(d.fundacoes||[]).map(function(f){ return f.item; }).join(' · ')
    +'</span>';
   (d.avisos||[]).forEach(function(a){
    hm+='<p style="color:#F0883E;font-size:11px;margin:6px 0 0 0;">⚠ '+a
     +'</p>'; });
   box.innerHTML=hm+'</div>';
   return;
  }
  const iv=d.intervencao||{};
  const cor = d.confianca==='alta' ? '#3FB950'
            : d.confianca==='média' ? '#F0883E' : '#F85149';
  let h='<div style="border:1px solid '+cor+';border-radius:6px;'
   +'padding:8px 10px;margin-top:10px;">'
   +'<b style="color:'+cor+';font-size:14px;">'+iv.nome+'</b> '
   +'<span style="color:#8b949e;font-size:11px;">'+d.concordancia
   +' · confiança '+d.confianca+'</span>'
   +'<br><span style="font-size:11px;">'+(iv.o_que_e||'')+'</span>'
   +'<br><span style="font-size:11px;color:'+cor+';"><b>Agora:</b> '
   +d.o_que_fazer_agora+'</span>';
  (d.avisos||[]).forEach(function(a){
   h+='<p style="color:#F0883E;font-size:11px;margin:6px 0 0 0;">⚠ '+a
    +'</p>'; });
  h+='</div>';
  // Valores REAIS deste teste (watts, bpm, SmO2), não só receitas
  // genéricas. Sem isto a sugestão dizia "treina abaixo do 1º limiar"
  // sem dizer a que carga é que isso ficou.
  const vv=MX_ULT_VALORES;
  if(vv && (vv.bp1_w!=null || vv.bp2_w!=null)){
   h+='<div style="border-left:3px solid '+cor+';padding:6px 10px;'
    +'margin-top:6px;font-size:11px;">'
    +'<b>Valores encontrados neste teste ('+(vv.modalidade||'')+')</b><br>';
   if(vv.bp1_w!=null)
    h+='1.º limiar (aeróbio): <b>'+Math.round(vv.bp1_w)+' W</b>'
     +(vv.bp1_bpm?' · '+Math.round(vv.bp1_bpm)+' bpm':'')+'<br>';
   if(vv.bp2_w!=null)
    h+='2.º limiar (MLSS/RCP): <b>'+Math.round(vv.bp2_w)+' W</b>'
     +(vv.bp2_bpm?' · '+Math.round(vv.bp2_bpm)+' bpm':'')+'<br>';
   if(vv.smo2_min!=null)
    h+='SmO2 mínimo atingido: <b>'+Math.round(vv.smo2_min)+'%</b><br>';
   const lf=vv.limitador_fisiologico||{};
   if(lf.ok)
    h+='<span style="color:#8b949e;">padrão observado: '
     +(lf.candidatos||[]).join(', ')+' (amplitude SmO2 '
     +lf.amplitude_smo2+'%)</span><br>'
     +'<span style="color:#F0883E;font-size:10px;">'
     +lf.aviso_limited_vs_limiting+'</span>';
   h+='</div>';
  }
  box.innerHTML=h;
  mxPlanoPorZona(d.limitador, vv);
 }).catch(function(){ box.innerHTML=''; });
}

// ═══════════════════════════════════════════════════════════════════
// Grafo da rede causal -- consome directamente d.arestas/d.graus/
// d.canais_usados ja devolvidos por /api/moxy/rede (rede_causal.py),
// sem recalcular nada. Layout de forcas simples (repulsao entre todos
// os pares + atraccao pelas arestas), cor por sinal, espessura por F,
// nos de grau maior em tamanho maior.
// ═══════════════════════════════════════════════════════════════════
function mxDesenharRedeGrafo(d){
 const o = ctx('chMxRedeGrafo', 240); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const nos = d.canais_usados||[];
 const dirigidas = d.arestas||[];
 const ambiguas = (d.indecisas||[]).filter(e=>e.direccao==='ambigua');
 if(!nos.length){ noData(g,W,H,'Sem canais suficientes para desenhar a rede'); return; }

 const graus = d.graus||{};
 // layout de forcas -- mesma tecnica ja usada no PhysioNexus, adaptada
 // ao canvas 2D directo (sem paper coords)
 let seed=42;
 function rnd(){ seed=(seed*9301+49297)%233280; return seed/233280; }
 const PAD=50;
 const pos={};
 nos.forEach(function(n,i){
  const ang=(i/nos.length)*2*Math.PI;
  pos[n]={x:W/2+Math.min(W,H)/3*Math.cos(ang), y:H/2+Math.min(W,H)/3*Math.sin(ang)};
 });
 const K = 60*Math.sqrt(1/(nos.length||1))*10;
 const todasArestas = dirigidas.concat(ambiguas);
 for(let iter=0; iter<120; iter++){
  const disp={}; nos.forEach(n=>disp[n]={x:0,y:0});
  for(let i=0;i<nos.length;i++) for(let j=i+1;j<nos.length;j++){
   const n1=nos[i], n2=nos[j];
   let dx=pos[n1].x-pos[n2].x, dy=pos[n1].y-pos[n2].y;
   let dist=Math.sqrt(dx*dx+dy*dy)||0.01;
   const force=(K*K)/dist;
   dx/=dist; dy/=dist;
   disp[n1].x+=dx*force; disp[n1].y+=dy*force;
   disp[n2].x-=dx*force; disp[n2].y-=dy*force;
  }
  todasArestas.forEach(function(e){
   if(!pos[e.de]||!pos[e.para]) return;
   let dx=pos[e.de].x-pos[e.para].x, dy=pos[e.de].y-pos[e.para].y;
   let dist=Math.sqrt(dx*dx+dy*dy)||0.01;
   const force=(dist*dist)/(K*8);
   dx/=dist; dy/=dist;
   disp[e.de].x-=dx*force; disp[e.de].y-=dy*force;
   disp[e.para].x+=dx*force; disp[e.para].y+=dy*force;
  });
  const temp=8*(1-iter/120);
  nos.forEach(function(n){
   const dd=disp[n]; const dl=Math.sqrt(dd.x*dd.x+dd.y*dd.y)||0.01;
   pos[n].x += (dd.x/dl)*Math.min(dl,temp);
   pos[n].y += (dd.y/dl)*Math.min(dl,temp);
   pos[n].x = Math.max(PAD, Math.min(W-PAD, pos[n].x));
   pos[n].y = Math.max(PAD, Math.min(H-PAD, pos[n].y));
  });
 }

 g.clearRect(0,0,W,H);
 const maxF = Math.max(1, ...todasArestas.map(e=>e.f||0));
 const hoverAlvo=[];

 // arestas ambiguas primeiro (por baixo), tracejadas e cinzentas
 ambiguas.forEach(function(e){
  if(!pos[e.de]||!pos[e.para]) return;
  const p1=pos[e.de], p2=pos[e.para];
  g.strokeStyle='#484f58'; g.setLineDash([3,4]); g.lineWidth=1.5;
  g.beginPath(); g.moveTo(p1.x,p1.y); g.lineTo(p2.x,p2.y); g.stroke();
  g.setLineDash([]);
 });
 // arestas dirigidas: cor pelo sinal, espessura pelo F, seta na ponta
 dirigidas.forEach(function(e){
  if(!pos[e.de]||!pos[e.para]) return;
  const p1=pos[e.de], p2=pos[e.para];
  const cor = e.sinal==='-' ? '#F0883E' : '#3FB950';
  const largura = Math.max(1, Math.min(7, (e.f/maxF)*6+1));
  g.strokeStyle=cor; g.lineWidth=largura; g.globalAlpha=0.8;
  g.beginPath(); g.moveTo(p1.x,p1.y); g.lineTo(p2.x,p2.y); g.stroke();
  g.globalAlpha=1;
  // seta: triangulo perto do destino, encostado ao raio do no
  const ang=Math.atan2(p2.y-p1.y, p2.x-p1.x);
  const raio=14;
  const ax=p2.x-Math.cos(ang)*raio, ay=p2.y-Math.sin(ang)*raio;
  g.fillStyle=cor;
  g.beginPath();
  g.moveTo(ax,ay);
  g.lineTo(ax-8*Math.cos(ang-0.4), ay-8*Math.sin(ang-0.4));
  g.lineTo(ax-8*Math.cos(ang+0.4), ay-8*Math.sin(ang+0.4));
  g.closePath(); g.fill();
  hoverAlvo.push({x0:Math.min(p1.x,p2.x)-4,x1:Math.max(p1.x,p2.x)+4,
                  y0:Math.min(p1.y,p2.y)-4,y1:Math.max(p1.y,p2.y)+4,
                  de:e.de,para:e.para,f:e.f,p:e.p,lag:e.lag,sinal:e.sinal});
 });
 // nos por cima -- tamanho por grau total, cor cinza neutro (o sistema
 // ja aparece no rotulo por baixo do nome)
 nos.forEach(function(n){
  const gr=graus[n]||{saidas:0,entradas:0};
  const raio=Math.max(12, Math.min(26, 12+(gr.saidas+gr.entradas)*2.5));
  const p=pos[n];
  g.fillStyle = gr.saidas>gr.entradas ? '#3FB950' : (gr.entradas>gr.saidas ? '#F0883E' : '#8b949e');
  g.beginPath(); g.arc(p.x,p.y,raio,0,7); g.fill();
  g.strokeStyle='#0d1117'; g.lineWidth=2; g.stroke();
  g.fillStyle='#0d1117'; g.font='bold 10px sans-serif'; g.textAlign='center';
  g.fillText(n, p.x, p.y+3);
 });

 g.font='10px sans-serif'; g.textAlign='left'; g.fillStyle='#8b949e';
 g.fillText('● verde = fonte (mais saídas) · ● laranja = sumidouro (mais entradas)', 8, H-28);
 g.fillText('linha verde = correlação +, laranja = correlação − · tracejado cinza = ambíguo (direcção não decidida)', 8, H-14);

 MX_HOVER.chMxRedeGrafo = {rects:hoverAlvo};
}

function mxLigarHoverRedeGrafo(){
 const cv=document.getElementById('chMxRedeGrafo'), tip=document.getElementById('mxTipRedeGrafo');
 if(!cv||!tip) return;
 cv.addEventListener('mousemove', function(ev){
  const info=MX_HOVER.chMxRedeGrafo;
  if(!info||!info.rects.length){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
  const mx=(ev.clientX-r.left)*esc, my=(ev.clientY-r.top)*esc;
  const e=info.rects.find(function(rr){ return mx>=rr.x0&&mx<=rr.x1&&my>=rr.y0&&my<=rr.y1; });
  if(!e){ tip.style.display='none'; return; }
  tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+12, r.width-190)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-30)+'px';
  tip.innerHTML=e.de+' → '+e.para+'<br>F='+e.f+' · p='+e.p+' · lag='+e.lag+'s'
    +'<br>correlação: '+(e.sinal==='-'?'negativa':'positiva');
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

// PCR -- percentagem de F de saida por sistema (ja calculado por
// rede_causal.rede(), campo d.pcr), como barras horizontais simples
function mxDesenharRedePCR(d){
 const o = ctx('chMxRedePCR', 240); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 const pcr = d.pcr||{};
 const sistemas=['periferico','cardiaco','respiratorio','autonomico'];
 const cores={periferico:'#F85149',cardiaco:'#58A6FF',respiratorio:'#3FB950',autonomico:'#A371F7'};
 const presentes = sistemas.filter(s=>pcr[s] && pcr[s].canais_presentes);
 if(!presentes.length){ noData(g,W,H,'Sem sistemas suficientes'); return; }
 g.clearRect(0,0,W,H);
 const PL=90, PR=50, PT=8, barH=(H-PT*2)/presentes.length;
 const maxPct = Math.max(10, ...presentes.map(s=>pcr[s].pct));
 presentes.forEach(function(s,i){
  const y=PT+i*barH;
  const val=pcr[s].pct;
  const w=(val/maxPct)*(W-PL-PR);
  g.fillStyle=cores[s]; g.fillRect(PL, y+barH*0.15, w, barH*0.7);
  g.fillStyle='#8b949e'; g.font='10px sans-serif'; g.textAlign='right';
  g.fillText(s, PL-6, y+barH/2+3);
  g.fillStyle='#c9d1d9'; g.textAlign='left';
  g.fillText(val+'%', PL+w+6, y+barH/2+3);
 });
 g.fillStyle='#8b949e'; g.font='9px sans-serif'; g.textAlign='left';
 g.fillText('% do F total que SAI de cada sistema (peso pelo F das arestas, não contagem)', PL, H-2);
}

function mxRede(){
 const ids=Object.keys(MX_DADOS);
 const est=document.getElementById('mxRdEstado');
 const box=document.getElementById('mxRede');
 if(!ids.length){ est.textContent='escolhe uma sessão'; return; }
 if(ids.length>1) est.textContent='usa a 1.ª sessão seleccionada';
 const id=ids[0];
 const c=mxCorteDe(id);
 const q='?lag='+document.getElementById('mxRdLag').value
  +'&corr='+document.getElementById('mxRdCorr').value
  +'&inicio='+Math.round(c[0])+'&fim='+Math.round(c[1])
  +(document.getElementById('mxRdDif').checked?'':'&diferenciar=0')
  +(document.getElementById('mxRdCond').checked?'':'&condicionar=0')
  +(document.getElementById('mxRdDer').checked?'&derivados=1':'');
 est.textContent='a calcular...';
 fetch('/api/moxy/rede/'+id+q).then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ est.textContent=d.mensagem||d.motivo||'sem dados';
   box.innerHTML=''; return; }
  est.textContent=d.n_pares_testados+' pares testados · '+d.n_dirigidas
   +' com direcção · '+d.n_indecisas+' ambíguos'
   +(d.controlo?' · condicionado a '+d.controlo:' · SEM condicionar');
  let h='';
  const L=d.limitador||{};
  if(L.sistema||L.leitura){
   const cores={periferico:'#F85149',cardiaco:'#58A6FF',
                respiratorio:'#3FB950',autonomico:'#A371F7'};
   const cor=cores[L.sistema]||'#8b949e';
   h+='<div style="border-left:3px solid '+cor+';padding:6px 10px;'
    +'margin-bottom:10px;">'
    +'<b style="color:'+cor+';">LIMITADOR: '
    +(L.sistema?L.sistema.toUpperCase():'indeterminado')+'</b><br>'
    +'<span style="font-size:12px;">'+(L.leitura||'')+'</span>';
   const cp=L.controlo_pct||{};
   const ks=Object.keys(cp).sort(function(a,b){ return cp[b]-cp[a]; });
   if(ks.length) h+='<br><span style="font-size:11px;color:#8b949e;">'
    +ks.map(function(k){ return k+' '+cp[k]+'%'; }).join(' · ')
    +' &nbsp;(peso pelo F das arestas que partem de cada sistema)</span>';
   h+='<br><span style="font-size:10px;color:#8b949e;">'+(L.aviso||'')
    +'</span></div>';
  }
  if(d.fontes && d.fontes.length)
   h+='<p style="font-size:12px;"><b style="color:#3FB950;">Fontes:</b> '
    +d.fontes.join(', ')+' &nbsp; <b style="color:#F0883E;">Sumidouros:</b> '
    +(d.sumidouros||[]).join(', ')+'</p>';
  // Centralidade de grau e de intermediacao (Brandes) -- adaptado do
  // PhysioNexus (Evan Peikon). Out-degree/in-degree ja tinhamos em
  // d.graus; isto acrescenta as duas metricas que faltavam.
  if(d.metricas && Object.keys(d.metricas.centralidade_grau||{}).length){
   const mg=d.metricas.centralidade_grau, mb=d.metricas.centralidade_intermediacao;
   const nos=Object.keys(mg).sort(function(a,b){ return mg[b]-mg[a]; });
   h+='<details style="margin-top:8px;"><summary style="cursor:pointer;'
    +'font-size:12px;color:#8b949e;padding:4px 0;">Centralidade de rede '
    +'(grau e intermediação)</summary>'
    +'<table style="font-size:11px;border-collapse:collapse;margin-top:4px;">'
    +'<tr class="sub" style="text-align:left;"><th style="padding-right:14px;">Canal</th>'
    +'<th style="padding-right:14px;">Saídas</th><th style="padding-right:14px;">Entradas</th>'
    +'<th style="padding-right:14px;">Centralidade de grau</th><th>Intermediação</th></tr>'
    +nos.map(function(k){
      const gr=(d.graus||{})[k]||{saidas:0,entradas:0};
      return '<tr><td style="padding-right:14px;"><b>'+k+'</b></td>'
       +'<td style="padding-right:14px;">'+gr.saidas+'</td>'
       +'<td style="padding-right:14px;">'+gr.entradas+'</td>'
       +'<td style="padding-right:14px;">'+mg[k]+'</td>'
       +'<td>'+(mb[k]!=null?mb[k]:'—')+'</td></tr>';
    }).join('')
    +'</table>'
    +'<p style="font-size:10px;color:#8b949e;margin-top:4px;">Centralidade de '
    +'grau: quantas ligações tem, no total, sobre o máximo possível. '
    +'Intermediação: em quantos caminhos mais curtos entre OUTROS dois canais '
    +'este aparece pelo meio — um valor alto identifica um canal por onde a '
    +'influência tem de passar, mesmo que não seja a fonte nem o destino '
    +'final.</p></details>';
  }
  if(d.mecanicos_excluidos && d.mecanicos_excluidos.length)
   h+='<p style="font-size:11px;color:#8b949e;">Mecânicos usados só como '
    +'controlo, nunca testados como causa: <b>'
    +d.mecanicos_excluidos.join(', ')+'</b>. "A potência precede a subida da '
    +'FC" não é um achado — é a definição de treinar.</p>';
  h+='<details style="margin-top:6px;"><summary style="cursor:pointer;'
   +'font-size:12px;color:#8b949e;padding:4px 0;">Arestas da rede ('
   +((d.arestas||[]).length)+' com direcção, '
   +((d.indecisas||[]).filter(e=>e.direccao==='ambigua').length)
   +' ambíguas)</summary><div style="margin-top:6px;">';
  h+='<table style="border-collapse:collapse;font-size:11px;">'
   +'<tr style="color:#8b949e;text-align:left;">'
   +'<th style="padding-right:14px;">De</th><th style="padding-right:14px;">Para</th>'
   +'<th style="padding-right:14px;">F</th><th style="padding-right:14px;">p</th>'
   +'<th style="padding-right:14px;">Lag</th><th style="padding-right:14px;">r</th>'
   +'<th>Direcção</th></tr>';
  (d.arestas||[]).forEach(function(e){
   h+='<tr><td style="padding-right:14px;color:#3FB950;">'+e.de+'</td>'
    +'<td style="padding-right:14px;">'+e.para+'</td>'
    +'<td style="padding-right:14px;">'+e.f+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.p+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.lag+'s</td>'
    +'<td style="padding-right:14px;color:'
    +(e.sinal==='-'?'#F0883E':'#3FB950')+';">'+e.correlacao+'</td>'
    +'<td style="color:#8b949e;">'+(e.direccao||'')
    +(e.racio_f?' ('+e.racio_f+'×)':'')+'</td></tr>';
  });
  (d.indecisas||[]).filter(e=>e.direccao==='ambigua').forEach(function(e){
   h+='<tr style="opacity:.6;"><td style="padding-right:14px;">'+e.de+'</td>'
    +'<td style="padding-right:14px;">'+e.para+'</td>'
    +'<td style="padding-right:14px;">'+e.f+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.p+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.lag+'s</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'+e.correlacao+'</td>'
    +'<td style="color:#F0883E;">ambíguo</td></tr>';
  });
  h+='</table></div></details>';
  // o div do mxIntervencoes vive no BODY e não aqui: era criado no fim do
  // mxRede, mas o mxSintese corre a partir do mx515, que pode terminar
  // ANTES da rede -- e nessa altura o elemento ainda não existia, portanto
  // o cartão nunca aparecia numa sessão única
  MX_ULT_REDE=(d.limitador||{}).sistema||null;
  if(typeof mxSintese==='function') mxSintese();
  const dg=d.diagnostico||{};
  const dif=Object.keys(dg).filter(k=>dg[k] && dg[k].diferenciada);
  const exc=Object.keys(dg).filter(k=>dg[k] && dg[k].excluido);
  h+='<p style="color:#8b949e;font-size:11px;margin-top:6px;">'
   +'Corte de p corrigido: '+(d.p_corte_bh!=null?d.p_corte_bh:'nenhum par passou')
   +' · lag até '+d.max_lag+'s'
   +(dif.length?' · diferenciadas: '+dif.join(', '):' · nenhuma diferenciada')
   +(exc.length?' · excluídas: '+exc.map(k=>k+' ('+dg[k].excluido+')').join(', '):'')
   +'</p>';
  box.innerHTML=h;
  mxDesenharRedeGrafo(d);
  mxDesenharRedePCR(d);
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

function mxErro(msg){
 const e=document.getElementById('mxErro');
 if(!e) return;
 if(!msg){ e.style.display='none'; e.innerHTML=''; return; }
 e.style.display='block'; e.innerHTML=msg;
}

// Chips de data acima do grafico. Um clique escolhe, outro tira. Varias
// escolhidas = comparacao.
function mxDatasChips(){
 const box=document.getElementById('mxDatas');
 if(!box) return;
 if(!MX_SESSOES.length){
  box.innerHTML='<span style="color:#8b949e;font-size:12px;">Nenhuma sessão '
   +'com a tag "Moxy".</span>'; return; }
 box.innerHTML='<div style="display:flex;flex-wrap:wrap;gap:6px;">'
  + MX_SESSOES.map(function(s2){
   const i=MX_SEL.indexOf(String(s2.id));
   const on=i>=0;
   const cor=on?mxCorSessao(i):'#30363d';
   return '<button class="mxDia" data-id="'+s2.id+'" '
    + 'style="border:1px solid '+cor+';border-radius:14px;padding:3px 11px;'
    + 'background:'+(on?'rgba(88,166,255,0.10)':'transparent')+';'
    + 'color:'+(on?cor:'#8b949e')+';font-size:11px;cursor:pointer;">'
    + (on?'● ':'○ ') + s2.data
    + '<span style="opacity:.7;"> · '+(s2.modalidade||s2.tipo||'')+'</span>'
    + '</button>';
  }).join('') + '</div>';
 Array.prototype.forEach.call(box.querySelectorAll('.mxDia'), function(el){
  el.addEventListener('click', function(){
   const id=el.getAttribute('data-id');
   mxAlternarSessao(id, MX_SEL.indexOf(String(id))<0);
  });
 });
}

function mxCanaisEdit(){
 const box=document.getElementById('mxCanais');
 const ids=Object.keys(MX_DADOS);
 if(!box||!ids.length) return;
 const base=MX_DADOS[ids[0]];
 const nirs=base.canais_nirs||[], ctx2=base.canais_contexto||[];
 const todos=nirs.concat(ctx2);
 // Por omissão: só SmO2, THb, watts e FC. Os outros (respiração, DFA-a1,
 // deoxy-Hb calculado, etc.) começam desligados — o utilizador liga-os
 // se quiser, mas o gráfico não abre carregado com tudo.
 const PADRAO = ['smo2', 'thb', 'watts', 'heartrate'];
 if(!Object.keys(MX_ON).length)
  todos.forEach(function(k){ MX_ON[k] = PADRAO.indexOf(k) >= 0; });
 box.innerHTML = '<div style="display:flex;flex-wrap:wrap;gap:6px;'
  + 'align-items:center;">'
  + todos.map(function(k){
   const on = MX_ON[k] === true;
   const nirsQ = nirs.indexOf(k)>=0;
   const calc = (k==='wprime'||k==='mprime');
   const cor = MX_CORES[k]||'#c9d1d9';
   const nome = k==='wprime' ? "W′ restante"
              : k==='mprime' ? "M′ restante"
              : k==='hhb_calc' ? 'deoxy-Hb' : k;
   // as reservas não são "de contexto": são calculadas a partir do CP e
   // do W′. Marcá-las com o mesmo asterisco dos streams em bruto dizia
   // que não tinham sido filtradas, o que não descreve o que são
   return '<button class="mxC" data-k="'+k+'" '
    + 'style="border:1px solid '+(on?cor:'#30363d')+';border-radius:14px;'
    + 'padding:3px 11px;background:'+(on?'rgba(255,255,255,0.05)':'transparent')
    + ';color:'+(on?cor:'#6e7681')+';font-size:11px;cursor:pointer;">'
    + (on?'● ':'○ ') + nome + (calc?' †':(nirsQ?'':' *')) + '</button>';
  }).join('')
  + '<span style="color:#6e7681;font-size:10px;">* stream em bruto, '
  + 'não filtrado &nbsp; † calculado (reserva)</span>'
  + '</div>';
 Array.prototype.forEach.call(box.querySelectorAll('.mxC'), function(el){
  el.addEventListener('click', function(){
   const k=el.getAttribute('data-k');
   MX_ON[k] = !(MX_ON[k]===true);
   mxCanaisEdit(); mxDraw();
  });
 });
}

function mxAlternarSessao(id, on){
 mxErro(null);
 id = String(id);
 if(on){ if(MX_SEL.indexOf(id)<0) MX_SEL.push(id); }
 else { MX_SEL = MX_SEL.filter(x=>x!==id); delete MX_DADOS[id]; delete MX_OFF[id]; }
 mxCarregar();
}

function mxCarregar(){
 if(!MX_SEL.length){
  MX=null; MX_DADOS={}; mxDraw(); mxDiagnostico(); mxBlocosTabela();
  document.getElementById('mxEstado').textContent='escolhe uma sessão';
  return;
 }
 const est=document.getElementById('mxEstado');
 const q = '?fc=' + document.getElementById('mxFc').value
   + (document.getElementById('mxNorm').value
      ? '&normalizar=' + document.getElementById('mxNorm').value : '');
 est.textContent='a carregar ' + MX_SEL.length + ' sessão(ões)...';
 Promise.all(MX_SEL.map(function(id){
  return fetch('/api/moxy/dados/'+id+q).then(r=>r.json())
   .then(function(d){ return {id:id, d:d}; })
   .catch(function(e){ return {id:id, d:{status:'erro',mensagem:e.message}}; });
 })).then(function(res){
  MX_DADOS={}; let maus=[];
  res.forEach(function(r){
   if(r.d && r.d.status==='ok'){ MX_DADOS[r.id]=r.d; }
   else {
    const s2=MX_SESSOES.find(x=>String(x.id)===r.id)||{};
    let m='<b>'+(s2.data||r.id)+'</b>: '+(r.d.mensagem||'erro desconhecido');
    if(r.d.detalhe_dos_streams)
     m+='<br><span style="color:#8b949e;">Streams: '
      + r.d.detalhe_dos_streams.map(function(x){
         return x.stream+' ('+x.n_pontos+')'; }).join(' · ')+'</span>';
    maus.push(m);
   }
  });
  mxErro(maus.length ? maus.join('<br>') : null);
  mxDatasChips();
  const ids=Object.keys(MX_DADOS);
  MX = ids.length===1 ? MX_DADOS[ids[0]] : null;
  if(ids.length===1) mxCorteInicial();
  est.textContent = ids.length + (ids.length===1?' sessão':' sessões a comparar')
   + (maus.length ? ' · ' + maus.length + ' com problema' : '');
  mxCanaisEdit(); mxAlinhar();
  // correr as duas analises sozinhas, com os valores por omissao: ter de
  // carregar em dois botoes de cada vez que se muda de sessao e' trabalho
  // que a maquina pode fazer
  if(ids.length === 1){
   // limpar os resultados da sessão anterior: sem isto, o cartão mostrava
   // o limitador da sessão que estava seleccionada antes
   MX_ULT_REDE=MX_ULT_US=MX_ULT_PC=MX_ULT_PERFIL=null; MX_ULT_HIPO=false;
   const _bi=document.getElementById('mxIntervencoes');
   if(_bi) _bi.innerHTML='';
   mxModoUnico(true); mxRede(); mx515(); mxLimiares();
  }
  else if(ids.length > 1){ mxModoUnico(false); mxResumo(); }
  mxAnalises();
 });
}

// ── alinhamento ─────────────────────────────────────────────────────────
// O relogio de cada sessao nao serve: uma tem 12 min de aquecimento e outra
// nao. Alinha-se pelo protocolo. Por omissao, pelo inicio do primeiro bloco
// de trabalho dentro do corte -- e' o instante que existe em todas e que
// significa o mesmo em todas.
function mxRefAlinhamento(id){
 const d=MX_DADOS[id]; if(!d) return 0;
 const modo=document.getElementById('mxAlinha').value;
 const corte=mxCorteDe(id);
 if(modo==='inicio') return corte[0];
 const bl=((d.blocos||{}).blocos)||[];
 const ons=bl.filter(b=>b.on && b.t1>=corte[0] && b.t0<=corte[1]);
 if(!ons.length) return corte[0];
 if(modo==='watts'){
  // Alinha o degrau cuja potencia mais se aproxima da do primeiro degrau
  // da referencia. Resolve o caso em que uma sessao comeca mais suave que
  // as outras: a de 2024 arranca em degraus leves que as de 2026 nao tem,
  // e alinhar pelo primeiro bloco poe cargas diferentes lado a lado.
  const ref=Object.keys(MX_DADOS)[0];
  if(id===ref) return ons[0].t0;
  const dr=MX_DADOS[ref];
  const cr=mxCorteDe(ref);
  const onsRef=(((dr.blocos||{}).blocos)||[])
    .filter(b=>b.on && b.t1>=cr[0] && b.t0<=cr[1]);
  if(!onsRef.length) return ons[0].t0;
  const alvo=onsRef[0].watts_medio;
  if(alvo==null) return ons[0].t0;
  let melhor=null, dmin=1e18;
  ons.forEach(function(b){
   if(b.watts_medio==null) return;
   const dd=Math.abs(b.watts_medio-alvo);
   if(dd<dmin){ dmin=dd; melhor=b; }
  });
  // se nenhum degrau chega perto, nao ha escalao equivalente: dizer isso
  // em vez de alinhar por um que esta 60 W ao lado
  if(!melhor || dmin>30){
   MX_ALINHA_AVISO[id]=('nenhum degrau desta sessão fica a menos de 30 W do '
    +'primeiro da referência ('+Math.round(alvo)+' W). Alinhado pelo '
    +'primeiro bloco — usa o ajuste fino');
   return ons[0].t0;
  }
  delete MX_ALINHA_AVISO[id];
  return melhor.t0;
 }
 return ons[0].t0;
}

function mxCorteDe(id){
 const d=MX_DADOS[id]; if(!d) return [0,0];
 const t=d.tempo||[];
 if(!t.length) return [0,0];
 if(MX_SEL.length===1 && MX_CORTE) return MX_CORTE;
 const g=d.corte_guardado, p=d.corte_proposto;
 if(g && g.inicio_s!=null) return [g.inicio_s, g.fim_s];
 if(p && p.ok) return [p.inicio_s, p.fim_s];
 return [t[0], t[t.length-1]];
}

function mxAlinhar(){
 mxOffsetsUI(); mxDraw(); mxDiagnostico(); mxBlocosTabela();
}

function mxOffsetsUI(){
 const box=document.getElementById('mxOffsets');
 if(!box) return;
 const ids=Object.keys(MX_DADOS);
 if(ids.length<2){ box.innerHTML=''; return; }
 const avisos=Object.keys(MX_ALINHA_AVISO)
  .filter(k=>ids.indexOf(k)>=0)
  .map(function(k){
   const s2=MX_SESSOES.find(x=>String(x.id)===k)||{};
   return (s2.data||k)+': '+MX_ALINHA_AVISO[k]; });
 box.innerHTML = (avisos.length
  ? '<span style="color:#F0883E;font-size:11px;width:100%;">⚠ '
    +avisos.join(' | ')+'</span>' : '')
  + ids.map(function(id,i){
  const s=MX_SESSOES.find(x=>String(x.id)===id)||{};
  const off=MX_OFF[id]||0;
  return '<label class="sel" style="white-space:nowrap;">'
   + '<span style="color:'+mxCorSessao(i)+';">'+(i+1)+'· '+(s.data||id)+'</span> '
   + '<input type="range" class="mxOff" data-id="'+id+'" data-i="'+i+'" '
   + 'min="-300" max="300" step="1" value="'+off+'" style="width:130px"> '
   + '<span id="mxOffTxt'+i+'">'+(off>0?'+':'')+off+'s</span></label>';
 }).join('');
 Array.prototype.forEach.call(box.querySelectorAll('.mxOff'), function(el){
  el.addEventListener('input', function(){
   mxOffset(el.getAttribute('data-id'), el.value);
  });
 });
}

function mxOffset(id, v){
 MX_OFF[id]=parseFloat(v);
 mxDraw(); mxBlocosTabela();
 const i=Object.keys(MX_DADOS).indexOf(String(id));
 const el=document.getElementById('mxOffTxt'+i);
 if(el) el.textContent=(v>0?'+':'')+v+'s';
}

function mxCorSessao(i){
 return ['#F85149','#58A6FF','#3FB950','#E3B341','#A371F7','#F0883E'][i%6];
}

function mxCorteInicial(){
 // Prioridade ao corte guardado; sem ele, a proposta automatica; sem ela,
 // a sessao inteira. O que estiver a ser usado e' dito em texto, para nao
 // haver duvida sobre o que se esta a ver.
 const t=(MX&&MX.tempo)||[];
 if(!t.length){ MX_CORTE=null; return; }
 const t0=t[0], t1=t[t.length-1];
 const g=MX.corte_guardado, p=MX.corte_proposto;
 let origem;
 if(g && g.inicio_s!=null){ MX_CORTE=[g.inicio_s, g.fim_s]; origem='guardado'; }
 else if(p && p.ok){ MX_CORTE=[p.inicio_s, p.fim_s]; origem='proposto'; }
 else { MX_CORTE=[t0,t1]; origem='sessão inteira'; }
 const ini=document.getElementById('mxIni'), fim=document.getElementById('mxFim');
 ini.value = (MX_CORTE[0]-t0)/((t1-t0)||1)*100;
 fim.value = (MX_CORTE[1]-t0)/((t1-t0)||1)*100;
 mxCorteTexto(origem);
}

function mxCorteTexto(origem){
 const t=(MX&&MX.tempo)||[]; if(!t.length||!MX_CORTE) return;
 const f=v=>Math.floor(v/60)+':'+String(Math.round(v%60)).padStart(2,'0');
 document.getElementById('mxCorteTxt').textContent =
  f(MX_CORTE[0])+' → '+f(MX_CORTE[1])
  +'  ('+Math.round((MX_CORTE[1]-MX_CORTE[0])/60)+' min)';
 const p=MX.corte_proposto||{}, g=MX.corte_guardado;
 let n='';
 if(origem==='guardado') n='A usar o corte que guardaste em '
   +(g.data_gravacao||'?')+'.';
 else if(origem==='proposto') n='Proposta automática: '+(p.motivo||'')
   +(p.confianca?' · confiança '+p.confianca:'')+'.';
 else n='Sem proposta automática: '+(p.motivo||'')+'. A mostrar tudo.';
 const b=MX.blocos||{};
 if(b.ok) n+=' '+b.n_on+' blocos de trabalho e '+b.n_off+' de recuperação, '
   +'de '+(b.fonte||'?')+(b.limiar_w?' (limiar '+b.limiar_w+' W)':'')+'.';
 const p2=MX.corte_proposto||{};
 if(p2.aviso) n+=' '+p2.aviso+'.';
 document.getElementById('mxCorteNota').textContent=n;
}

function mxSlider(){
 const t=(MX&&MX.tempo)||[]; if(!t.length) return;
 const t0=t[0], t1=t[t.length-1];
 let a=parseFloat(document.getElementById('mxIni').value);
 let b=parseFloat(document.getElementById('mxFim').value);
 if(a>b){ const c=a; a=b; b=c; }
 MX_CORTE=[t0+(t1-t0)*a/100, t0+(t1-t0)*b/100];
 mxCorteTexto('manual'); mxDraw(); mxDiagnostico();
}

function mxAplicarProposta(){
 const p=MX&&MX.corte_proposto;
 if(!p||!p.ok){ document.getElementById('mxCorteEstado').textContent
   = 'sem proposta'; return; }
 MX_CORTE=[p.inicio_s,p.fim_s];
 const t=MX.tempo, t0=t[0], t1=t[t.length-1];
 document.getElementById('mxIni').value=(p.inicio_s-t0)/((t1-t0)||1)*100;
 document.getElementById('mxFim').value=(p.fim_s-t0)/((t1-t0)||1)*100;
 mxCorteTexto('proposto'); mxDraw(); mxDiagnostico();
}

function mxTudo(){
 const t=(MX&&MX.tempo)||[]; if(!t.length) return;
 MX_CORTE=[t[0],t[t.length-1]];
 document.getElementById('mxIni').value=0;
 document.getElementById('mxFim').value=100;
 mxCorteTexto('sessão inteira'); mxDraw(); mxDiagnostico();
}

function mxGuardarCorte(){
 if(!MX||!MX_CORTE) return;
 const est=document.getElementById('mxCorteEstado');
 const s=MX_SESSOES.find(x=>String(x.id)===String(MX.activity_id))||{};
 est.textContent='a guardar...';
 fetch('/api/moxy/corte',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({activity_id:MX.activity_id,
   inicio_s:MX_CORTE[0], fim_s:MX_CORTE[1],
   modalidade:s.modalidade, data:s.data,
   proposto_s:(MX.corte_proposto||{}).inicio_s})})
 .then(r=>r.json()).then(function(d){
  est.textContent = d.status==='erro' ? 'erro: '+d.mensagem
    : 'guardado' + (d.status==='gravado_sem_upload' ? ' (local)' : '');
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

// indices dentro do corte
function mxJanela(){
 const t=(MX&&MX.tempo)||[];
 if(!t.length) return [0,0];
 if(!MX_CORTE) return [0,t.length-1];
 let a=0,b=t.length-1;
 for(let i=0;i<t.length;i++){ if(t[i]>=MX_CORTE[0]){ a=i; break; } }
 for(let i=t.length-1;i>=0;i--){ if(t[i]<=MX_CORTE[1]){ b=i; break; } }
 return [a,b];
}

function mxDraw(){
 const o = ctx('chMoxy', 380); if(!o) return;
 const g=o.g, W=o.W, H=o.H;
 MX_HOVER_BP_FAIXAS = [];
 const ids=Object.keys(MX_DADOS);
 if(!ids.length){ noData(g,W,H,'Escolhe uma sessão na lista'); return; }
 const activos=Object.keys(MX_ON).filter(k=>MX_ON[k]===true);
 if(!activos.length){ noData(g,W,H,'Escolhe pelo menos uma métrica'); return; }

 // series alinhadas: tempo relativo ao ponto de referencia de cada sessao
 const series=[];
 ids.forEach(function(id, si){
  const d=MX_DADOS[id]; const t=d.tempo||[];
  const corte=mxCorteDe(id);
  const ref=mxRefAlinhamento(id)+(MX_OFF[id]||0);
  activos.forEach(function(k){
   const v=d.canais[k]; if(!v) return;
   const pts=[];
   for(let n=0;n<t.length;n++){
    if(t[n]<corte[0]||t[n]>corte[1]) continue;
    if(v[n]==null) continue;
    pts.push([t[n]-ref, v[n]]);
   }
   if(pts.length) series.push({
    id:id, canal:k, si:si, pts:pts,
    nirs:(d.canais_nirs||[]).indexOf(k)>=0,
    rotulo: ids.length>1 ? k+'_'+(si+1) : k});
  });
 });
 if(!series.length){ noData(g,W,H,'Sem dados no intervalo'); return; }

 let ta=1e18, tb=-1e18;
 series.forEach(function(s2){
  if(s2.pts[0][0]<ta) ta=s2.pts[0][0];
  if(s2.pts[s2.pts.length-1][0]>tb) tb=s2.pts[s2.pts.length-1][0];
 });

 const PL=54,PR=128,PT=18,PB=40,w=W-PL-PR,h=H-PT-PB;
 const X=v=>PL+(v-ta)/((tb-ta)||1)*w;
 MX_ESC={X:X,PL:PL,PT:PT,w:w,h:h,t0:ta,t1:tb};

 // Potencia de TODAS as sessoes em fundo, cada uma na sua cor. Sem isto
 // nao se ve porque e' que os degraus nao alinham: uma sessao pode comecar
 // a 140 W e outra a 117 W, e a diferenca so' aparece aqui.
 const wsers=[];
 ids.forEach(function(id, si){
  const d=MX_DADOS[id], t=d.tempo||[], v=d.canais.watts;
  if(!v) return;
  const corte=mxCorteDe(id), ref=mxRefAlinhamento(id)+(MX_OFF[id]||0);
  // Guardar os NULOS em vez de os saltar.
  //
  // Antes fazia-se 'continue' nos valores nulos, o que os removia da
  // lista. A linha ligava então o fim de um degrau directamente ao início
  // do seguinte, sem passar pelo descanso — e os degraus apareciam
  // "grudados", sem descer a zero. A tab Atividades não tem este problema
  // porque percorre a série toda e QUEBRA o traço quando encontra um
  // nulo, em vez de o filtrar.
  const pts=[];
  for(let n=0;n<t.length;n++){
   if(t[n]<corte[0]||t[n]>corte[1]) continue;
   pts.push([t[n]-ref, (v[n]==null ? null : v[n])]);
  }
  if(pts.some(p=>p[1]!==null)) wsers.push({si:si, pts:pts});
 });
 // Normalizar do MINIMO ao MAXIMO da série, como a tab Atividades faz.
 //
 // Antes dividia-se pelo máximo absoluto e usava-se 42% da altura:
 //   y = PT + h - (v/wmax) * h * 0.42
 // Com degraus de 117 a 251 W, isso dava 86 px de amplitude num gráfico
 // de 380 — os degraus ficavam esmagados no fundo e pareciam planos.
 // De min a max são os 380 px todos, e a escada vê-se.
 let wmin=null, wmax=null;
 wsers.forEach(function(ws){ ws.pts.forEach(function(p){
  if(p[1]===null) return;
  if(wmin===null||p[1]<wmin) wmin=p[1];
  if(wmax===null||p[1]>wmax) wmax=p[1]; }); });
 if(wmax!==null && wmax>wmin){
  // ocupa a metade de baixo, para não tapar os canais NIRS
  const hW = h*0.45, topoW = PT + h - hW;
  wsers.forEach(function(ws){
   const cor = ids.length>1 ? mxCorSessao(ws.si) : '#8b949e';
   // um ponto por pixel, com a média da coluna: 1800 pontos em ~800 px
   // punham vários valores no mesmo sítio e o traço virava mancha
   const cols={};
   ws.pts.forEach(function(p){
    const px=Math.round(X(p[0]));
    if(!cols[px]) cols[px]=[0,0,0];
    if(p[1]===null){ cols[px][2]++; }
    else { cols[px][0]+=p[1]; cols[px][1]++; }
   });
   const xs=Object.keys(cols).map(Number).sort(function(a,b){return a-b;});
   g.strokeStyle=cor; g.globalAlpha=0.6; g.lineWidth=1.3;
   g.beginPath();
   let st=false;
   xs.forEach(function(px){
    const c=cols[px];
    // coluna sem nenhum valor real: quebrar o traço, como na tab
    // Atividades. Sem isto a linha salta o descanso e liga degraus
    if(!c[1]){ st=false; return; }
    const media=c[0]/c[1];
    const y = topoW + hW - (media-wmin)/(wmax-wmin)*hW;
    if(!st){ g.moveTo(px,y); st=true; } else g.lineTo(px,y);
   });
   g.stroke(); g.globalAlpha=1;
  });
  // escala à direita, como nos outros canais
  g.fillStyle='#6e7681'; g.font='10px sans-serif'; g.textAlign='left';
  for(let i=0;i<=2;i++){
   const val = wmax-(wmax-wmin)*i/2;
   g.fillText(Math.round(val)+'W', PL+w+6, topoW+hW*i/2+3);
  }
  g.fillText('watts', PL+4, topoW+hW-4);
 }


 // escala partilhada pelos NIRS; contexto normaliza-se por canal
 const nirs=series.filter(s2=>s2.nirs);
 let lo=1e18, hi=-1e18;
 nirs.forEach(s2=>s2.pts.forEach(function(p){
  if(p[1]<lo)lo=p[1]; if(p[1]>hi)hi=p[1]; }));
 if(lo>hi){ lo=0; hi=100; }
 const pad=(hi-lo)*0.08||1; lo-=pad; hi+=pad;
 const Y=v=>PT+h-(v-lo)/((hi-lo)||1)*h;

 g.strokeStyle='#21262d'; g.fillStyle='#8b949e'; g.font='11px sans-serif';
 for(let n=0;n<=4;n++){
  const v=lo+(hi-lo)*n/4, y=Y(v);
  g.beginPath(); g.moveTo(PL,y); g.lineTo(PL+w,y); g.stroke();
  g.textAlign='right'; g.fillText(Math.round(v), PL-6, y+4);
 }
 // marca do zero: o ponto de alinhamento
 if(ta<0 && tb>0){
  g.strokeStyle='#8b949e'; g.setLineDash([4,4]);
  g.beginPath(); g.moveTo(X(0),PT); g.lineTo(X(0),PT+h); g.stroke();
  g.setLineDash([]);
  g.fillStyle='#8b949e'; g.textAlign='center'; g.font='9px sans-serif';
  g.fillText('alinhamento', X(0), PT+10);
  g.font='11px sans-serif';
 }
 g.textAlign='center'; g.fillStyle='#8b949e';
 for(let n=0;n<=5;n++){
  const tv=ta+(tb-ta)*n/5;
  const m=Math.floor(Math.abs(tv)/60);
  g.fillText((tv<0?'-':'')+m+' min', X(tv), PT+h+18);
 }

 // NIRS na escala comum; contexto na sua
 const mostrarTendencia = document.getElementById('mxTendencia')
   && document.getElementById('mxTendencia').checked;
 // Regressão linear simples sobre os pontos válidos de uma série. Serve
 // para ver a DIRECÇÃO geral (a subir, a descer, estável) sem o ruído
 // amostra a amostra — pedido explicitamente para SmO2, THb, FC,
 // respiração e DFA-a1.
 //
 // Rolling de 30s, centrada, em vez de regressão linear: um sinal
 // fisiológico raramente é uma recta, e a média móvel segue a FORMA da
 // curva (sobe, estabiliza, desce) em vez de resumir tudo a um único
 // declive. Centrada e não à esquerda, para não deslocar os picos no
 // tempo — o mesmo critério já usado no resto do dashboard.
 // Linha de tendência ao estilo das figuras do 5-1-5: um ponto por
 // bloco (o TOPO ou o FUNDO, conforme o canal e o tipo de bloco), ligados
 // por uma linha tracejada com marcas — não uma média móvel contínua.
 //
 // A convenção é a mesma do interpretacao_515.py (PERGUNTAS 3A/4A/6A/7A/
 // 10/11): SmO2 e THb leem-se no VALE do trabalho e no PICO da
 // recuperação (última terça parte); a FC e a respiração leem-se ao
 // contrário — pico no trabalho, vale na recuperação — porque sobem com
 // o esforço em vez de descerem.
 const CANAL_INVERTIDO = {heartrate:true, respiration:true};
 function mxPontosPorBloco(pts, si, canal){
  const idS = ids[si] || ids[0];
  const blocos = (((MX_DADOS[idS]||{}).blocos||{}).blocos || []);
  if(!blocos.length) return null;
  const refS = mxRefAlinhamento(idS) + (MX_OFF[idS]||0);
  const invertido = !!CANAL_INVERTIDO[canal];
  const fora=[];
  blocos.forEach(function(b){
   const t0=b.t0-refS, t1=b.t1-refS;
   // recuperação: só o último terço, como nas perguntas 3A/6A/10
   const de = b.on ? t0 : t0 + (t1-t0)*2/3;
   const sub=pts.filter(function(p){ return p[0]>=de && p[0]<=t1
                                      && p[1]!=null; });
   if(!sub.length) return;
   // trabalho quer o vale (SmO2/THb) ou o pico (FC/respiração);
   // recuperação é o contrário
   const querMax = b.on ? invertido : !invertido;
   const alvo = sub.reduce(function(a,c){
     return (querMax ? c[1]>a[1] : c[1]<a[1]) ? c : a; });
   fora.push(alvo);
  });
  return fora.length>1 ? fora : null;
 }
 nirs.forEach(function(s2){
  g.strokeStyle=ids.length>1 ? mxCorSessao(s2.si) : (MX_CORES[s2.canal]||'#c9d1d9');
  g.lineWidth = ids.length>1 ? (s2.canal==='smo2'?2.2:1.4) : 2;
  g.setLineDash(mxTraco(s2.canal, ids.length));
  g.beginPath();
  s2.pts.forEach(function(p,n){ n?g.lineTo(X(p[0]),Y(p[1]))
                                 :g.moveTo(X(p[0]),Y(p[1])); });
  g.stroke(); g.setLineDash([]); g.lineWidth=1;
  if(mostrarTendencia){
   const cor=g.strokeStyle;
   const pts=mxPontosPorBloco(s2.pts, s2.si, s2.canal);
   if(pts){
    g.strokeStyle=cor; g.globalAlpha=0.9; g.lineWidth=1.4;
    g.setLineDash([3,2]);
    g.beginPath();
    pts.forEach(function(p,n){ n?g.lineTo(X(p[0]),Y(p[1]))
                                 :g.moveTo(X(p[0]),Y(p[1])); });
    g.stroke(); g.setLineDash([]);
    g.fillStyle=cor;
    pts.forEach(function(p){
     g.beginPath(); g.arc(X(p[0]),Y(p[1]),2.4,0,6.284); g.fill(); });
    g.globalAlpha=1; g.lineWidth=1;
   }
  }
 });
 const outros=series.filter(s2=>!s2.nirs && s2.canal!=='watts');
 outros.forEach(function(s2){
  const vs=s2.pts.map(p=>p[1]);
  let a=Math.min.apply(null,vs), b=Math.max.apply(null,vs);
  if(b===a) b=a+1;
  const Y2=v=>PT+h-(v-a)/((b-a)||1)*h;
  g.strokeStyle=ids.length>1 ? mxCorSessao(s2.si) : (MX_CORES[s2.canal]||'#c9d1d9');
  g.globalAlpha=0.55; g.lineWidth=1;
  g.setLineDash(mxTraco(s2.canal, ids.length));
  g.beginPath();
  s2.pts.forEach(function(p,n){ n?g.lineTo(X(p[0]),Y2(p[1]))
                                 :g.moveTo(X(p[0]),Y2(p[1])); });
  g.stroke(); g.setLineDash([]); g.globalAlpha=1;
  if(mostrarTendencia){
   const pts=mxPontosPorBloco(s2.pts, s2.si, s2.canal);
   if(pts){
    g.globalAlpha=0.95; g.lineWidth=1.4; g.setLineDash([3,2]);
    g.beginPath();
    pts.forEach(function(p,n){ n?g.lineTo(X(p[0]),Y2(p[1]))
                                 :g.moveTo(X(p[0]),Y2(p[1])); });
    g.stroke(); g.setLineDash([]);
    pts.forEach(function(p){
     g.beginPath(); g.arc(X(p[0]),Y2(p[1]),2.4,0,6.284); g.fill(); });
    g.globalAlpha=1; g.lineWidth=1;
   }
  }
 });

 // breakpoints como riscas verticais, quando calculados
 //
 // Antes procurava-se a AMOSTRA BRUTA (segundo a segundo) mais próxima do
 // valor do BP. Num desporto não-ergo, com um pico logo no início do
 // bloco (o atleta empurra forte no arranque), essa amostra passava perto
 // do valor do BP tanto a SUBIR no início como depois a ESTABILIZAR — e
 // ficava-se com a PRIMEIRA encontrada, normalmente a transitória do
 // início. A marca aparecia antes de o SmO2 sequer começar a cair.
 //
 // Agora procura-se o BLOCO (não a amostra) cujo watts_medio — já a média
 // da parte ESTÁVEL, sem o transitório inicial — está mais perto do valor
 // do BP, e a marca fica no MEIO desse bloco. É onde a medição realmente
 // aconteceu.
 const wserBP = wsers.find(function(w){ return w.si===0; }) || wsers[0];
 if(MX_BP && wserBP){
  // 'ref' (o ponto de alinhamento entre sessões) está declarado dentro
  // do forEach mais acima, fora de alcance aqui — usá-lo lançava
  // "ReferenceError: ref is not defined" e abortava o resto do desenho
  // em silêncio, incluindo os próprios marcadores. Recalcula-se para a
  // sessão de wserBP.
  const idBP = ids[wserBP.si] || ids[0];
  const refBP = mxRefAlinhamento(idBP) + (MX_OFF[idBP]||0);
  const blocosOn = ((MX_DADOS[idBP].blocos||{}).blocos||[]).filter(b=>b.on
    && b.watts_medio!=null);
  [[MX_BP.bp1,'#3FB950','BP1'],[MX_BP.bp2,'#F85149','BP2']].forEach(function(b){
   if(b[0]==null) return;

   // se houver VST consistente/parcial para este BP, na CACHE ja'
   // preenchida pela Principal/Limiares (mesma fonte, nao recalculada
   // aqui), desenhar uma FAIXA translucida entre o min e o max
   // verificados, em vez de uma linha unica -- nunca o ponto medio.
   const dvst = MX_VST_VERIF_CACHE[idBP];
   const chaveVst = b[2]==='BP1' ? 'bp1' : 'bp2';
   const vstOk = dvst && dvst.status==='ok' && dvst.sincronizado && dvst.analisado
     && dvst[chaveVst] && dvst[chaveVst].range_verificado
     && (dvst[chaveVst].status==='CONSISTENTE' || dvst[chaveVst].status==='PARCIALMENTE CONSISTENTE');

   function _xDoWatts(alvo){
    if(blocosOn.length){
     const bloco=blocosOn.reduce(function(a,c){
       return Math.abs(c.watts_medio-alvo)<=Math.abs(a.watts_medio-alvo)?c:a;
     });
     if(Math.abs(bloco.watts_medio-alvo)<=25) return X((bloco.t0+bloco.t1)/2 - refBP);
    }
    let melhor=null, dmin=1e18;
    wserBP.pts.forEach(function(p){
     const dd=Math.abs(p[1]-alvo);
     if(dd<dmin){ dmin=dd; melhor=p[0]; }
    });
    return (melhor==null || dmin>25) ? null : X(melhor);
   }

   if(vstOk){
    const rv=dvst[chaveVst].range_verificado;
    const xMin=_xDoWatts(rv[0]), xMax=_xDoWatts(rv[1]);
    if(xMin==null || xMax==null) return;  // sem correspondencia de blocos -- nao inventa faixa
    const x0=Math.min(xMin,xMax), x1=Math.max(xMin,xMax);
    const parcial = dvst[chaveVst].status==='PARCIALMENTE CONSISTENTE';
    g.fillStyle=b[1]; g.globalAlpha=parcial?0.08:0.16;
    g.fillRect(x0,PT,Math.max(2,x1-x0),h);
    g.globalAlpha=1;
    g.strokeStyle=b[1]; g.setLineDash(parcial?[2,3]:[]); g.lineWidth=1;
    g.strokeRect(x0,PT,Math.max(2,x1-x0),h);
    g.setLineDash([]);
    g.fillStyle=b[1]; g.font='10px sans-serif'; g.textAlign='center';
    const simb = parcial ? '~' : '✓';
    g.fillText(simb+' '+b[2]+' VST'+(parcial?' parcial':'')+': '+Math.round(rv[0])+'–'+Math.round(rv[1])+'W',
      (x0+x1)/2, PT+10);
    MX_HOVER_BP_FAIXAS = MX_HOVER_BP_FAIXAS || [];
    MX_HOVER_BP_FAIXAS.push({x0:x0,x1:x1,bp:b[2],range:rv,parcial:parcial});
    return;
   }

   // CASO A -- sem VST valido: comportamento original, linha pontual
   let x=null;
   if(blocosOn.length){
    const bloco=blocosOn.reduce(function(a,c){
      return Math.abs(c.watts_medio-b[0])<=Math.abs(a.watts_medio-b[0])?c:a;
    });
    if(Math.abs(bloco.watts_medio-b[0])<=25){
     const meio=(bloco.t0+bloco.t1)/2 - refBP;
     x=X(meio);
    }
   }
   if(x==null){
    // recurso: sem blocos disponíveis, volta ao método antigo
    let melhor=null, dmin=1e18;
    wserBP.pts.forEach(function(p){
     const dd=Math.abs(p[1]-b[0]);
     if(dd<dmin){ dmin=dd; melhor=p[0]; }
    });
    if(melhor==null || dmin>25) return;
    x=X(melhor);
   }
   g.strokeStyle=b[1]; g.setLineDash([5,4]); g.lineWidth=1.5;
   g.beginPath(); g.moveTo(x,PT); g.lineTo(x,PT+h); g.stroke();
   g.setLineDash([]); g.lineWidth=1;
   g.fillStyle=b[1]; g.font='10px sans-serif'; g.textAlign='center';
   const bpm = b[2]==='BP1' ? MX_BP.bp1_bpm : MX_BP.bp2_bpm;
   // dizer de onde vem o valor: o do script bate certo com o que se ve'
   // na Intervals.icu, a mediana dos metodos nao
   const disp = b[2]==='BP1' ? MX_BP.bp1_disp : MX_BP.bp2_disp;
   const doScript = MX_BP.fonte === 'script Intervals.icu';
   g.fillText(b[2]+' '+Math.round(b[0])+'W'+(bpm?' · '+bpm+'bpm':'')
              +(disp?' ±'+disp+'%':'')
              +(MX_BP.fiavel===false?' (?)':''), x, PT+10);
   g.font='9px sans-serif';
   g.fillText(doScript?'script icu':'mediana', x, PT+21);
   g.font='10px sans-serif';
  });
 }

 // cartao fixo, sempre visivel -- as etiquetas no proprio grafico ficam
 // por cima da linha vertical e podem sair do enquadramento se o corte
 // mudar; este cartao no canto nunca se move.
 const cartaoBP=document.getElementById('mxCartaoBP');
 if(cartaoBP){
  if(MX_BP && (MX_BP.bp1!=null || MX_BP.bp2!=null)){
   cartaoBP.style.display='block';
   cartaoBP.innerHTML =
    '<div id="mxCartaoBP1Linha">'
    + (MX_BP.bp1!=null ? '<b style="color:#3FB950">BP1</b> '+Math.round(MX_BP.bp1)+'W'
      +(MX_BP.bp1_bpm?' · '+MX_BP.bp1_bpm+'bpm':'') : '')
    + '</div><div id="mxCartaoBP2Linha">'
    + (MX_BP.bp2!=null ? '<b style="color:#F85149">BP2</b> '+Math.round(MX_BP.bp2)+'W'
      +(MX_BP.bp2_bpm?' · '+MX_BP.bp2_bpm+'bpm':'') : '')
    + '</div>';
   mxPrincipalVerificacaoMostrar();
  } else {
   cartaoBP.style.display='none';
  }
 }

 g.textAlign='left'; g.font='10px sans-serif';
 series.forEach(function(s2,n){
  g.fillStyle=ids.length>1 ? mxCorSessao(s2.si) : (MX_CORES[s2.canal]||'#c9d1d9');
  g.fillText('\u2500 '+s2.rotulo, PL+w+6, PT+12+n*12);
 });
 if(ids.length>1){
  g.fillStyle='#6e7681';
  g.fillText('cor = sessão · traço = canal', PL+w+6, PT+12+series.length*12+8);
 }
 g.font='11px sans-serif';
 mxLigarTip();
}

// Traco por canal quando ha varias sessoes: a cor passa a identificar a
// sessao, portanto o canal precisa de outra dimensao visual.
function mxTraco(canal, nSessoes){
 if(nSessoes<2) return [];
 return {smo2:[], thb:[6,3], o2hb:[2,2], hhb:[8,3,2,3],
         heartrate:[4,2], respiration:[1,3], dfa_a1:[10,4],
         cadence:[3,3], torque:[5,5], velocity_smooth:[7,2],
         wprime:[12,3], mprime:[12,3,3,3]}[canal] || [];
}

function mxLigarTip(){
 const cv=document.getElementById('chMoxy');
 const tip=document.getElementById('mxTip');
 if(!cv||!tip||cv._tipMx) return;
 cv._tipMx=true;
 cv.addEventListener('mousemove', function(ev){
  // Le de MX_DADOS e nao de MX: em comparacao MX e' null, e o tooltip
  // deixava de funcionar exactamente quando era mais util.
  const ids=Object.keys(MX_DADOS);
  if(!MX_ESC || !ids.length){ tip.style.display='none'; return; }
  const r=cv.getBoundingClientRect();
  const esc=(cv.width/r.width)/(window.devicePixelRatio||1);
  const mx=(ev.clientX-r.left)*esc;
  const e=MX_ESC;
  if(mx<e.PL||mx>e.PL+e.w){ tip.style.display='none'; return; }
  const trel=e.t0+(mx-e.PL)/e.w*(e.t1-e.t0);   // tempo relativo ao alinhamento
  const m=Math.floor(Math.abs(trel)/60), sg=Math.round(Math.abs(trel)%60);
  let h='<b>'+(trel<0?'-':'')+m+':'+String(sg).padStart(2,'0')+'</b>'
   +(ids.length>1?' <span style="color:#8b949e;">do alinhamento</span>':'');
  const activos=Object.keys(MX_ON).filter(k=>MX_ON[k]===true);
  ids.forEach(function(id, si){
   const d=MX_DADOS[id];
   const t=d.tempo||[];
   const ref=mxRefAlinhamento(id)+(MX_OFF[id]||0);
   const alvo=trel+ref;                        // tempo absoluto nesta sessao
   let idx=-1, dmin=1e18;
   for(let n=0;n<t.length;n++){
    const dd=Math.abs(t[n]-alvo);
    if(dd<dmin){ dmin=dd; idx=n; }
   }
   if(idx<0 || dmin>5) return;                 // fora desta sessao
   const s2=MX_SESSOES.find(x=>String(x.id)===id)||{};
   if(ids.length>1)
    h+='<br><span style="color:'+mxCorSessao(si)+';font-weight:600;">'
     +(si+1)+'· '+(s2.data||id)+'</span>';
   // degrau em que estamos, com os watts medios do lap
   const bl=((d.blocos||{}).blocos)||[];
   const b=bl.find(x=>t[idx]>=x.t0 && t[idx]<=x.t1);
   if(b) h+='<br><span style="color:#8b949e;font-size:10px;">'
    +(b.on?'trabalho':'recuperação')
    +(b.watts_medio!=null?' · média '+Math.round(b.watts_medio)+' W':'')
    +(b.tipo?' ['+b.tipo+']':'')+'</span>';
   activos.forEach(function(k){
    const v=(d.canais[k]||[])[idx];
    if(v==null) return;
    h+='<br><span style="color:'
     +(ids.length>1?mxCorSessao(si):(MX_CORES[k]||'#c9d1d9'))+';">'
     +k+(ids.length>1?'_'+(si+1):'')+'</span> '+v;
   });
  });
  tip.innerHTML=h; tip.style.display='block';
  tip.style.left=Math.min(ev.clientX-r.left+14, r.width-200)+'px';
  tip.style.top=Math.max(4, ev.clientY-r.top-40)+'px';
 });
 cv.addEventListener('mouseleave', function(){ tip.style.display='none'; });
}

function mxDiagnostico(){
 const box=document.getElementById('mxDiag');
 if(!box) return;
 const ids=Object.keys(MX_DADOS);
 if(ids.length>1){
  // com varias sessoes, mostrar so' o essencial de cada uma
  let hh='<table style="border-collapse:collapse;font-size:11px;">'
   +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Sessão</th>'
   +'<th style="padding-right:14px;">Canais</th><th style="padding-right:14px;">Artefacto</th>'
   +'<th>Corte</th></tr>';
  ids.forEach(function(id,si){
   const dd=MX_DADOS[id], s2=MX_SESSOES.find(x=>String(x.id)===id)||{};
   const ar=dd.artefactos||{}, c=mxCorteDe(id);
   hh+='<tr><td style="padding-right:14px;color:'+mxCorSessao(si)+';">'
    +(si+1)+'· '+(s2.data||id)+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'
    +(dd.canais_nirs||[]).join(', ')+'</td>'
    +'<td style="padding-right:14px;color:#8b949e;">'
    +(ar.pct_acima_do_limiar!=null?ar.pct_acima_do_limiar+'%':'—')+'</td>'
    +'<td style="color:#8b949e;">'+Math.round((c[1]-c[0])/60)+' min</td></tr>';
  });
  box.innerHTML=hh+'</table>';
  return;
 }
 const d=(MX&&MX.diagnostico)||{};
 const ks=Object.keys(d);
 if(!ks.length){ box.innerHTML=''; return; }
 // Diagnostico da limpeza em dropdown: interessa quando alguma coisa
 // corre mal, nao a cada leitura do grafico. O aviso de artefacto fica
 // FORA, a vista, porque esse muda a leitura de tudo o resto.
 let h='<details style="margin-top:4px;"><summary style="cursor:pointer;'
  +'font-size:12px;color:#8b949e;padding:4px 0;">Qualidade dos canais ('
  +ks.length+' canais)</summary><div style="margin-top:6px;">';
 h+='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Canal</th>'
  +'<th style="padding-right:14px;">Pontos</th><th style="padding-right:14px;">Inválidos</th>'
  +'<th style="padding-right:14px;">Outliers</th><th style="padding-right:14px;">Substituído</th>'
  +'<th>Filtro</th></tr>';
 ks.forEach(function(k){
  const x=d[k], f=x.filtro||{};
  const pct=x.pct_substituido;
  const cor = pct==null ? '#8b949e' : pct<5 ? '#3FB950' : pct<15 ? '#F0883E' : '#F85149';
  h+='<tr><td style="padding-right:14px;color:'+(MX_CORES[k]||'#c9d1d9')+';">'
   +k+'</td>'
   +'<td style="color:#8b949e;padding-right:14px;">'+(x.n_pontos||'—')+'</td>'
   +'<td style="color:#8b949e;padding-right:14px;">'+(x.invalidos||0)+'</td>'
   +'<td style="color:#8b949e;padding-right:14px;">'+(x.outliers||0)+'</td>'
   +'<td style="color:'+cor+';padding-right:14px;">'+(pct!=null?pct+'%':'—')+'</td>'
   +'<td style="color:#8b949e;">'+(f.metodo||'—')
   +(f.motivo?' ('+f.motivo+')':'')+'</td></tr>';
 });
 h+='</table>';
 // streams e artefacto DENTRO do dropdown, e o aviso de artefacto
 // repetido FORA quando e' alto: uma sessao com 40% de artefacto muda a
 // leitura de tudo, e nao pode ficar escondida atras de um clique.
 if(MX.streams_usados) h+='<p style="color:#8b949e;font-size:11px;">Streams: '
  +Object.keys(MX.streams_usados).map(function(k){
    return k+' \\u2190 '+MX.streams_usados[k]; }).join(' · ')+'</p>';
 h+='</div></details>';

 // aviso de artefacto FORA do dropdown: uma sessão com 40% de artefacto
 // muda a leitura de tudo e não pode ficar atrás de um clique
 // canais congelados: aviso à vista, porque invalida tudo o que depende
 // deles — e não é óbvio a olhar para o gráfico, já que uma linha recta
 // parece um sinal estável
 // FC descartada: dizer que os bpm sumiram e porquê. Sem isto, o
 // utilizador vê os limiares em watts e pensa que a FC não foi medida.
 const ci = MX.detalhe_invalidos;
 if(ci && ci.fc_invalida){
  h+='<p style="font-size:11px;border-left:2px solid #F85149;'
   +'padding-left:8px;margin:6px 0;">'
   +'<b style="color:#F85149;">FC descartada nesta sessão</b><br>'
   +'<span style="color:#8b949e;">'+(ci.consequencia||'')+'</span><br>'
   +'<span style="color:#6e7681;font-size:10px;">'
   +Object.keys(ci.motivos||{}).map(function(k){
     return k+': '+ci.motivos[k]; }).join(' · ')+'</span></p>';
 }

 const cg = MX.congelados;
 if(cg && cg.ok && Object.keys(cg.canais_congelados||{}).length){
  h+='<p style="font-size:11px;border-left:2px solid #F85149;'
   +'padding-left:8px;margin:6px 0;">'
   +'<b style="color:#F85149;">Sensor preso</b> — '
   +Object.keys(cg.canais_congelados).map(function(k){
     const x=cg.canais_congelados[k];
     return k+' em '+x.valor_preso+' durante '+x.pct_da_serie+'% da sessão'
      +(x.primeiro_troco_s!=null?' (desde '+Math.floor(x.primeiro_troco_s/60)
        +' min)':''); }).join(' · ')
   +'.<br><span style="color:#8b949e;">'+(cg.nota||'')+'</span>'
   +((cg.propagado_para||[]).length
     ? '<br><span style="color:#F0883E;">Também apagado em '
       +cg.propagado_para.join(', ')+': '+cg.porque_propaga+'</span>' : '')
   +'</p>';
 }

 const ar = MX.artefactos;
 if(ar && ar.pct_acima_do_limiar!=null){
  const c = ar.pct_acima_do_limiar<10 ? '#3FB950'
          : ar.pct_acima_do_limiar<30 ? '#F0883E' : '#F85149';
  h+='<p style="font-size:11px;color:#8b949e;border-left:2px solid '+c
   +';padding-left:8px;margin:6px 0;"><b style="color:'+c+';">'
   +ar.pct_acima_do_limiar+'%</b> dos pontos com artefacto na cinta acima de '
   +ar.limiar_usado+'%. '+ar.pontos_descartados+' pontos removidos da FC, '
   +'DFA-a1 e respiração antes de filtrar. O SmO2 e o THb vêm do Moxy e não '
   +'são afectados.</p>';
 }
 box.innerHTML=h;
}

function mxLista(){
 const box=document.getElementById('mxLista');
 if(!box) return;
 if(!MX_SESSOES.length){
  box.innerHTML='<p style="color:#8b949e;font-size:12px;">Nenhuma sessão com '
   +'Moxy encontrada. Só entram actividades com a <b>tag</b> "Moxy" — o nome '
   +'da sessão é ignorado. Confirma a grafia na tab Atividades.</p>';
  return;
 }
 let h='<table style="width:100%;border-collapse:collapse;font-size:12px;">'
  +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
  +'<th style="padding:6px;width:30px;"></th><th>Data</th><th>Modalidade</th>'
  +'<th>Sessão</th><th>Duração</th><th>SmO2 médio</th><th>Tags</th></tr>';
 MX_SESSOES.forEach(function(s2){
  const on=MX_SEL.indexOf(String(s2.id))>=0;
  const i2=MX_SEL.indexOf(String(s2.id));
  h+='<tr style="border-bottom:1px solid #161b22;'
   +(on?'background:rgba(88,166,255,0.06);':'')+'">'
   +'<td style="padding:6px;"><input type="checkbox" class="mxSel" '
   +'data-id="'+s2.id+'"'+(on?' checked':'')+'></td>'
   +'<td'+(on?' style="color:'+mxCorSessao(i2)+';font-weight:600;"':'')+'>'
   +s2.data+(on?' <span style="font-size:10px;">('+(i2+1)+')</span>':'')+'</td>'
   +'<td style="color:#8b949e;">'+(s2.modalidade||s2.tipo||'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s2.nome||'—')+'</td>'
   +'<td style="color:#8b949e;">'+(s2.duracao_min?s2.duracao_min+' min':'—')+'</td>'
   +'<td>'+(s2.smo2_no_sumario!=null?Math.round(s2.smo2_no_sumario):'—')+'</td>'
   +'<td style="color:#8b949e;">'+((s2.tags||[]).join(', ')||'—')+'</td></tr>';
 });
 h+='</table>';
 box.innerHTML=h;
 // handlers ligados em JS: aspas dentro de atributos HTML ja' partiram
 // este ficheiro uma vez, e voltam a partir a proxima alteracao
 Array.prototype.forEach.call(box.querySelectorAll('.mxSel'), function(el){
  el.addEventListener('change', function(){
   mxAlternarSessao(el.getAttribute('data-id'), el.checked);
  });
 });
}

// Tabela dos blocos de trabalho de cada sessao seleccionada. Existe para o
// alinhamento ser verificavel: se duas sessoes foram emparelhadas pelo
// primeiro bloco mas os degraus nao correspondem, ve-se aqui e corrige-se
// no ajuste fino, em vez de se descobrir a olho no grafico.
// Média de um canal dentro de [t0,t1]. Usada para watts/respiração/FC/
// SmO2 por degrau — o mesmo princípio da FC do último minuto, mas aqui é
// a média do bloco inteiro, porque a pergunta é "como foi o degrau", não
// "onde estabilizou".
function _mxMedia(canal, tempo, t0, t1){
 if(!canal || !tempo) return null;
 let soma=0, n=0;
 for(let i=0;i<tempo.length && i<canal.length;i++){
  if(tempo[i]>=t0 && tempo[i]<=t1 && canal[i]!=null){ soma+=canal[i]; n++; }
 }
 return n ? soma/n : null;
}

function mxBlocosTabelaUnica(id){
 const box=document.getElementById('mxBlocos');
 const d=MX_DADOS[id];
 if(!d){ box.innerHTML=''; return; }
 const corte=mxCorteDe(id);
 const t=d.tempo||[];
 const ons=(((d.blocos||{}).blocos)||[])
   .filter(b=>b.on && b.t1>=corte[0] && b.t0<=corte[1]);
 if(!ons.length){
  box.innerHTML='<p class="sub" style="font-size:11px;">Sem blocos de '
   +'trabalho detectados no intervalo.</p>';
  return;
 }
 // RPE já carregado por mxRpe(), na mesma ordem dos blocos de trabalho
 const rpes=MX_RPE_ULTIMOS||[];

 let h='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr class="sub" style="text-align:left;">'
  +'<th style="padding-right:12px;">Degrau</th>'
  +'<th style="padding-right:12px;">Watts (média)</th>'
  +'<th style="padding-right:12px;">Respiração</th>'
  +'<th style="padding-right:12px;">FC</th>'
  +'<th style="padding-right:12px;">SmO2</th>'
  +'<th>RPE</th></tr>';
 ons.forEach(function(b,k){
  const w=_mxMedia(d.canais.watts, t, b.t0, b.t1);
  const resp=_mxMedia(d.canais.respiration, t, b.t0, b.t1);
  const hr=_mxMedia(d.canais.heartrate, t, b.t0, b.t1);
  const smo2=_mxMedia(d.canais.smo2, t, b.t0, b.t1);
  const rpe=(rpes[k]||{}).rpe;
  h+='<tr><td style="padding-right:12px;color:#8b949e;">'+(k+1)+'</td>'
   +'<td style="padding-right:12px;"><b>'
   +(w!=null?Math.round(w)+' W':(b.watts_medio!=null
     ?Math.round(b.watts_medio)+' W':'—'))+'</b></td>'
   +'<td style="padding-right:12px;color:#8b949e;">'
   +(resp!=null?resp.toFixed(1)+'/min':'—')+'</td>'
   +'<td style="padding-right:12px;color:#E3B341;">'
   +(hr!=null?Math.round(hr)+' bpm':'—')+'</td>'
   +'<td style="padding-right:12px;color:#3FB950;">'
   +(smo2!=null?smo2.toFixed(1)+'%':'—')+'</td>'
   +'<td style="'+(rpe!=null?'color:#58A6FF;':'color:#484f58;')+'">'
   +(rpe!=null?rpe:'—')+'</td></tr>';
 });
 h+='</table>';
 box.innerHTML=h;
}

function mxBlocosTabela(){
 const box=document.getElementById('mxBlocos');
 if(!box) return;
 const ids=Object.keys(MX_DADOS);
 if(!ids.length){ box.innerHTML=''; return; }

 // Sessão única: tabela rica por degrau, com as métricas médias e o
 // RPE — não a comparação entre sessões, que não faz sentido aqui.
 if(ids.length===1){ mxBlocosTabelaUnica(ids[0]); return; }

 const cols=[];
 ids.forEach(function(id,si){
  const d=MX_DADOS[id];
  const corte=mxCorteDe(id);
  const ons=(((d.blocos||{}).blocos)||[])
    .filter(b=>b.on && b.t1>=corte[0] && b.t0<=corte[1]);
  const s2=MX_SESSOES.find(x=>String(x.id)===id)||{};
  cols.push({id:id, si:si, data:s2.data||id, ons:ons,
             ref:mxRefAlinhamento(id)+(MX_OFF[id]||0)});
 });
 const maxN=Math.max.apply(null, cols.map(c=>c.ons.length));
 if(!maxN){ box.innerHTML='<p style="color:#8b949e;font-size:11px;">Sem blocos '
   +'de trabalho detectados no intervalo.</p>'; return; }
 let h='<table style="border-collapse:collapse;font-size:11px;">'
  +'<tr style="color:#8b949e;text-align:left;"><th style="padding-right:14px;">Degrau</th>'
  +cols.map(function(c){ return '<th style="padding-right:14px;color:'
    +mxCorSessao(c.si)+';">'+(c.si+1)+'· '+c.data+'</th>'; }).join('')
  +'<th>Δ watts</th></tr>';
 for(let k=0;k<maxN;k++){
  const vals=cols.map(function(c){ return c.ons[k]; });
  const ws=vals.filter(v=>v&&v.watts_medio!=null).map(v=>v.watts_medio);
  const dif=ws.length>1 ? Math.round(Math.max.apply(null,ws)-Math.min.apply(null,ws)) : null;
  const cor = dif==null ? '#8b949e' : dif<15 ? '#3FB950' : dif<40 ? '#F0883E' : '#F85149';
  h+='<tr><td style="padding-right:14px;color:#8b949e;">'+(k+1)+'</td>'
   +vals.map(function(v,n){
     if(!v) return '<td style="padding-right:14px;color:#484f58;">—</td>';
     const rel=Math.round(v.t0-cols[n].ref);
     return '<td style="padding-right:14px;">'
      +(v.watts_medio!=null?Math.round(v.watts_medio)+' W':'—')
      +' <span style="color:#8b949e;">'+Math.round(v.duracao_s)+'s'
      +' @'+(rel>=0?'+':'')+rel+'s'
      +(v.tipo?' · '+v.tipo.toLowerCase():'')+'</span></td>';
    }).join('')
   +'<td style="color:'+cor+';">'+(dif!=null?dif+' W':'—')+'</td></tr>';
 }
 h+='</table>';
 if(ids.length>1) h+='<p style="color:#8b949e;font-size:11px;margin-top:4px;">'
  +'@ é o instante do degrau relativo ao ponto de alinhamento. Se os degraus '
  +'da mesma linha tiverem watts muito diferentes (Δ a vermelho), o '
  +'emparelhamento está errado — corrige no ajuste fino ou muda o critério '
  +'de alinhamento.</p>';
 box.innerHTML=h;
}

function mxEscolher(id){
 mxAlternarSessao(id, MX_SEL.indexOf(String(id))<0);
}

// ══════════════════════════════════════════════════════════════════════
// SECÇÃO DE INTERVENÇÕES
//
// Independente da análise acima. Lê as análises JÁ GRAVADAS, o que
// permite cruzar modalidades diferentes sem recalcular nada.
// ══════════════════════════════════════════════════════════════════════
let IV_SESSOES = [], IV_SEL = {};

function ivSessoes(){
 const elMod=document.getElementById('ivMod');
 if(!elMod) return;   // seccao removida -- ver nota no arranque
 const mod=elMod.value;
 const est=document.getElementById('ivEstado');
 est.textContent='a carregar...';
 fetch('/api/moxy/analises'+(mod?'?modalidade='+mod:''))
 .then(r=>r.json()).then(function(d){
  if(d.status!=='ok'){ est.textContent=d.mensagem||'erro'; return; }
  IV_SESSOES=d.analises||[];
  est.textContent=IV_SESSOES.length+' análise(s) gravada(s)'
   +(IV_SESSOES.length?'':' — grava na secção acima primeiro');
  const box=document.getElementById('ivLista');
  box.innerHTML=IV_SESSOES.map(function(x,i){
   // Por omissão só a MAIS RECENTE fica seleccionada (i===0, porque
   // /api/moxy/analises devolve por 'data DESC'), não todas. Antes
   // "!==false" marcava tudo como ligado enquanto não houvesse uma
   // escolha explícita — com muitas sessões gravadas, isso juntava-as
   // todas na análise sem o utilizador ter pedido.
   if(IV_SEL[x.activity_id] === undefined) IV_SEL[x.activity_id] = (i === 0);
   const on=IV_SEL[x.activity_id]===true;
   return '<button class="ivS" data-id="'+x.activity_id+'" '
    +'style="border:1px solid '+(on?'#3FB950':'#30363d')+';border-radius:14px;'
    +'padding:3px 11px;background:transparent;color:'+(on?'#3FB950':'#6e7681')
    +';font-size:11px;cursor:pointer;">'+(on?'● ':'○ ')
    +(x.data||x.activity_id)+' · '+(x.modalidade||'')+'</button>';
  }).join('');
  Array.prototype.forEach.call(box.querySelectorAll('.ivS'), function(b){
   b.addEventListener('click', function(){
    const id=b.getAttribute('data-id');
    IV_SEL[id]=!IV_SEL[id];
    ivSessoes();
   });
  });
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

function ivAnalisar(){
 const est=document.getElementById('ivEstado');
 const box=document.getElementById('ivResultado');
 const sel=IV_SESSOES.filter(x=>IV_SEL[x.activity_id]);
 if(!sel.length){ est.textContent='nenhuma sessão seleccionada'; return; }
 // as análises gravadas trazem os limitadores de cada método
 const payload=sel.map(function(x){
  return {id:x.activity_id, data:x.data, modalidade:x.modalidade,
          rede:x.rede_limitador, us:x.us_limitador, pc:x.pc_limitador,
          perfil:x.perfil};
 });
 est.textContent='a cruzar '+payload.length+' sessões...';
 fetch('/api/moxy/intervencoes?sessoes='
       +encodeURIComponent(JSON.stringify(payload)))
 .then(r=>r.json()).then(function(d){
  if(!d.ok){
   est.textContent='';
   box.innerHTML='<p style="color:#8b949e;font-size:12px;">'
    +(d.motivo||'sem limitador nas sessões escolhidas')+'</p>';
   return;
  }
  est.textContent=d.de+' sessões cruzadas';
  const iv=d.intervencao||{};
  const cor=d.estavel?'#3FB950':'#F0883E';
  // modalidades envolvidas: cruzar modalidades é intencional, mas o
  // utilizador tem de ver que o fez
  const mods=[...new Set(sel.map(x=>x.modalidade).filter(Boolean))];
  let h='<div style="border:1px solid '+cor+';border-radius:6px;'
   +'padding:10px 12px;">'
   +'<b style="font-size:17px;color:'+cor+';">'+(iv.nome||d.limitador_mais_comum)
   +'</b> <span style="color:#8b949e;font-size:12px;">'+d.n+' de '+d.de
   +' sessões ('+d.concordancia_pct+'%)'+(d.unanime?' · unânime':'')+'</span>'
   +(mods.length>1
     ? '<br><span style="color:#F0883E;font-size:11px;">a cruzar '
       +mods.join(', ')+' — o limitador pode ser específico da modalidade, '
       +'porque o gesto e a massa muscular envolvida são diferentes</span>'
     : '')
   +'<br><span style="font-size:12px;">'+(iv.o_que_e||'')+'</span>'
   +'<br><span style="font-size:12px;color:'+cor+';"><b>'+d.o_que_fazer
   +'</b></span>';
  if(d.partilhado)
   h+='<p style="color:#F0883E;font-size:11px;">⚠ '+d.partilhado+'</p>';
  h+='<table style="border-collapse:collapse;font-size:11px;margin-top:8px;">'
   +'<tr style="color:#8b949e;text-align:left;">'
   +'<th style="padding-right:14px;">Sessão</th><th style="padding-right:14px;">'
   +'Modalidade</th><th style="padding-right:14px;">Limitador</th>'
   +'<th>Concordância</th></tr>'
   +(d.sessoes||[]).map(function(x,i){
     const m=(sel[i]||{}).modalidade||'';
     return '<tr><td style="padding-right:14px;">'+(x.data||'—')+'</td>'
      +'<td style="padding-right:14px;color:#8b949e;">'+m+'</td>'
      +'<td style="padding-right:14px;">'+(x.limitador||'—')+'</td>'
      +'<td style="color:#8b949e;">'+(x.concordancia||'—')+'</td></tr>';
    }).join('')
   +'</table></div>';

  // plano por zona — a sessão mais recente do grupo cruzado dá os números
  const lc3=((sel[0]||{}).limiares||{}).limiares_consenso||{};
  const p1c=lc3.primeiro||{}, p2c=lc3.segundo||{};
  h+='<div id="mxPlanoZonasIv" style="margin-top:8px;"></div>';
  if(d.limitador_mais_comum){
   setTimeout(function(){
    mxPlanoPorZona(d.limitador_mais_comum, {
     bp1_w: p1c.mediana, bp2_w: p2c.mediana,
     bp1_bpm: (p1c.estimativas||[]).find(e=>e.bpm) ?
       (p1c.estimativas||[]).find(e=>e.bpm).bpm : null,
     bp2_bpm: (p2c.estimativas||[]).find(e=>e.bpm) ?
       (p2c.estimativas||[]).find(e=>e.bpm).bpm : null,
    }, 'mxPlanoZonasIv');
   }, 0);
  }
  h+='<p style="color:#8b949e;font-size:11px;margin-top:8px;">'
   +(d.nota||'')+'</p>';
  box.innerHTML=h;
 }).catch(e=>{ est.textContent='erro: '+e.message; });
}

function ivGlossario(){
 const box=document.getElementById('ivGlossario');
 if(!box || box.innerHTML) return;
 fetch('/api/moxy/intervencoes').then(r=>r.json()).then(function(d){
  if(d.status!=='ok') return;
  let h='';
  Object.keys(d.intervencoes||{}).forEach(function(k){
   const v=d.intervencoes[k];
   h+='<div style="border-left:3px solid #58A6FF;padding:6px 10px;'
    +'margin-bottom:10px;">'
    +'<b style="font-size:14px;">'+v.nome+'</b>'
    +'<br><span style="font-size:12px;">'+v.o_que_e+'</span>'
    +(v.sinais?'<br><span style="font-size:11px;color:#8b949e;">'
      +'<b>Sinais:</b> '+v.sinais.join(' · ')+'</span>':'')
    +(v.causas_possiveis?'<br><span style="font-size:11px;color:#8b949e;">'
      +'<b>Causas:</b> '+v.causas_possiveis.join(' · ')+'</span>':'')
    +(v.nao_fazer?'<br><span style="font-size:11px;color:#F0883E;">'
      +'<b>Não fazer:</b> '+v.nao_fazer+'</span>':'')
    +'</div>';
  });
  h+='<h3 style="font-size:13px;">Fundações — antes de qualquer '
   +'intervenção</h3>'
   +(d.fundacoes||[]).map(function(f){
     return '<p style="font-size:11px;color:#8b949e;"><b>'+f.item+'</b>: '
      +f.porque+'</p>'; }).join('')
   +'<h3 style="font-size:13px;">Zonas por SmO2</h3>'
   +'<table style="width:100%;border-collapse:collapse;font-size:11px;">'
   +'<tr style="color:#8b949e;text-align:left;border-bottom:1px solid #21262d;">'
   +'<th style="padding:5px;">Zona</th><th>Sinal</th><th>Usar para</th></tr>'
   +(d.zonas_smo2||[]).map(function(z){
     return '<tr style="border-bottom:1px solid #161b22;">'
      +'<td style="padding:5px;"><b>'+z.zona+'</b></td>'
      +'<td style="color:#3FB950;">'+z.sinal+'</td>'
      +'<td style="color:#8b949e;">'+z.usar_para+'</td></tr>'; }).join('')
   +'</table>'
   +'<p style="color:#8b949e;font-size:10px;">'+(d.fonte_zonas||'')+'</p>'
   +'<p style="color:#8b949e;font-size:11px;">'+(d.principio||'')+'</p>';
  box.innerHTML=h;
 });
}

mxLigarHoverPontos('chMxLimiares','mxTipLimiares');
mxLigarHoverPontos('chMxDfa1','mxTipDfa1');
mxLigarHoverZonas();
mxLigarHoverVstTemporal();
mxLigarHoverVstFisiologico();
mxLigarHoverRedeGrafo();
mxMudarSubTab('principal');
mxSessoes();
// ivSessoes()/ivGlossario() ja nao sao chamadas aqui -- a seccao que
// as precisava ("Intervenções — o que treinar") foi removida, o fluxo
// por sessao ja mostra tudo sozinho. As duas funcoes ficam protegidas
// contra elementos em falta, para o caso de ainda serem chamadas
// de outro sitio (ex.: depois de gravar uma analise).
window.addEventListener('resize', function(){
 mxDraw();
 if(MX_ULT_LIMIARES_D){
  mxDesenharLimiaresSmo2(MX_ULT_LIMIARES_D);
  mxDesenharDmax(MX_ULT_LIMIARES_D);
  mxDesenharDfa1(MX_ULT_LIMIARES_D.dfa1);
 }
 if(MX_ULT_PLANO) mxDesenharZonas(MX_ULT_PLANO, MX_ULT_RPE_D, MX_ULT_ZONAS_D);
});
"""


def render():
    from flask import render_template_string
    return render_template_string(page('Moxy', SLUG, BODY, JS))
