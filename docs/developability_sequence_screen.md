# Sequence developability review

[한국어](developability_sequence_screen.ko.md)

Policy: `sequence_liability_screen_v2`. This policy applies to newly computed sequence assessments. Opening a saved result does not reassess it.

## What the screen means

This is a sequence screen for review, not a calibrated prediction of experimental developability. A motif identifies a possible liability; it does not establish degradation, aggregation, expression yield or binding loss. Liability prevalence and structural context matter. The LAP study documents the limits of motif-only screening and the value of context ([LAP, 2024](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1011881)).

TAP uses structural surface descriptors and clinical antibody reference distributions. Its metrics are not equivalent to NanoCoFold's five-residue Kyte–Doolittle sequence windows ([TAP, 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6410772/)). The nanobody-specific TNP study also supports contextual assessment rather than a universal sequence-count cutoff; its predictions are screening evidence, with assay and format limitations ([TNP, 2026](https://pmc.ncbi.nlm.nih.gov/articles/PMC12963540/)). NanoCoFold does not implement or claim calibration against TAP or TNP.

## Category corrections

| Matched motif | v2 category | Previous classification |
| --- | --- | --- |
| NG, NS | Deamidation screen | Unchanged |
| DG, DS | Asp isomerization screen | Incorrectly included under deamidation; DG could count twice |
| DP | Cleavage screen | Included under isomerization |

The same matched site and reaction category is counted once, including when old configuration patterns overlap. Multiple sites in one category still contribute one category to `dev_severe`. Original configured labels, matched motifs, positions and CDR membership are retained in `dev_motif_evidence`. Configured custom matches outside Asn/Asp retain their screen label with `chemistry_assigned=false`; they are not chemical assignments.

`dev_severe` is a compatibility field name. In v2 it counts six review categories: cysteine count, glycosylation motif, CDR deamidation, CDR isomerization, CDR cleavage, and hydrophobic windows overlapping CDRs. It is not a count of experimentally confirmed severe defects. `dev_flags` adds the oxidation, pI and net-charge screens used in relative ranking.

The existing cysteine and glycosylation field names are retained: cysteine counts do not identify unpaired cysteines, and a glycosylation motif does not demonstrate occupancy. Hydrophobic evidence records merged runs of above-threshold linear windows and their CDR overlap. It does not measure solvent-exposed hydrophobic surface.

## Selection behavior

- `screen_pass`: all three CDR regions were assessed and the category count is within the configured limit. Structural and other selection gates still apply.
- `review_required`: the count exceeds the limit. `final_eligible=false` and the reason is `developability_review_required`; this is an automatic-selection hold for review.
- `unassessed`: CDR annotation is incomplete or invalid. This is not a screen with no findings.

The default limit remains `rank.max_dev_severe: 1`. The hydrophobic window remains 5 residues at a mean KD threshold of 1.6; the cysteine count limit remains 2. None of these values was optimized against the current candidates. They are workflow review thresholds, not validated clinical failure boundaries.

A common parent finding is not automatically exempted: its relevance may change with a mutation, conformation or expression format. A variant with a favorable structure prediction still needs a separate developability review. `fallback_to_ineligible` and setting the `dev_flags` ranking weight to zero do not override v2 review holds. Synthesis export and handoff validation also enforce this hold.

Historical results keep their recorded scores, categories, eligibility and files. A new assessment has its own policy and output. Do not combine v1 and v2 category counts as if their definitions were identical. No manual override, WT exemption or experimental approval is introduced by this change.
