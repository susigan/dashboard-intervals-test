"""Tabs do dashboard.

Para adicionar uma tab nova:
1. criar tabs/tab_xxx.py com SLUG, render() e api_data()
2. registar aqui no import e no __all__
3. registar em tabs/base.py -> TABS  (barra de navegacao)
4. registar em app.py -> uma rota de pagina e uma de API
"""

from tabs import (tab_volume, tab_atividades, tab_detalhe,
                  tab_recordes, tab_pmc, tab_corporal)

__all__ = ['tab_volume', 'tab_atividades', 'tab_detalhe',
           'tab_recordes', 'tab_pmc', 'tab_corporal']
