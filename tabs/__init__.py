"""Tabs do dashboard. Para adicionar uma tab nova:

1. criar tabs/tab_xxx.py com SLUG, ROUTE, render() e api_data()
2. registar em tabs/base.py -> TABS
3. registar em app.py -> TAB_MODULES
"""

from tabs import tab_volume, tab_atividades, tab_detalhe

__all__ = ['tab_volume', 'tab_atividades', 'tab_detalhe']
