# Selection review: EGFR–7D12

Reviewed 2026-11-10. Scope: the saved 4KRL experiment, comprising 200 CDR3 variants with four substitutions each and one WT control. No predictions or selection calculations were repeated.

## Conclusion

The current criteria are suitable for exploratory prioritization and manual review. This experiment does not establish a calibrated binding classifier for this target. It also does not justify lowering the thresholds or changing recorded decisions.

All 201 models completed. Six variants pass the structural criteria but remain on sequence review hold; 194 also fail structural criteria. Zero selected is a valid recorded outcome. Selection reflects both the engine predictions and the platform's configured rules.

## Criteria retained

| Measure | Recorded requirement | Interpretation |
|---|---:|---|
| ipSAE | ≥0.6 | PAE-based interface confidence, not a binding probability calibrated here. |
| Chain-pair ipTM | ≥0.7 | Target–VHH confidence, distinct from whole-complex ipTM. |
| Interface pLDDT | ≥70 | Local structural confidence on the platform's 0–100 scale. |
| Atomic clashes | 0 | Geometry check using the recorded clash definition. |
| Sequence review categories | At most 1 | Excess categories hold selection for review; they do not establish experimental expression or stability failure. |
| Required hotspot contact | Off | Contacts remain diagnostic; no new selection rule was introduced. |

ipSAE depends on the PAE cutoff and effective residue counts. Treating a single cutoff as a universal binding boundary across targets and constructs goes beyond its definition. [Official ipSAE implementation](https://github.com/DunbrackLab/IPSAE)

Boltz reports structural confidence separately from affinity. Its documented affinity feature supports protein–small-molecule pairs. Nanobody confidence scores here are not converted to KD. [Official Boltz prediction documentation](https://github.com/jwohlwend/boltz/blob/main/docs/prediction.md)

## WT control and shared sequence findings

4KRL is an experimental complex of EGFR domain III and 7D12. Label-chain and author-chain identifiers differ; this comparison explicitly uses author A for EGFR and B for 7D12. [RCSB PDB 4KRL](https://www.rcsb.org/structure/4KRL)

The saved WT prediction has ipSAE 0.518, below the configured threshold, while recovering 37 of 42 native contacts after target alignment. Thus, a known binder can fail this single prediction-and-selection combination. This observation neither establishes that every variant is a nonbinder nor warrants tuning the threshold to 0.518.

All variants retain the WT CDR2 DS motif and a linear hydrophobic window crossing the CDR boundary. The v2 policy records these as sequence review categories. WT and variants receive the same rules; these findings do not measure structural burial, aggregation or degradation. This review found no error requiring a change in the numerical criteria or decision logic.

The six structural passes recover only 0–28.6% of WT native contacts. Higher confidence need not mean recovery of the WT pose. The linked WT structure is comparison-only. Different mutant poses are not classified as errors because experimental mutant structures are unavailable.

## Scope of inference

These 200 related variants share a parent, homolog rows and seed 42. They are not 200 independent target validations. Public target and WT MSAs were acquired once; variant queries replaced the first sequence while retaining WT homologs. There were no independent variant searches. The API did not supply a database revision, so it remains unrecorded.

Any later claim of binding-classification performance requires separate experimental positives and negatives evaluated with prespecified criteria. That is a boundary on performance claims, not a barrier to sharing the present results. This update changes warning translation, provenance links and lossless report storage only. Original scores, ranks, thresholds and results are preserved.
