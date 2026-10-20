# Chip RTL

| File | Module | What |
|---|---|---|
| `project.v` | `tt_um_kyrylo_nazarevych_bp` | Top level: input registers, global history, the three table predictors, pins |
| `perceptron_704.v` | `bp_perceptron_704` | The perceptron predictor of Tiny Tapeout design #704 |
| `latch_memory.v` | `bp_latch_mem` | 64-byte latch RAM. Behavioural gates in simulators (or with `-DSIM`), SKY130 cells in synthesis |
| `predictors.v` | `bimodal`, `gshare`, `chooser`, `perceptron` | Table predictors; `perceptron` here is an unused single-cycle variant |

These four files are identical to `src/` of [tt-branch-predictor](https://github.com/Kyrylo-Nazarevych/tt-branch-predictor)
at commit `21dad17`, the version hardened for SKY130. Parameters in `project.v`: `BIDX = 6` (64-entry tables),
`GHIST = 8` (history bits for gshare).
