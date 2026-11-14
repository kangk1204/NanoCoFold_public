"""Keep adapter-owned scientific and transport settings out of free CLI arguments."""
from __future__ import annotations


BOLTZ_MANAGED = frozenset({
    "--use-msa-server", "--msa-server-url", "--msa-server-username", "--msa-server-password",
    "--msa-pairing-strategy", "--cache", "--out-dir", "--output-format", "--override",
    "--seed", "--recycling-steps", "--diffusion-samples", "--write-full-pae",
    "--subsample-msa", "--num-subsampled-msa", "--model",
})
CHAI_MANAGED = frozenset({
    "--fasta", "--out-dir", "--constraint-path", "--msa-directory", "--use-msa-server",
    "--msa-server-url", "--seed", "--num-trunk-recycles", "--num-diffn-timesteps",
    "--num-diffn-samples", "--cache", "--output-dir",
})


def boltz_extra_args(cfg, *, target=False, extra_args=None):
    """Named sampling steps with explicit compatibility for saved extra_args."""
    from ..config import cfg_get
    path = "structure.boltz2.extra_args" if target else "score.boltz2_extra_args"
    key = "structure.boltz2.sampling_steps" if target else "score.boltz2_sampling_steps"
    steps = cfg_get(cfg, key)
    managed = BOLTZ_MANAGED | ({"--sampling-steps"} if steps is not None else set())
    extra = validate_extra_args(cfg_get(cfg, path, []) if extra_args is None else extra_args,
                                setting=path, managed=managed)
    if steps is not None:
        if type(steps) is not int or not 1 <= steps <= 10000:
            raise ValueError(f"{key}: 1–10000 integer required")
        extra.extend(["--sampling_steps", str(steps)])
    return extra


def validate_colabfold_extra_args(values) -> None:
    """The ColabFold adapter supports named settings, not passthrough argv."""
    if values is not None and (not isinstance(values, (list, tuple)) or values):
        raise ValueError("screen.colabfold.extra_args is unsupported; use named managed settings instead")


def validate_extra_args(values, *, setting: str, managed, allow_abbrev: bool = False) -> list[str]:
    """Validate argv without rewriting legitimate engine options or their values.

    Chai's argparse CLI accepts long-option abbreviations. Reject these too,
    as otherwise an abbreviated flag could override consent or frozen inputs.
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
        raise ValueError(f"{setting} extra_args must be a list of CLI strings")
    canonical = {name.replace("_", "-").lower() for name in managed}
    for value in values:
        if "\x00" in value or value == "--":
            raise ValueError(f"{setting} extra_args cannot change the managed command boundary")
        if not value.startswith("--"):
            continue
        option = value.split("=", 1)[0].replace("_", "-").lower()
        if option in canonical or (allow_abbrev and any(name.startswith(option) for name in canonical)):
            raise ValueError(f"{setting} extra_args cannot override managed option {value.split('=', 1)[0]}; use the named configuration setting")
    return list(values)
