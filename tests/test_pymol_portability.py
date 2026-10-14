"""Portable PyMOL exports; native execution evidence is kept in validation."""
import json
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace

import pytest
from nanocofold.handoff import write_pymol_session_script


@pytest.mark.parametrize("identifier", ["나노바디2", "VHH(A3)", "VHH;delete all", "O'Brien"])
def test_moved_script_resolves_exact_original_coordinate_bytes(tmp_path, monkeypatch, identifier):
    original = tmp_path / "source run"
    source = original / "models" / (identifier + ".pdb")
    source.parent.mkdir(parents=True)
    raw = b"HEADER immutable coordinate\nEND\n"
    source.write_bytes(raw)
    pml = original / "top_candidates.pml"
    write_pymol_session_script([(identifier, str(source), "B")], pml)
    text = pml.read_text()
    assert str(tmp_path) not in text and "models/" in text
    assert "__script__" in text
    moved = tmp_path / "copied run"
    shutil.copytree(original, moved)
    original.rename(tmp_path / "preserved original")
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setitem(sys.modules, "pymol", SimpleNamespace(cmd=SimpleNamespace(load=lambda path, name: calls.append((Path(path), name, Path(path).read_bytes())))))
    namespace = {"__script__": str(moved / pml.name)}
    for code in re.findall(r"(?:^|\n)python\n(.*?)\npython end(?:\n|$)", text, re.S):
        exec(compile(code, "portable-pml", "exec"), namespace)
    mapping = json.loads(next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("# candidate_id_map:")))
    assert calls == [(moved / "models" / source.name, mapping["object"], raw)]


@pytest.mark.parametrize("invalid", ["model\n.pdb", "model\r.pdb", "model\x00.pdb"])
def test_control_characters_in_coordinate_paths_are_rejected(tmp_path, invalid):
    out = tmp_path / "top_candidates.pml"
    with pytest.raises(ValueError, match="줄바꿈·NUL"):
        write_pymol_session_script([("candidate", invalid, "B")], out)
    assert not out.exists()


@pytest.mark.parametrize("identifier", ["own_model", "나노바디2(A3);O'Neil"])
def test_interface_does_not_gain_other_model_atoms_after_translation(tmp_path, identifier):
    """Evaluate the generated selection's object/radius contract on a counterexample."""
    import math
    pml = tmp_path / "interface.pml"
    write_pymol_session_script([(identifier, str(tmp_path / "one.pdb"), "B"),
                               ("other_model", str(tmp_path / "two.pdb"), "B")], pml)
    text = pml.read_text()
    mappings = [json.loads(line.split(": ", 1)[1]) for line in text.splitlines() if line.startswith("# candidate_id_map:")]
    own, other = [m["object"] for m in mappings]
    expression = next(line.split(", ", 1)[1] for line in text.splitlines() if line.startswith("select interface_" + own + ","))
    match = re.fullmatch(r"(\w+) within ([0-9.]+) of (?:\((\w+) and chain (\w+)\)|chain (\w+))", expression)
    assert match, "Unsupported interface selection output"
    source_object, radius, target_object, target_chain, global_chain = match.groups()
    objects = {own: [("A", (0.,0.,0.)), ("B", (3.,0.,0.)), ("B", (30.,0.,0.))],
               other: [("A", (100.,0.,0.)), ("B", (103.,0.,0.))]}
    def displayed_binders():
        targets = [coord for name, atoms in objects.items() for chain, coord in atoms
                   if chain == (target_chain or global_chain) and (target_object is None or name == target_object)]
        return [index for index, (chain, coord) in enumerate(objects[source_object])
                if chain == "B" and any(math.dist(coord, target) <= float(radius) for target in targets)]
    assert displayed_binders() == [1]  # Other target starts far away.
    objects[other] = [(chain, (coord[0]-70.,coord[1],coord[2])) for chain, coord in objects[other]]
    assert displayed_binders() == [1]  # Global chain A would spuriously add binder2.
