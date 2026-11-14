# Compare 100 variants with four engines

Download the [configuration](../examples/mature_100_four_engines.yaml) and update NanoCoFold before using it. The example inputs are IL-6 and an anti-IL-6 VHH. Replace `target.fasta` and `mature.wt_fasta` for your own proteins.

This configuration generates **single amino-acid substitutions in the CDRs**, then uses ESM pre-screening to retain up to 100 variants. It removes the small starter's three-variant cap. With 100 variants and one WT control, ColabFold, Boltz-2, Chai-1 and OpenDDE request **404 structures** in total. Fewer available variants or engine failures reduce the completed count. Final eligibility and selection are separate outcomes.

## Run

Finish active analyses before updating. Activate the NanoCoFold environment and run from the repository folder:

```bash
nanocofold run --config examples/mature_100_four_engines.yaml
```

The default MSA source is `cache`. Exact MSAs are required for the target, WT and each evaluated variant. Newly generated variants may not be cached. Only if you consent to sending the sequences to the public MSA service, use:

```bash
nanocofold run --config examples/mature_100_four_engines.yaml \
  --set screen.colabfold.msa_source=public_api \
  --set screen.allow_public_msa_server=true
```

For local searches, an administrator must first register a local MSA database. Then use `screen.colabfold.msa_source=local_mmseqs2`. All four prediction engines, ESM and the CDR tools must be installed. Preparing this configuration does not launch computation.

## Use the browser

Choose nanobody maturation and import the configuration using the advanced YAML import. Review the 100-variant limit, WT inclusion, four engines, one sample per engine and seed 42. For public MSA searches, approve sequence transfer once. The same account reuses approval for later analyses and batches; revoke it in Environment & setup.

## Review results

After completion, open Explore results and check the successful and failed models and metrics for each engine. Engine scores are not combined. Equal recycling counts do not mean equivalent computations across engines. Four-engine comparisons must meet the ColabFold and OpenDDE input limit of 2,000 total residues.

For **two or more substitutions per variant**, choose random CDR multi-residue substitution in the browser instead of this single-substitution configuration. Set 100 variants and the minimum and maximum substitution counts. You can restrict substitutions to CDR3 or specified CDR intervals.
