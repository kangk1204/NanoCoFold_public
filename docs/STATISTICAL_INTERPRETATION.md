# Reading scores and statistical summaries

[한국어](STATISTICAL_INTERPRETATION.ko.md)

Use NanoCoFold to inspect predicted structures and choose candidates for further
review. Its scores are not calibrated binding probabilities or measurements of
affinity, KD, expression, or biological specificity.

| Recorded result | How to read it |
| --- | --- |
| Prediction complete | A computational stage finished; inspect failures and unevaluated observations separately. |
| Eligible or selected | The recorded software quality and selection rules passed. Experimental binding is a separate endpoint. |
| Composite score | A relative score within its recorded engine and candidate cohort. Adding candidates or changing available metrics can change the score without changing a candidate's raw measurements. |
| Engine agreement or seed range | Computational consistency or variation; seeds, models, residues, and contacts are not independent biological replicates. |
| Full/cleaved specificity comparison | Requires complete observations with verified approved comparison conditions. Unverified historical records remain readable but cannot establish a complete comparison. |

Read the saved ranking policy for each actual engine cohort, including applied
and ignored weights and held observations. Do not combine engine scores into a
global ranking. A partial Boltz re-evaluation gives different candidate subsets
to the two engines because the first-stage scores selected that subset. Separate
engine rankings do not make this a matched engine-performance comparison.

The specificity field `delta_ipsae` is the cleaved-state median minus the
full-state median. It is not the median of paired seed differences. Inspect the
original observations and their conditions before interpreting either quantity.

After specificity reanalysis, distinguish the original prediction settings from
the current assessment criteria. The latter require a verified assessment
receipt bound to the original prediction and exact output tables. A missing or
inconsistent receipt means the current criteria are unverified; the original
thresholds must not be presented as the reanalysis thresholds.

Older benchmark fields called `permutation_pvalue_rank1` and
`permutation_pvalue_all_positives_top_k` describe random-ranking baseline chances.
They do not test an observed rank or AUC. Current observed-rank summaries state
their finite labelled cohort, tied-score boundary, null model, and whether
multiple testing was addressed. Rounded historical tables cannot reconstruct
full-precision original scores. Reference-positive and assumed-decoy labels do
not establish experimentally measured nonbinding or performance on new targets.

For experimental performance claims, define the assay endpoint, constructs,
measured positives and negatives, independent targets or sequence families,
and validation policy before selecting favourable engines or thresholds.
Report experimental denominators and uncertainty separately from model counts.
Raw scores are not p-values; applying a multiple-testing correction to them does
not turn them into evidence of binding.
