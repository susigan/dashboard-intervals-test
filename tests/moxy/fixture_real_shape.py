# Fixture com o FORMATO COMPLETO produzido por api_moxy (rpe_zonas_integrado).
# Valores sintéticos. Campos conforme _rz_stats_zona, _rz_curva, _rz_regressao,
# _rz_hrvt, bp_rz, comparacao_bp_hrvt e day1_vs_day2.
def _iv(sessao, i, zona, w, rpe, hr, rf, sm, df, thb=None):
    return {'sessao':sessao,'intervalo':i,'zona':zona,'potencia':w,'rpe':rpe,'hr':hr,
            'respiracao':rf,'smo2':sm,'thb':thb,'dfa1':df,'t0':100*i,'grupo':'moxy' if sessao=='d1' else 'bp1'}

def _reg(a,b,n):
    return {'declive':a,'intercepto':b,'r2':0.8,'n':n,'exploratorio': n<5,'label':'x'}

def _stat(vals):
    vals=[v for v in vals if v is not None]
    if not vals: return {'media':None,'mediana':None,'n':0}
    s=sorted(vals); m=len(s)//2
    med = s[m] if len(s)%2 else (s[m-1]+s[m])/2
    return {'media':round(sum(s)/len(s),4),'mediana':round(med,4),'n':len(s)}

def real_shape(b1, b2, h1c, h1s, h2, sem_rpe_d1=False, sem_thb=True):
    d1=[_iv('d1',1,'Z1',b1-40,None if sem_rpe_d1 else 3,140.2,28.1,62.0,1.02),
        _iv('d1',2,'Z2',b1-10,None if sem_rpe_d1 else 5,150.8,30.6,60.1,0.91),
        _iv('d1',3,'Z2',b1+5,None if sem_rpe_d1 else 6,154.8,31.2,59.0,0.84),
        _iv('d1',4,'Z3',b2-2,None if sem_rpe_d1 else 8,163.4,34.0,56.4,0.69)]
    d2=[_iv('d2',1,'Z1',b1-30,3,141.0,28.9,61.5,1.00),
        _iv('d2',2,'Z2',b1+12,5,151.9,31.0,59.8,0.90),
        _iv('d2',3,'Z2',b1+22,6,155.2,31.5,58.9,0.80),
        _iv('d2',4,'Z3',b2+7,9,166.0,34.6,55.0,0.61)]
    ivs=d1+d2
    zonas={}
    for z in ('Z1','Z2','Z3'):
        zz=[i for i in ivs if i['zona']==z]
        if not zz: continue
        zonas[z]={'n':len(zz)}
        for c in ('potencia','rpe','hr','respiracao','smo2','thb','dfa1'):
            zonas[z][c]=_stat([i.get(c) for i in zz])
        zonas[z]['delta_rpe_consecutivo']={'media':0.5,'mediana':0.5,'n':max(len(zz)-1,0)}
    curva=lambda campo: {
        'regressao_global': _reg(0.02,3.1,len(ivs)),
        'regressao_d1': None if sem_rpe_d1 else _reg(0.018,3.0,4),
        'regressao_d2': _reg(0.025,2.7,4)}
    curvas={'rpe_potencia':curva('potencia'),'rpe_hr':curva('hr'),'rpe_rf':curva('respiracao'),
            'rpe_smo2':curva('smo2'),'rpe_dfa1':curva('dfa1')}
    return {
      'intervalos': ivs,
      'zonas': zonas,
      'curvas': curvas,
      'bp': {'bp1':{'watts':b1,'hr_interpolado':151.2,'respiracao_interpolada':30.4,'smo2_interpolada':59.4,'dfa1_interpolado':0.87,'nota':'x'},
             'bp2':{'watts':b2,'hr_interpolado':None,'respiracao_interpolada':None,'smo2_interpolada':None,'dfa1_interpolado':None,'nota':'x'}},
      'hrvt': {'HRVT1c':{'ok':True,'alpha_alvo':0.75,'alpha_label':'individualizado','watts':h1c,'heartrate':149.1},
               'HRVT1s':{'ok':True,'alpha_alvo':0.75,'alpha_label':'0.75','watts':h1s,'heartrate':154.7},
               'HRVT2': {'ok': h2 is not None,'alpha_alvo':0.5,'alpha_label':'0.50','watts':h2,'heartrate':169.1 if h2 else None}},
      'comparacao_bp_hrvt': {
        'bp1_vs_HRVT1c': {'delta_w': round(b1-h1c,1),'delta_w_pct':1.2,'delta_hr':2.0,'delta_hr_pct':1.3,'nota':'x'},
        'bp1_vs_HRVT1s': {'delta_w': round(b1-h1s,1),'delta_w_pct':-2.1,'delta_hr':None,'delta_hr_pct':None,'nota':'x'},
        'bp2_vs_HRVT2':  {'delta_w': (round(b2-h2,1) if h2 else None),'delta_w_pct':None,'delta_hr':None,'delta_hr_pct':None,'nota':'x'}},
      'day1_vs_day2': {z:{'d1':{'n':1,'rpe':_stat([3]),'potencia':_stat([b1]),'hr':_stat([141]),'smo2':_stat([61])},
                          'd2':{'n':1,'rpe':_stat([4]),'potencia':_stat([b1+2]),'hr':_stat([142]),'smo2':_stat([60])}} for z in ('Z1','Z2','Z3')},
      'meta': {'n_d1':len([i for i in ivs if i['sessao']=='d1']),'n_d2':len([i for i in ivs if i['sessao']=='d2']),
               'n_total':len(ivs),'bp1_alvo_w':b1,'bp2_alvo_w':b2},
      'limitacoes': ['Day1: apenas 2 intervalos com RPE — regressão exploratória.','HRVT2: não disponível nesta sessão (DFA-α1 insuficiente ou ausente).'] if h2 is None else ['Day2: apenas 2 intervalos com RPE — regressão exploratória.'],
    }

REAL = {
 'BIKE': real_shape(188.6, 235.5, 196.0, 205.0, 240.0),
 'ROW':  real_shape(150.0, 200.0, 158.0, 166.0, None),
 'SKI':  real_shape(170.0, 215.0, 180.0, 190.0, 220.0),
 'BIKE_SEM_RPE_D1': real_shape(188.6, 235.5, 196.0, 205.0, 240.0, sem_rpe_d1=True),
}
