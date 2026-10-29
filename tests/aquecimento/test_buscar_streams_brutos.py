import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'utils'))

import aquecimento_hrr as hrr  # noqa: E402


class BuscarStreamsBrutosTest(unittest.TestCase):
    def test_pede_include_defaults_e_monta_duplicados(self):
        chamadas = []

        def icu_get(path, params=None, timeout=60):
            chamadas.append((path, params))
            return ([
                {'type': 'heartrate', 'allNull': True, 'data': []},
                {'type': 'heartrate', 'data': [100, 110, 120]},
                {'type': 'watts', 'data': [200, 210, 220]},
            ], None)

        streams, nomes, err = hrr.buscar_streams_brutos(icu_get, 'i1')
        self.assertIsNone(err)
        self.assertEqual(chamadas, [('/activity/i1/streams', {'includeDefaults': 'true'})])
        self.assertEqual(streams['heartrate'], [100, 110, 120])
        self.assertIn('watts', streams)
        self.assertEqual(nomes, ['heartrate', 'watts'])

    def test_erro_da_api_propaga(self):
        streams, nomes, err = hrr.buscar_streams_brutos(lambda p, params=None, timeout=60: (None, 'boom'), 'i2')
        self.assertIsNone(streams)
        self.assertEqual(nomes, [])
        self.assertEqual(err, 'boom')

    def test_sem_fc_devolve_dict_vazio_de_fc(self):
        streams, _, err = hrr.buscar_streams_brutos(
            lambda p, params=None, timeout=60: ([{'type': 'watts', 'data': [1, 2]}], None), 'i3')
        self.assertIsNone(err)
        self.assertNotIn('heartrate', streams)


if __name__ == '__main__':
    unittest.main()
