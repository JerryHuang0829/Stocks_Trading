# H_a1 Hypothesis Lock Amendment — Consistency Sub-Signal Deprecation

**Date**: 2026-05-10
**Plan reference**: `codex-pro-codex-precious-reef.md` Phase 2 P1-D
**Trigger**: Codex R26 audit findings (Section C3)

## Original H_a1 lock (Phase A1 baseline, 2026-04-17)

`foreign_investor_v2` composite specified 4 sub-signals with weights:

| Sub-signal | Weight | Source spec |
|---|---:|---|
| foreign_cum_ratio | 0.40 | `config/factor_thresholds.yaml :: factor_specific.foreign_investor_v2.weights` |
| persistence | 0.20 | 同上 |
| rank_stability | 0.20 | 同上 |
| consistency | 0.20 | 同上 |

`consistency` definition (`src/features/foreign_investor_v2.py::_compute_symbol_signals`):
> Fraction of last 20 days where BOTH `Foreign_Investor` AND `Investment_Trust` were on the same side (net positive).

## Amendment 2026-05-10

Reset `consistency` weight from 0.20 to **0.0**. Redistribute 0.20 weight:

| Sub-signal | Old | New | Δ |
|---|---:|---:|---:|
| foreign_cum_ratio | 0.40 | **0.50** | +0.10 |
| persistence | 0.20 | **0.25** | +0.05 |
| rank_stability | 0.20 | **0.25** | +0.05 |
| consistency | 0.20 | **0.0** | -0.20 |

## Justification (Codex R26 Section C3 evidence)

Codex R26 ran cross-section diagnostic on all 71 saved periods of the contaminated `foreign_investor_v2_ic.json`:

```
71-period statistics:
  consistency mean        = 0.0385
  consistency zero fraction = 0.7838  (78% of symbols have consistency = 0)
  consistency std         = 0.0943
  persistence std         = 0.1711
```

**Interpretation**: trust_net (投信) is sparse for mid/small-cap symbols (投信 doesn't actively trade them), so `(foreign_net > 0) AND (trust_net > 0)` collapses to 0 for ~78% of cross-section. Result: cross-section variance is concentrated in 22% of symbols, signal-to-noise ratio is roughly half of `persistence` (which doesn't depend on trust). With z-score normalization, consistency contributes mostly noise to the composite.

## Risk to H_a1 hypothesis pre-registration

The sub-signal weight redistribution changes the composite formula post-hoc. This is **not** a clean blind-out-of-sample design. To document the bias risk:

- **Pre-registered formula** (frozen 2026-04-17): 0.40/0.20/0.20/0.20
- **Post-audit formula** (this amendment): 0.50/0.25/0.25/0.0
- **Risk classification**: minor methodology refinement, not factor selection. The 4-sub-signal architecture remains intact; only the weight on a sub-signal documented as low-SNR is set to 0.
- **Bias caveat**: if fresh rerun under new weights produces materially different IC sign or magnitude, that change is partly attributable to weight re-design, not pure data evidence.

## Action items

1. ✅ `src/features/foreign_investor_v2.py::SUBSIGNAL_WEIGHTS` updated (2026-05-10)
2. ✅ `config/factor_thresholds.yaml :: factor_specific.foreign_investor_v2.weights` updated (2026-05-10)
3. ⏳ Phase 3 fresh rerun under new weights pending
4. ⏳ Pre-existing `tests/test_foreign_investor_v2.py::test_foreign_and_trust_alignment_boosts_consistency` updated to assert post-amendment behavior (BOTH ≈ ONEF instead of BOTH > ONEF)

## Decision

Amendment **accepted** under the rationale that:
- (a) consistency was already documented as low-SNR pre-audit (78% sparsity is structural, not contingent)
- (b) the 0.20 weight that consistency was carrying was effectively contributing noise, and removing it improves composite SNR
- (c) the alternative (keeping consistency=0.20 for purity of pre-registration) means accepting known noise in production composite, which fails the "贏 0050 / 贏大盤" goal

This amendment is documented in `reports/factor_ic/_amend/` and referenced from the post-Phase-3 closeout report so future Codex rounds can audit the methodology drift.
