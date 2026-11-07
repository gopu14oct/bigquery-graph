| case | kind | expected tables | docparse cells | unstructured cells | docparse tables found | unstructured tables found |
|---|---|---|---|---|---|---|
| us030-lattice | ruled | 1 | 1.000 | 0.000 | 1 pred / 1 matched | 0 pred / 0 matched |
| twotables-lattice | ruled | 2 | 0.907 | 0.000 | 2 pred / 2 matched | 0 pred / 0 matched |
| rotated-cw-lattice | ruled, rotated 90 | 1 | 0.959 | 0.000 | 1 pred / 1 matched | 0 pred / 0 matched |
| rotated-acw-lattice | ruled, rotated 90 | 1 | 0.959 | 0.000 | 1 pred / 1 matched | 0 pred / 0 matched |
| arabic-lattice | ruled, Arabic | 1 | 1.000 | 0.000 | 1 pred / 1 matched | 0 pred / 0 matched |
| health-stream | borderless | 1 | 0.416 | 0.000 | 1 pred / 1 matched | 0 pred / 0 matched |
| rotated-cw-stream | borderless, rotated 90 | 1 | 0.041 | 0.000 | 3 pred / 1 matched | 0 pred / 0 matched |
| rotated-acw-stream | borderless, rotated 90 | 1 | 0.041 | 0.000 | 3 pred / 1 matched | 0 pred / 0 matched |
| census-two-stream | borderless, two tables | 2 | 0.003 | 0.000 | 2 pred / 1 matched | 0 pred / 0 matched |

| parser | mean cell accuracy over all expected tables |
|---|---|
| docparse | 0.567 |
| unstructured | 0.000 |
