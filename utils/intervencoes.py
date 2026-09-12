"""utils/intervencoes.py — o que treinar, conforme o limitador.

De onde vem cada coisa está marcado. Nada aqui é inventado por mim: são
protocolos publicados, e a fonte de cada um está no campo `fonte`.

O PRINCÍPIO, antes das tabelas
──────────────────────────────
Peikon, na lição das intervenções:

  "the athlete's starting point dictates what intervention will get them
   to their goal outcome (...) This is why cookie cutter programs fail"

E, sobre escolher o protocolo:

  "If the protocol I choose doesn't address the underlying problem
   causing an athlete's limitation, it won't get us the desired result."

Ou seja: a tabela só serve depois de o limitador estar identificado. Dar
um protocolo de entrega a quem tem limitação de utilização não é
ineficiente — é inútil.

FUNDAÇÕES ANTES DE TUDO
───────────────────────
Peikon põe três pré-requisitos antes de qualquer trabalho de sistemas
energéticos: capacidade de movimento, coordenação e respiração.

  "If an athlete cannot comfortably perform all of the relevant movements
   for their sport, they lack coordination (...) or they have trouble
   breathing with movement appropriate mechanics, they need not spend
   time hammering out intensive energy system training."

Isto fica em primeiro lugar de propósito. É a parte que se salta.
"""

FUNDACOES = [
    {'item': 'Capacidade de movimento',
     'porque': ('sem conseguir executar confortavelmente os gestos do '
                'desporto, o trabalho de sistemas energéticos treina um '
                'padrão defeituoso')},
    {'item': 'Coordenação',
     'porque': ('a 5-1-5 detecta isto: SmO2 a SUBIR no último bloco com '
                'carga e FC constantes significa que o músculo medido está '
                'a ser recrutado menos — mudou a forma')},
    {'item': 'Respiração',
     'porque': ('respiração rápida e superficial causa hipocapnia, que '
                'IMITA limitação de utilização. Tratar a técnica antes de '
                'concluir que o músculo não extrai')},
]


# ══════════════════════════════════════════════════════════════════════════
# INTERVENCOES POR LIMITADOR
#
# Cada entrada tem: o que se pretende adaptar, o método, como se sabe que
# está a correr bem (o sinal no SmO2), e a fonte.
# ══════════════════════════════════════════════════════════════════════════

INTERVENCOES = {
    'entrega': {
        'nome': 'Limitação de entrega (cardíaco)',
        'o_que_e': ('o débito não acompanha: o músculo extrai tudo o que '
                    'lhe chega e continua com fome'),
        'sinais': ['SmO2 desce muito e não recupera entre blocos',
                   'THb a descer ao longo da sessão',
                   'FC estabiliza ou desce nos últimos degraus',
                   'SmO2 mínimo de trabalho abaixo de 30%'],
        'adaptacoes_alvo': [
            'fluxo sanguíneo e circulação periférica',
            ('débito cardíaco — FC × volume de ejeção, não só o volume '
             'sozinho'),
            'volume telediastólico (enchimento do ventrículo)',
            ('hipertrofia excêntrica do ventrículo esquerdo — adaptação de '
             'meses/anos de carga de volume repetida, não de uma sessão'),
            'coordenação cardio-pulmonar'],
        # Peikon organiza a "endurance basica" em quatro categorias D0-D3,
        # com alvos de FC e de SmO2 explicitos. Sao mais operacionais do
        # que "volume em zona verde": dizem a duracao, a intensidade e o
        # que o SmO2 deve fazer.
        'metodos': [
            {'metodo': 'D0 — regeneração',
             'como': ('intensidade que produz o SmO2 MÁXIMO local. Faixa '
                      'muito estreita: é preciso volume sanguíneo elevado, '
                      'logo alguma intensidade, mas se passar disso o SmO2 '
                      'começa a descer'),
             'alvo_smo2': 'no máximo local de SmO2 — "andar na linha fina"',
             'alvo_fc': None,
             'objectivo': ('não produz adaptação: estimula o sistema '
                           'linfático e leva ao estado parassimpático. É '
                           'recuperação'),
             'precisa_de_moxy': True,
             'fonte': 'Peikon, Training The Delivery Limited Athlete'},

            {'metodo': 'D1 — endurance básica',
             'como': ('20 min a 3–5 h contínuos. Para atletas pesados e '
                      'potentes, intervalos curtos: 40 s de trabalho, 20 s '
                      'de descanso, 40 séries — em vez de 30 min seguidos'),
             'alvo_smo2': ('SmO2 a SUBIR ao longo do intervalo, ou estável '
                           'num máximo local'),
             'alvo_fc': '50–65% da FC máxima',
             'alvo_lactato': 'sem acumulação acima da base',
             'sensacao': ('se não consegue manter conversa fluida, está a '
                          'trabalhar demais'),
             'frequencia': ('várias sessões no mesmo dia ou em dias '
                            'seguidos, sem consequências'),
             'fonte': 'Peikon, idem'},

            {'metodo': 'D2 — endurance moderada',
             'como': ('20 a 180 min contínuos, ou intervalos de 10–30 min, '
                      '2 a 6 séries, com 30–90 s de descanso'),
             'alvo_smo2': 'SmO2 estabilizado entre 40% e 70%',
             'alvo_fc': '65–75% da FC máxima',
             'alvo_lactato': 'muito pouca acumulação acima da base',
             'sensacao': ('~70–75% do esforço; deve conseguir dizer uma '
                          'frase completa'),
             'nota': ('em atletas avançados com historial de trabalho '
                      'contínuo longo, o D2 pode substituir o grosso do D1'),
             'fonte': 'Peikon, idem'},

            {'metodo': 'HIIT sistémico (Moxy)',
             'como': ('3–5 min a intensidade submáxima até atingir o SmO2 '
                      'mínimo individual; recuperação até voltar à linha '
                      'de base'),
             'alvo_smo2': 'chega ao mínimo em 2–5 min',
             'fonte': 'Moxy HIIT Guide, coluna "Systemic"'},
        ],
        'nota_categorias': (
            'o D1 pode substituir o D2 ou o D3 em semanas de descarga. E o '
            'D1 é ESTIMULATIVO — o D2 e o D3 já impõem carga a sério'),

        # ── NIVEL DOIS ────────────────────────────────────────────────
        # Peikon e' contundente sobre isto, e vale a pena citar:
        #
        #   "Long slow distance training and high volumes of 'zone two'
        #    training WITHOUT higher intensity training inputs are akin to
        #    training leg extensions and leg curls, but neglecting to
        #    squat heavy."
        #
        # As categorias D0-D3 sao o trabalho acessorio. Isto e' o
        # agachamento pesado -- e faltava por completo na tabela.
        'nivel_dois': {
            'porque': (
                'só volume em zona verde é como treinar extensões de perna '
                'e nunca agachar pesado. As categorias D são o acessório; '
                'isto é o trabalho principal'),
            'metodos': [
                {'metodo': 'Intervalo de dessaturação gradual',
                 'como': (
                     'em vez de repetições à intensidade alvo desde o '
                     'início, CONSTRUIR o ritmo dentro do intervalo. '
                     'Exemplo real: 500 m de remo com o ritmo a apertar a '
                     'cada 100 m — 1:55, 1:50, 1:45, 1:40, 1:35. A média '
                     'dá o mesmo 1:45, mas a adaptação é outra'),
                 'porque': (
                     'em repetições ao ritmo alvo, o consumo ultrapassa a '
                     'entrega logo no início e a maior parte do intervalo '
                     'passa-se em hipóxia. Isso serve para afinar antes de '
                     'competir, não para melhorar o limitador'),
                 'como_verificar': (
                     'a correlação entre SmO2 e THb fica entre −0,9 e −1 '
                     'na dessaturação gradual, e só −0,2 a −0,7 nos '
                     'intervalos tradicionais. Uma correlação forte e '
                     'negativa mostra vasodilatação hipóxica; uma fraca '
                     'mostra oclusão e vasoconstrição simpática — ou seja, '
                     'entrega e consumo desacoplados'),
                 'nota': ('é por isto que os tempo runs são valiosos para '
                          'meio-fundo e fundo'),
                 'fonte': 'Peikon, Training The Delivery Limited Athlete'},

                {'metodo': 'Sessões combinadas',
                 'como': ('duas ou mais intensidades na MESMA sessão, em '
                          'blocos separados'),
                 'fonte': 'Peikon, idem'},

                {'metodo': 'Sessões misturadas',
                 'como': ('duas ou mais intensidades DENTRO do mesmo '
                          'intervalo — é o caso da dessaturação gradual'),
                 'porque': ('a maioria dos programas salta de uma '
                            'intensidade para outra de repente; isto '
                            'integra a transição'),
                 'fonte': 'Peikon, idem'},
            ],
            'modalidade': (
                'preferir cíclico. Em formato misto exige desenvolvimento '
                'cardiopulmonar já alto, senão as contracções criam '
                'restrição de retorno venoso e prejudicam o débito. Se se '
                'fizer misto, alternar membros superiores e inferiores — '
                'desafia a regulação da pressão e a redistribuição do '
                'débito'),
        },

        # ── ESTILOS NOMEADOS — os que o atleta reconhece por nome ────────
        #
        # As categorias D0-D2 e o nível dois acima cobrem a mesma
        # fisiologia, mas com nomes técnicos. Aqui ficam os mesmos alvos
        # sob os nomes que se usam na prática, com watts/bpm calculados
        # para este atleta em alvos_para_atleta().
        'estilos_treino': [
            {'estilo': 'D3 — HIIT longo (perto do VO2máx)',
             'como': ('4–5 séries de 3–5 min a >90% da FC máxima, com '
                      '2–3 min de recuperação activa leve entre séries'),
             'porque': ('estímulo perto do débito cardíaco máximo, mantido '
                        'tempo suficiente para ser um estímulo de volume, '
                        'não só de intensidade'),
             'adaptacao_esperada': (
                 'é o protocolo clássico para volume de ejeção (o "4×4" '
                 'norueguês, Helgerud 2007). A hipertrofia excêntrica do '
                 'ventrículo é uma adaptação de meses de treino repetido '
                 'deste tipo — não desta sessão isolada; o erro comum é '
                 'apresentar como mecanismo directo da sessão o que é na '
                 'verdade o efeito acumulado de muitas sessões'),
             'alvo_fc': 'acima de 90% da FC máxima',
             'fonte': 'Helgerud et al. 2007; protocolo "4×4" norueguês'},

            {'estilo': '30/30 ou 40/20 em blocos',
             'como': ('2–3 blocos de 10 min: 30 s de esforço muito alto / '
                      '30 s de descanso leve (ou 40s/20s)'),
             'porque': ('acumula tempo perto do VO2máx sem a fadiga '
                        'periférica que o mesmo esforço contínuo geraria — '
                        'o descanso curto mantém a FC perto do tecto ao '
                        'longo do bloco todo'),
             'adaptacao_esperada': 'central, igual ao D3, com menos fadiga',
             'alvo_fc': 'sustentada acima de 85-90% durante o bloco',
             'fonte': 'família Billat/Rønnestad de protocolos intermitentes'},
        ],
        'nao_fazer_lista': [
            {'evitar': 'Isometria ou força de alta repetição perto da falha',
             'porque': (
                 'dois mecanismos diferentes, não um só: (1) a contracção '
                 'sustentada comprime os vasos LOCALMENTE, restringindo o '
                 'fluxo só para aquele músculo; (2) contracções isométricas '
                 'pesadas elevam a pressão arterial de forma aguda — a '
                 '"resposta pressora" bem documentada em treino de força '
                 '(MacDougall 1985) — e essa subida sistémica de pressão '
                 'é que aumenta a pós-carga a sério, contra a qual o '
                 'coração inteiro empurra. As duas coisas juntam-se no '
                 'mesmo efeito prático (menos volume ejectado durante o '
                 'esforço), mas não são o mesmo mecanismo')},
            {'evitar': 'Sprints curtos com descanso muito longo (10s/3min)',
             'porque': (
                 'esforços abaixo de 10-15s são dominados pelo sistema '
                 'fosfagénio, com contribuição aeróbia mínima. Um descanso '
                 'de 3 min nunca deixa a FC sustentar-se perto do máximo '
                 'tempo suficiente para gerar estímulo central — é '
                 'potência neuromuscular, não débito cardíaco')},
        ],
        'nao_fazer': ('mais intensidade não resolve: o músculo já usa tudo '
                      'o que recebe. O travão está a montante'),
    },

    'utilizacao': {
        'nome': 'Limitação de utilização (oxidativa muscular)',
        'o_que_e': ('chega oxigénio mas o músculo não o extrai. Mitocôndrias, '
                    'enzimas oxidativas, recrutamento'),
        'sinais': ['SmO2 de trabalho fica alto mesmo em carga alta (>30%)',
                   'queda fraca durante o trabalho intenso',
                   'recuperação forte ou prolongada nos intervalos'],
        'causas_possiveis': [
            'densidade mitocondrial baixa',
            'alteração da estrutura das fibras',
            'coordenação intra e intermuscular após lesão',
            'sobretreino crónico',
            ('desvio à esquerda da curva de dissociação da hemoglobina por '
             'respiração hipocápnica — ESTA não se trata com treino, '
             'trata-se com técnica respiratória')],
        'metodos': [
            {'metodo': 'HIIT local, O2-dependente',
             'como': ('sprints de menos de 30 s até o SmO2 chegar ao mínimo '
                      'individual; recuperação até voltar à linha de base. '
                      'Terminar ~10 pontos antes do mínimo, por causa do '
                      'atraso de medição'),
             'sinal_no_smo2': 'queda rápida e forte até ao mínimo',
             'fonte': 'Moxy HIIT Guide, coluna "Local O2 dependent"'},
            {'metodo': 'HIIT local, O2-independente',
             'como': ('30–120 s, mantendo o SmO2 no mínimo o máximo de '
                      'tempo possível. Parar quando o patamar já não se '
                      'sustenta. Recuperar até ao fim da hiperemia'),
             'sinal_no_smo2': 'patamar no mínimo, prolongado',
             'fonte': 'Moxy HIIT Guide, coluna "Local O2 independent"'},
            {'metodo': 'Força específica, cadência baixa e torque alto',
             'como': 'obriga o músculo a puxar oxigénio',
             'sinal_no_smo2': 'queda mais profunda à mesma potência',
             'fonte': 'Peikon, Training The Utilization Limited Athlete'},
            {'metodo': 'SIT supramáximo, 20s/10s (estilo boxe)',
             'como': ('20 s a esforço máximo absoluto, 10 s de recuperação, '
                      'repetido por "rondas" de vários minutos. Variante '
                      '"undulating": alternar 20on/60off com 30on/50off na '
                      'mesma sessão'),
             'sinal_no_smo2': ('dessaturação rápida e repetida; o objectivo '
                               'é supramáximo a sério — a maior parte dos '
                               'atletas fica submáxima sem perceber'),
             'fonte': ('Usher, Sprint Protocols For Boxing (SSOF #19) — '
                       '"supramaximal is where you need to be to get those '
                       'fast adaptive changes"')},
            {'metodo': 'Sprint de 30s com paragem por forma da recuperação',
             'como': ('30 s a esforço máximo no bike. Entre sprints, olhar '
                      'para a FORMA da queda da FC — não o número. '
                      'Queda rápida e nítida até um patamar = pronto para '
                      'o próximo. Queda lenta, com atraso, ou presa acima '
                      'do patamar anterior = parar a sessão'),
             'sinal_no_smo2': ('critério de FC, não de SmO2 — mas o mesmo '
                               'princípio de "olhar a forma, não só o '
                               'número" aplica-se ao SmO2 entre blocos'),
             'fonte': ('Babraj/Usher, Why VO2max Doesnt Matter — "look at '
                       'shapes: how is the heart rate dropping, is it '
                       'sharp then plateau, or slow and delayed? If it\'s '
                       'stuck, that\'s the signal to stop"')},
        ],
        'nota_fonte_sit': (
            'estes dois protocolos vêm de fisiologistas que discordam '
            'explicitamente do enquadramento "débito cardíaco": "we tend '
            'to think in systemic terms — cardiac output, stroke volume, '
            'VO2max. What we tend to think LESS about is what\'s the '
            'actual muscle doing." O alvo declarado é mitocondrial, não '
            'central — por isso ficam aqui e não em entrega, mesmo '
            'elevando a FC perto do máximo'),
        'nao_fazer': ('antes de concluir que é utilização, descartar '
                      'hipocapnia: respiração rápida e superficial produz '
                      'exactamente o mesmo padrão'),
    },

    'respiratorio': {
        'nome': 'Limitação respiratória (pulmonar)',
        'o_que_e': ('a ventilação não chega: CO2 acumula-se (hipercapnia) '
                    'ou o oxigénio não é trocado (EIAH)'),
        'sinais': ['THb de repouso e de trabalho a SUBIR ao longo da sessão',
                   'atraso do SmO2 face ao THb na recuperação',
                   'atraso que cresce nas cargas mais altas'],
        'metodos': [
            {'metodo': 'Treino de dessaturação prolongada (EDT)',
             'como': (
                 'potência fixa e rápida mas NÃO máxima: ~80–85% do máximo '
                 'no remo, 60–65% na bicicleta de ar, 85–90% no SkiErg. '
                 'Manter até o SmO2 deixar de descer e assentar num mínimo '
                 'local — ou seja, até a taxa de queda chegar a zero'),
             'sinal_no_smo2': ('a ΔSmO2 passa de negativa a ~0 %/s. É esse '
                               'o momento de parar o intervalo'),
             'porque_funciona': (
                 'acumula tempo a alta percentagem do VO2pico sem o volume '
                 'que os músculos e articulações não tolerariam'),
             'fonte': 'Peikon, Extended Desaturation Training'},
            {'metodo': 'Trabalho respiratório dedicado',
             'como': 'SpiroTiger ou equivalente, fora do treino',
             'sinal_no_smo2': '—',
             'fonte': 'Peikon, Training The Respiratory Limited Athlete'},
        ],
        'fundacoes_estruturais': (
            'posição das costelas, do diafragma e da pélvis. Peikon põe '
            'estas antes de qualquer intervenção de sistemas energéticos '
            'no atleta respiratoriamente limitado'),
    },
}


# ══════════════════════════════════════════════════════════════════════════
# ZONAS POR SmO2 — quatro zonas
#
# Peikon, Zoning Energy System Training with NIRS. Ao contrário das zonas
# por potência ou FC, estas definem-se pelo COMPORTAMENTO do sinal, e por
# isso adaptam-se ao dia.
# ══════════════════════════════════════════════════════════════════════════

ZONAS_SMO2 = [
    {'zona': 'Recuperação activa',
     'sinal': 'oferta excede procura — o SmO2 SOBE',
     'o_que_acontece': 'nenhum sistema é stressado, o limitador não é tocado',
     'usar_para': 'recuperação, aquecimento, aquisição de técnica'},
    {'zona': 'Endurance estrutural',
     'sinal': 'oferta e procura equilibradas — SmO2 estável',
     'o_que_acontece': ('o limitador é estimulado mas não excedido; esforço '
                        'sub-limiar'),
     'usar_para': 'o grosso do volume'},
    {'zona': 'Endurance funcional',
     'sinal': 'procura excede oferta — SmO2 desce e assenta mais baixo',
     'o_que_acontece': ('o limitador ficou sobrecarregado e entram os '
                        'padrões de compensação'),
     'usar_para': 'trabalho de limiar'},
    {'zona': 'Alta intensidade',
     'sinal': 'procura excede muito a oferta — queda rápida',
     'o_que_acontece': 'limitador E compensadores sobrecarregados',
     'usar_para': 'HIIT, SIT'},
]

FONTE_ZONAS = 'Peikon, Zoning Energy System Training with NIRS'


# ══════════════════════════════════════════════════════════════════════════
# LINHAS DE BASE — o que permite auto-regular
# ══════════════════════════════════════════════════════════════════════════

LINHAS_DE_BASE = {
    'recuperacao': {
        'o_que_e': ('valor estável de SmO2 durante um período de descanso, '
                    'depois de um aquecimento completo'),
        'usar_para': ('saber quando o intervalo seguinte pode começar, e '
                      'quando parar a sessão — se já não voltar à linha de '
                      'base, acabou')},
    'desempenho': {
        'o_que_e': ('o SmO2 MÍNIMO atingido numa série depois do '
                    'aquecimento'),
        'usar_para': ('saber quando parar: se já não desce até à linha de '
                      'base de desempenho apesar do esforço máximo, a '
                      'sessão terminou. Uma variação de 5–10% é normal')},
    'fonte': 'Peikon, Offensive Load Management Strategies',
    'aviso': ('estas linhas mudam com o dia. É esse o ponto: são o que '
              'permite adaptar a sessão ao estado de hoje em vez de seguir '
              'watts fixos de um teste de há três meses'),
}


def para_limitador(chave):
    """Devolve a intervenção para um limitador, aceitando vários nomes."""
    mapa = {
        'fornecimento': 'entrega', 'supply': 'entrega',
        'cardíaco': 'entrega', 'cardiaco': 'entrega', 'entrega': 'entrega',
        'periférico': 'utilizacao', 'periferico': 'utilizacao',
        'utilização': 'utilizacao', 'utilizacao': 'utilizacao',
        'utilization': 'utilizacao', 'muscular': 'utilizacao',
        'pulmonar': 'respiratorio', 'respiratório': 'respiratorio',
        'respiratorio': 'respiratorio', 'pulmonary': 'respiratorio',
    }
    k = mapa.get(str(chave).strip().lower())
    return INTERVENCOES.get(k) if k else None


def tudo():
    return {
        'fundacoes': FUNDACOES,
        'intervencoes': INTERVENCOES,
        'zonas_smo2': ZONAS_SMO2,
        'fonte_zonas': FONTE_ZONAS,
        'linhas_de_base': LINHAS_DE_BASE,
        'aviso': (
            'a tabela só serve depois de o limitador estar identificado, e '
            'um limitador de UMA sessão não é um limitador. Repetir em '
            'várias sessões antes de reorganizar o treino'),
        'principio': (
            'Peikon: "the athlete\'s starting point dictates what '
            'intervention will get them to their goal outcome. This is why '
            'cookie cutter programs fail"'),
    }


# ══════════════════════════════════════════════════════════════════════════
# TRADUZIR OS ALVOS PARA OS WATTS E BPM DESTE ATLETA
#
# As categorias vêm em % da FC máxima. Sem isto, o utilizador tem de fazer
# a conta de cabeça — e a % de FCmax é justamente o tipo de âncora que
# este dashboard evita: varia com o dia, com o calor, com a fadiga.
#
# Onde houver limiar medido, prefere-se o limiar. A % da FCmax só entra
# quando não há alternativa, e vai marcada como tal.
# ══════════════════════════════════════════════════════════════════════════

def _pct(v, lo, hi):
    return (round(v * lo), round(v * hi)) if v else None


def alvos_para_atleta(fc_max=None, lt1_w=None, lt2_w=None,
                      lt1_bpm=None, lt2_bpm=None, smo2_max=None):
    """Converte os alvos das categorias para valores concretos.

    Devolve, por categoria, o que se pode dizer com o que existe — e diz
    o que falta quando não dá.
    """
    fora = {}

    # ── D0: só com Moxy ──────────────────────────────────────────────
    fora['D0'] = {
        'nome': 'D0 — regeneração',
        'watts': None,
        'bpm': None,
        'smo2': (f'manter no máximo local (~{round(smo2_max)}% nesta '
                 'modalidade)' if smo2_max else 'no máximo local de SmO2'),
        'so_com_moxy': True,
        'porque': ('a intensidade certa é a que produz o SmO2 mais alto, e '
                   'isso não se sabe sem o sensor — é a definição da '
                   'categoria, não uma limitação do cálculo'),
    }

    # ── D1: abaixo do primeiro limiar ────────────────────────────────
    d1 = {'nome': 'D1 — endurance básica', 'ancora': None}
    if lt1_w:
        # o D1 fica claramente abaixo do LT1: é onde o SmO2 ainda sobe
        d1['watts'] = (round(lt1_w * 0.60), round(lt1_w * 0.90))
        d1['ancora'] = 'LT1 medido'
    if lt1_bpm:
        d1['bpm'] = (round(lt1_bpm * 0.82), round(lt1_bpm * 0.97))
        d1['ancora'] = d1['ancora'] or 'LT1 medido'
    if not d1.get('bpm') and fc_max:
        d1['bpm'] = _pct(fc_max, 0.50, 0.65)
        d1['ancora'] = '% da FC máxima (sem LT1 medido)'
    d1['smo2'] = 'a SUBIR ao longo do intervalo, ou estável no máximo'
    d1['duracao'] = '20 min a 3–5 h'
    d1['teste'] = 'consegue manter conversa fluida'
    fora['D1'] = d1

    # ── D2: entre os dois limiares, na metade de baixo ───────────────
    d2 = {'nome': 'D2 — endurance moderada', 'ancora': None}
    if lt1_w and lt2_w:
        d2['watts'] = (round(lt1_w * 0.95), round(lt1_w + (lt2_w - lt1_w) * 0.5))
        d2['ancora'] = 'entre LT1 e metade do caminho para o LT2'
    elif lt1_w:
        d2['watts'] = (round(lt1_w * 0.95), round(lt1_w * 1.15))
        d2['ancora'] = 'LT1 medido (sem LT2)'
    if lt1_bpm and lt2_bpm:
        d2['bpm'] = (round(lt1_bpm), round(lt1_bpm + (lt2_bpm - lt1_bpm) * 0.5))
        d2['ancora'] = d2['ancora'] or 'entre LT1 e LT2 medidos'
    if not d2.get('bpm') and fc_max:
        d2['bpm'] = _pct(fc_max, 0.65, 0.75)
        d2['ancora'] = '% da FC máxima (sem limiares medidos)'
    d2['smo2'] = 'estabilizado entre 40% e 70%'
    d2['duracao'] = '20–180 min, ou 2–6 × 10–30 min com 30–90 s de pausa'
    d2['teste'] = 'consegue dizer uma frase completa'
    fora['D2'] = d2

    # ── D3: acima do LT2 — HIIT longo e 30/30, perto do débito máximo ──
    d3 = {'nome': 'D3 — HIIT longo (perto do VO2máx)', 'ancora': None}
    if lt2_w:
        d3['watts'] = (round(lt2_w * 1.02), round(lt2_w * 1.15))
        d3['ancora'] = 'LT2 medido'
    if lt2_bpm and fc_max:
        d3['bpm'] = (round(lt2_bpm), round(fc_max * 0.97))
        d3['ancora'] = d3['ancora'] or 'LT2 medido e FC máxima'
    if not d3.get('bpm') and fc_max:
        d3['bpm'] = _pct(fc_max, 0.90, 0.97)
        d3['ancora'] = '% da FC máxima (sem LT2 medido)'
    d3['duracao'] = '3–5 min × 4–5 séries, 2–3 min de recuperação activa'
    d3['teste'] = 'FC não deve estabilizar abaixo do alvo — se estagnar, a carga está baixa'
    fora['D3'] = d3

    # SIT (sprint interval, estilo boxe): alvo separado por natureza —
    # é supramáximo, acima de qualquer limiar contínuo, e o alvo é uma FC
    # de referência no PRIMEIRO sprint, não uma gama sustentada.
    sit = {'nome': 'SIT supramáximo (20s/10s ou 30s)', 'ancora': None}
    if fc_max:
        sit['fc_1o_sprint'] = round(fc_max * 0.84)
        sit['ancora'] = ('FC máxima — o 1.º sprint de 30s "a frio" deve '
                         'chegar perto disto (referência de Babraj/Usher: '
                         '~160 bpm num atleta com FCmáx próxima de 190)')
    sit['criterio_paragem'] = (
        'não é um número de repetições fixo — é a FORMA da recuperação da '
        'FC entre sprints. Queda rápida até um patamar = continuar. Queda '
        'lenta, com atraso, ou presa acima do patamar anterior = parar')
    fora['SIT'] = sit

    faltam = []
    if not (lt1_w or lt1_bpm):
        faltam.append('LT1 medido')
    if not (lt2_w or lt2_bpm):
        faltam.append('LT2 medido')
    if not fc_max:
        faltam.append('FC máxima')
    if smo2_max is None:
        faltam.append('SmO2 máximo de uma sessão Moxy')

    return {
        'categorias': fora,
        'faltam': faltam,
        'nota': ('onde há limiar medido, os alvos ancoram nele. A % da FC '
                 'máxima só entra quando não há alternativa, e fica '
                 'assinalada — é o tipo de âncora que varia com o dia, o '
                 'calor e a fadiga'),
        'aviso': ('as fronteiras entre categorias não são linhas: são '
                  'transições. O que decide é o comportamento do SmO2, não '
                  'o número'),
    }


# ══════════════════════════════════════════════════════════════════════════
# LIGAR AOS RESULTADOS: 5-1-5, REDE CAUSAL E PERFIL
#
# A tabela acima organiza-se por limitador. Mas o dashboard produz TRÊS
# resultados diferentes, e eles nem sempre concordam:
#
#   rede causal  -> periférico / cardíaco / respiratório
#   5-1-5 U/S    -> utilização / fornecimento / misto
#   5-1-5 P/C    -> pulmonar / cardíaco / misto
#   perfil       -> monotónico / parabólico
#
# Faltava dizer o que fazer quando se contradizem — que é o caso comum, e
# o mais importante de todos.
# ══════════════════════════════════════════════════════════════════════════

def sintetizar(rede=None, us=None, pc=None, perfil=None, hipocapnia=None):
    """Junta os resultados e diz o que fazer, incluindo quando discordam."""
    votos = {}
    origens = {}

    def _voto(chave, origem):
        if not chave:
            return
        k = {'periférico': 'utilizacao', 'periferico': 'utilizacao',
             'utilização': 'utilizacao', 'utilizacao': 'utilizacao',
             'cardíaco': 'entrega', 'cardiaco': 'entrega',
             'fornecimento': 'entrega',
             'pulmonar': 'respiratorio',
             'respiratório': 'respiratorio'}.get(str(chave).lower())
        if k:
            votos[k] = votos.get(k, 0) + 1
            origens.setdefault(k, []).append(origem)

    _voto(rede, 'rede causal')
    _voto(us, '5-1-5 eixo U/S')
    _voto(pc, '5-1-5 eixo P/C')

    avisos = []

    # A hipocapnia manda em tudo o resto: imita utilização.
    if hipocapnia:
        avisos.append(
            'HIPOCAPNIA SUSPEITA. Respiração rápida e superficial desloca a '
            'curva de dissociação e faz a hemoglobina segurar o oxigénio — '
            'aparece como limitação de utilização sem o ser. Tratar a '
            'técnica respiratória ANTES de programar trabalho de extracção; '
            'qualquer conclusão sobre utilização fica suspensa até isso')

    if perfil == 'monotónico':
        avisos.append(
            'perfil monotónico: o primeiro limiar não é observável no SmO2. '
            'As categorias D1 e D2 têm de ancorar em potência e FC, não no '
            'comportamento do SmO2')

    if not votos:
        # MISTO nao e' ausencia de informacao: e' um resultado, e tem
        # consequencia pratica. Devolver ok=False fazia o cartao nao
        # aparecer de todo -- e o utilizador ficava sem saber se o
        # calculo tinha corrido ou nao.
        indefinido = [x for x in (rede, us, pc) if x]
        return {
            'ok': True,
            'limitador': None,
            'indeterminado': True,
            'lidos': indefinido,
            'nome': 'Sem limitador dominante',
            'o_que_significa': (
                'nenhum sistema domina: a entrega, a extracção e a '
                'ventilação estão equilibradas nesta sessão. Não é uma '
                'falha da medição — é um resultado, e dos bons: significa '
                'que não há um travão único a corrigir'),
            'o_que_fazer_agora': (
                'trabalho de base (D1 e D2) e fundações. Sem limitador '
                'identificado não há protocolo específico a escolher — e '
                'escolher um à sorte é pior do que não escolher'),
            'quando_reavaliar': (
                'o limitador muda com o estado de recuperação. Repetir a '
                'avaliação noutro dia, e de preferência bem descansado: um '
                'atleta fatigado mostra limitação de extracção que não tem '
                'quando está fresco'),
            'fundacoes': FUNDACOES,
            'confianca': 'n/a',
            'avisos': avisos,
        }

    top = max(votos, key=votos.get)
    n_top = votos[top]
    total = sum(votos.values())
    concordam = len(votos) == 1

    if not concordam:
        avisos.append(
            'os métodos DISCORDAM: '
            + ' · '.join(f'{k} ({votos[k]})' for k in votos)
            + '. Um limitador de uma sessão não é um limitador — repetir em '
            'várias antes de reorganizar o treino. Enquanto discordarem, '
            'trabalhar as FUNDAÇÕES, que servem em qualquer dos casos')

    return {
        'ok': True,
        'limitador': top,
        'intervencao': INTERVENCOES.get(top),
        'concordancia': f'{n_top} de {total} métodos',
        'unanime': concordam,
        'votos': votos,
        'origens': origens.get(top, []),
        'avisos': avisos,
        'confianca': ('alta' if concordam and n_top >= 2 else
                      'baixa' if not concordam else 'média'),
        'o_que_fazer_agora': (
            f'trabalhar as intervenções de {INTERVENCOES[top]["nome"]}'
            if concordam and n_top >= 2 else
            'fundações (movimento, coordenação, respiração) e repetir a '
            'avaliação — não vale a pena escolher um protocolo com os '
            'métodos a discordar'),
    }


# ══════════════════════════════════════════════════════════════════════════
# CONSENSO ENTRE SESSOES
#
# Quando se comparam varias sessoes, cada uma da' o seu limitador. O que
# interessa nao e' a lista -- e' saber se ha' padrao.
#
# O criterio e' o mesmo que ja' usamos noutros consensos: contagem simples
# e nao media ponderada. Com 3 sessoes a dizer periferico e 2 cardiaco, o
# que importa e' que ha' 2 a discordar, nao que 60% ganha.
# ══════════════════════════════════════════════════════════════════════════

def consenso_entre_sessoes(sessoes):
    """sessoes: [{'data':..., 'rede':..., 'us':..., 'pc':..., 'perfil':...}]"""
    if not sessoes:
        return {'ok': False, 'motivo': 'nenhuma sessão'}

    por_sessao, votos = [], {}
    for s in sessoes:
        r = sintetizar(rede=s.get('rede'), us=s.get('us'), pc=s.get('pc'),
                       perfil=s.get('perfil'),
                       hipocapnia=s.get('hipocapnia'))
        linha = {'data': s.get('data'), 'id': s.get('id'),
                 'limitador': r.get('limitador') if r.get('ok') else None,
                 'confianca': r.get('confianca'),
                 'concordancia': r.get('concordancia'),
                 'avisos': r.get('avisos') or []}
        por_sessao.append(linha)
        if linha['limitador']:
            votos[linha['limitador']] = votos.get(linha['limitador'], 0) + 1

    if not votos:
        return {'ok': False, 'motivo': 'nenhuma sessão deu limitador',
                'sessoes': por_sessao}

    top = max(votos, key=votos.get)
    n_tot = sum(votos.values())
    pct = round(votos[top] / n_tot * 100)
    unanime = len(votos) == 1 and n_tot > 1

    # comum a TODAS: se varios limitadores aparecem, as fundacoes e o que
    # todos partilham e' o que vale a pena fazer
    partilhado = None
    if len(votos) > 1:
        partilhado = (
            'os limitadores mudam entre sessões. Isso pode ser real — o '
            'limitador muda com o estado de recuperação e com a modalidade '
            '— ou pode ser ruído. Enquanto não estabilizar, o que serve em '
            'qualquer dos casos são as FUNDAÇÕES e o trabalho de base (D1 e '
            'D2), que nenhum limitador dispensa')

    return {
        'ok': True,
        'limitador_mais_comum': top,
        'intervencao': INTERVENCOES.get(top),
        'n': votos[top], 'de': n_tot, 'concordancia_pct': pct,
        'unanime': unanime,
        'contagem': votos,
        'sessoes': por_sessao,
        'estavel': pct >= 70,
        'o_que_fazer': (
            f'trabalhar {INTERVENCOES[top]["nome"]} — aparece em '
            f'{votos[top]} de {n_tot} sessões'
            if pct >= 70 else
            'não escolher protocolo ainda: os limitadores não estabilizaram'),
        'partilhado': partilhado,
        'nota': ('contagem simples, não média ponderada. Com 3 sessões a '
                 'dizer um e 2 a dizer outro, o que importa é que há 2 a '
                 'discordar — não que 60% ganha. Abaixo de 70% não há '
                 'padrão utilizável'),
    }


# ══════════════════════════════════════════════════════════════════════════
# ESTILOS RECENTES — o que o atleta tem feito de facto
#
# Usa o classificador de tipo de sessão que já existe (mnirs.classificar_
# de_summary), que só precisa do interval_summary de cada actividade — não
# precisa de streams, por isso é barato correr sobre muitas sessões de
# uma vez.
#
# A pergunta que isto responde: dos estilos que sugerimos, quais já fazes
# e quais nunca apareceram nos últimos N dias? Um atleta que só faz
# contínuo há dois meses não precisa de ouvir "faz D1 contínuo" — precisa
# de ouvir que falta o outro lado.
# ══════════════════════════════════════════════════════════════════════════

# Mapa do tipo devolvido pelo classificador para o estilo mais próximo
# nas listas de metodos/estilos_treino acima. Usado só para dar a
# correspondência ao utilizador — não filtra nem decide nada por si.
_TIPO_PARA_ESTILO = {
    'escada (teste de degraus)': 'D3 — HIIT longo / teste incremental',
    'contínuo': 'D1/D2 — endurance contínua',
    'intervalado por tempo': '30/30 ou 40/20 em blocos',
    'intervalado por distância': '30/30 ou 40/20 em blocos (por distância)',
    'blocos repetidos': 'SIT supramáximo, séries repetidas',
    'intervalado com descanso variável': 'undulating (Usher/Babraj)',
    'intervalado irregular': None,  # sem correspondência clara
}


def estilos_recentes(sessoes, dias=60):
    """sessoes: [{'modalidade':..., 'data':..., 'interval_summary':[...]}]

    Classifica cada sessão pelo interval_summary e agrega por modalidade.
    """
    import mnirs as _mn
    from datetime import date, timedelta

    corte = (date.today() - timedelta(days=dias)).isoformat()
    por_modalidade = {}
    ignoradas = 0
    for s in (sessoes or []):
        if not s.get('interval_summary') or (s.get('data') or '') < corte:
            continue
        try:
            c = _mn.classificar_de_summary(s['interval_summary'])
        except Exception:
            ignoradas += 1
            continue
        if not c.get('ok'):
            ignoradas += 1
            continue
        mod = s.get('modalidade') or '?'
        bloco = por_modalidade.setdefault(mod, {})
        tipo = c.get('tipo') or 'indeterminado'
        bloco[tipo] = bloco.get(tipo, 0) + 1

    resumo = {}
    for mod, tipos in por_modalidade.items():
        total = sum(tipos.values())
        ordenado = sorted(tipos.items(), key=lambda kv: -kv[1])
        resumo[mod] = {
            'n_sessoes': total,
            'tipos': [{'tipo': t, 'n': n,
                      'estilo_correspondente': _TIPO_PARA_ESTILO.get(t)}
                     for t, n in ordenado],
            'dominante': ordenado[0][0] if ordenado else None,
            'variedade': len(tipos),
        }

    faltam = []
    todos_tipos = {t for r in resumo.values() for t in
                   (x['tipo'] for x in r['tipos'])}
    for nome_tipo, nome_estilo in _TIPO_PARA_ESTILO.items():
        if nome_estilo and nome_tipo not in todos_tipos:
            faltam.append(nome_estilo)

    return {
        'ok': bool(resumo),
        'janela_dias': dias,
        'por_modalidade': resumo,
        'ignoradas': ignoradas,
        'estilos_ausentes': faltam,
        'nota': (
            'classificado pelo interval_summary de cada sessão, sem ler '
            'streams — a mesma lógica que classifica o tipo de sessão na '
            'tab Moxy. Uma sessão "intervalado irregular" não corresponde '
            'a nenhum estilo específico, por isso pode aparecer com '
            'frequência sem que isso signifique nada em falta'),
    }


# ══════════════════════════════════════════════════════════════════════════
# PLANO POR ZONAS — o que a tabela de "Métodos" devia ter sido desde o
# início: números REAIS do teste, não percentagens genéricas, e opções
# por zona em vez de uma lista só.
#
# Três zonas, ancoradas nos limiares DESTA sessão:
#   Zona 1  abaixo do BP1
#   Zona 2  entre o BP1 e o BP2
#   Zona 3  acima do BP2
#
# Para cada zona e cada limitador: o que fazer (contínuo e/ou
# intervalado, como OPÇÕES) e o que evitar NESSA zona PARA ESSE
# limitador — porque o mesmo protocolo pode ser certo para um limitador
# e errado para outro na mesma zona.
# ══════════════════════════════════════════════════════════════════════════

PLANO_ZONAS = {
    'entrega': {
        'zona1': {
            'protocolos': [
                {'nome': 'D1 contínuo', 'tipo': 'contínuo',
                 'como': '20 min a 3–5 h'},
                {'nome': 'D1 em blocos (atletas pesados)', 'tipo': 'intervalado',
                 'como': '40s trabalho / 20s descanso, 40 séries'},
            ],
            'evitar': [],  # zona segura para qualquer limitador
        },
        'zona2': {
            'protocolos': [
                {'nome': 'D2 contínuo', 'tipo': 'contínuo',
                 'como': '20–180 min'},
                {'nome': 'D2 intervalado', 'tipo': 'intervalado',
                 'como': '2–6 × 10–30 min, 30–90 s de pausa'},
            ],
            'evitar': [],
        },
        'zona3': {
            'protocolos': [
                {'nome': 'HIIT longo', 'tipo': 'intervalado',
                 'como': '4–5 × 3–5 min, 2–3 min de recuperação activa'},
                {'nome': '30/30 ou 40/20 em blocos', 'tipo': 'intervalado',
                 'como': '2–3 blocos de 10 min'},
            ],
            'evitar': [
                {'o_que': 'Sprints curtos com descanso muito longo (10s/3min)',
                 'porque': ('fosfagénio, contribuição aeróbia mínima. O '
                            'descanso de 3 min nunca deixa a FC sustentar-se '
                            'perto do máximo — sem tempo, sem estímulo '
                            'central')},
                {'o_que': 'Isometria ou força de alta repetição perto da falha',
                 'porque': ('a resposta pressora eleva a pressão arterial de '
                            'forma aguda — mais pós-carga, menos volume '
                            'ejectado durante o próprio esforço')},
            ],
        },
    },

    'utilizacao': {
        'zona1': {
            'protocolos': [
                {'nome': 'Força específica, cadência baixa e torque alto',
                 'tipo': 'intervalado', 'como': 'séries curtas, torque alto'},
            ],
            'evitar': [
                {'o_que': 'Volume puro de zona 1 como único estímulo',
                 'porque': ('Usher: a única forma de encorajar stress '
                            'metabólico alto e a capacidade de extrair é '
                            'através de alta intensidade. Zona 2 não dá '
                            'isso')},
            ],
        },
        'zona2': {
            'protocolos': [
                {'nome': 'Dessaturação gradual', 'tipo': 'intervalado',
                 'como': 'construir o ritmo dentro do intervalo, não entrar '
                         'já no ritmo alvo'},
            ],
            'evitar': [],
        },
        'zona3': {
            'protocolos': [
                {'nome': 'HIIT local O2-dependente', 'tipo': 'intervalado',
                 'como': 'sprints <30s até ao SmO2 mínimo, recuperar até à '
                         'linha de base'},
                {'nome': 'SIT supramáximo 20s/10s', 'tipo': 'intervalado',
                 'como': '20s esforço máximo / 10s recuperação, repetido em '
                         'rondas'},
            ],
            'evitar': [
                {'o_que': 'Recuperação longa entre séries (>60-90s)',
                 'porque': ('quebra a acumulação de stress metabólico que '
                            'é o alvo aqui — o objectivo é empilhar '
                            'esforços, não os isolar')},
            ],
        },
    },

    'respiratorio': {
        'zona1': {
            'protocolos': [
                {'nome': 'Trabalho respiratório dedicado', 'tipo': 'outro',
                 'como': 'SpiroTiger ou equivalente, fora do treino'},
            ],
            'evitar': [],
        },
        'zona2': {
            'protocolos': [
                {'nome': 'Base contínua com foco técnico',
                 'tipo': 'contínuo', 'como': 'postura das costelas, do '
                         'diafragma e da pélvis antes de subir a carga'},
            ],
            'evitar': [],
        },
        'zona3': {
            'protocolos': [
                {'nome': 'EDT — dessaturação prolongada', 'tipo': 'contínuo',
                 'como': 'potência fixa e rápida mas não máxima; manter até '
                         'a taxa de queda do SmO2 chegar a zero'},
            ],
            'evitar': [
                {'o_que': 'Sprints muito curtos (<60-90s) como estímulo principal',
                 'porque': ('a ventilação precisa de tempo sustentado para '
                            'se tornar o factor limitante. Um sprint de 20s '
                            'acaba antes de a respiração ser o travão')},
                {'o_que': 'Isometria com apneia ou Valsalva',
                 'porque': 'reduz directamente a ventilação durante o esforço'},
            ],
        },
    },
}


def plano_personalizado(limitador, bp1_w=None, bp2_w=None, bp1_bpm=None,
                        bp2_bpm=None, smo2_min=None, fc_max=None):
    """O plano de zonas com os NÚMEROS REAIS desta sessão.

    Não usa percentagens genéricas: zona 1 é "abaixo do BP1 medido nesta
    sessão", zona 2 é "entre o BP1 e o BP2 medidos", zona 3 é "acima do
    BP2 medido". Se faltar algum limiar, a zona correspondente fica sem
    números — não se inventa um substituto.
    """
    tpl = PLANO_ZONAS.get(limitador)
    if not tpl:
        return {'ok': False, 'motivo': f'sem plano para "{limitador}"'}

    zonas = {}
    limites_w = {
        'zona1': (None, bp1_w), 'zona2': (bp1_w, bp2_w), 'zona3': (bp2_w, None)}
    limites_bpm = {
        'zona1': (None, bp1_bpm), 'zona2': (bp1_bpm, bp2_bpm),
        'zona3': (bp2_bpm, fc_max)}

    for chave, bloco in tpl.items():
        lo_w, hi_w = limites_w[chave]
        lo_bpm, hi_bpm = limites_bpm[chave]
        zonas[chave] = {
            'watts': (round(lo_w) if lo_w else None,
                     round(hi_w) if hi_w else None),
            'bpm': (round(lo_bpm) if lo_bpm else None,
                   round(hi_bpm) if hi_bpm else None),
            'tem_numeros': bool(lo_w or hi_w or lo_bpm or hi_bpm),
            'protocolos': bloco['protocolos'],
            'evitar': bloco['evitar'],
        }

    return {
        'ok': True, 'limitador': limitador,
        'zonas': zonas,
        'smo2_min_da_sessao': smo2_min,
        'nota': ('os watts e bpm de cada zona vêm do BP1 e do BP2 medidos '
                 'NESTA sessão — não são percentagens genéricas da FC '
                 'máxima. Sem os dois limiares, a zona correspondente fica '
                 'sem números em vez de usar um substituto'),
    }
