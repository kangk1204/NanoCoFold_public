"""A cache hit may reuse only a successful, unchanged full content verification."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
import io
import json
import os
import tarfile
import time

import pytest

from nanocofold import install


@pytest.fixture(autouse=True)
def isolated_verification_cache(monkeypatch):
    monkeypatch.setattr(install, '_BOLTZ_MOLECULE_CACHE', OrderedDict())


def molecule_cache(tmp_path, name='cache'):
    cache = tmp_path / name
    (cache / 'mols/nested').mkdir(parents=True)
    rows = {'ALA.pkl': b'first reference molecule', 'nested/GLY.pkl': b'second reference molecule'}
    with tarfile.open(cache / 'mols.tar', 'w') as archive:
        for relative, payload in rows.items():
            member = tarfile.TarInfo('mols/' + relative)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
            (cache / 'mols' / relative).write_bytes(payload)
    return cache


def count_full_checks(monkeypatch):
    original = install._verify_boltz_molecule_contents
    calls = []
    def verify(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(install, '_verify_boltz_molecule_contents', verify)
    return calls


def test_unchanged_readonly_verification_reuses_full_success_without_writing_index(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    assert install._verify_boltz_molecules(cache, write_index=False) == 2
    assert install._verify_boltz_molecules(cache, write_index=False) == 2
    assert len(calls) == 1
    assert not (cache / '.nanocofold-molecules.json').exists()


def test_index_publication_remains_full_even_when_readonly_cache_exists(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    install._verify_boltz_molecules(cache, write_index=False)
    assert install._verify_boltz_molecules(cache) == 2
    first = json.loads((cache / '.nanocofold-molecules.json').read_text())
    assert len(first['files']) == 2
    assert install._verify_boltz_molecules(cache) == 2
    assert len(calls) == 3
    assert json.loads((cache / '.nanocofold-molecules.json').read_text()) == first


@pytest.mark.parametrize('mutation', ['same_size_restore_mtime', 'add', 'delete', 'rename', 'replace', 'file_symlink', 'directory_rename'])
def test_tree_changes_cannot_reuse_previously_valid_count(tmp_path, monkeypatch, mutation):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    install._verify_boltz_molecules(cache, write_index=False)
    file = cache / 'mols/ALA.pkl'
    old = file.stat()
    time.sleep(.002)
    if mutation == 'same_size_restore_mtime':
        file.write_bytes(b'X' * old.st_size)
        os.utime(file, ns=(old.st_atime_ns, old.st_mtime_ns))
        assert file.stat().st_mtime_ns == old.st_mtime_ns
        assert file.stat().st_ctime_ns != old.st_ctime_ns
    elif mutation == 'add':
        (cache / 'mols/extra.pkl').write_bytes(b'unexpected')
    elif mutation == 'delete':
        file.unlink()
    elif mutation == 'rename':
        file.rename(file.with_name('renamed.pkl'))
    elif mutation == 'replace':
        replacement = file.with_name('replacement.pkl')
        replacement.write_bytes(b'Y' * old.st_size)
        os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        replacement.replace(file)
    elif mutation == 'file_symlink':
        reference = tmp_path / 'external-reference'
        reference.write_bytes(file.read_bytes())
        file.unlink()
        file.symlink_to(reference)
    else:
        (cache / 'mols/nested').rename(cache / 'mols/moved')
    with pytest.raises((ValueError, OSError)):
        install._verify_boltz_molecules(cache, write_index=False)
    assert len(calls) == 2
    # A failed state is never added to the successful-state memo.
    assert len(install._BOLTZ_MOLECULE_CACHE) == 1


def test_permission_metadata_change_invalidates_even_when_bytes_unchanged(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    install._verify_boltz_molecules(cache, write_index=False)
    file = cache / 'mols/ALA.pkl'
    before = file.read_bytes()
    file.chmod(0o640)
    assert install._verify_boltz_molecules(cache, write_index=False) == 2
    assert len(calls) == 2 and file.read_bytes() == before


def test_new_empty_directory_invalidates_without_changing_original_guard(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    install._verify_boltz_molecules(cache, write_index=False)
    (cache / 'mols/empty').mkdir()
    assert install._verify_boltz_molecules(cache, write_index=False) == 2
    assert len(calls) == 2


@pytest.mark.parametrize('alias', ['archive', 'root', 'extra_directory'])
def test_aliases_stay_on_full_validation_path(tmp_path, monkeypatch, alias):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    if alias == 'archive':
        archive = cache / 'mols.tar'
        moved = tmp_path / 'external.tar'
        archive.rename(moved)
        archive.symlink_to(moved)
    elif alias == 'root':
        root = cache / 'mols'
        moved = tmp_path / 'external-mols'
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
    else:
        (cache / 'mols/alias').symlink_to(cache / 'mols/nested', target_is_directory=True)
    assert install._verify_boltz_molecules(cache, write_index=False) == 2
    assert install._verify_boltz_molecules(cache, write_index=False) == 2
    assert len(calls) == 2 and not install._BOLTZ_MOLECULE_CACHE


def test_archive_replacement_invalidates_even_when_content_matches(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    install._verify_boltz_molecules(cache, write_index=False)
    archive = cache / 'mols.tar'
    old = archive.stat()
    replacement = tmp_path / 'replacement.tar'
    replacement.write_bytes(archive.read_bytes())
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    replacement.replace(archive)
    assert install._verify_boltz_molecules(cache, write_index=False) == 2
    assert len(calls) == 2


def test_changed_archive_content_fails_without_remembering_failure(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    install._verify_boltz_molecules(cache, write_index=False)
    archive = cache / 'mols.tar'
    old = archive.stat()
    with tarfile.open(archive, 'w') as target:
        member = tarfile.TarInfo('mols/ALA.pkl')
        payload = b'changed reference molecule'
        member.size = len(payload)
        target.addfile(member, io.BytesIO(payload))
    os.utime(archive, ns=(old.st_atime_ns, old.st_mtime_ns))
    with pytest.raises(ValueError, match='해시'):
        install._verify_boltz_molecules(cache, write_index=False)
    assert len(calls) == 2 and len(install._BOLTZ_MOLECULE_CACHE) == 1


def test_untrusted_index_cannot_admit_corrupt_contents(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    install._verify_boltz_molecules(cache, write_index=False)
    (cache / 'mols/ALA.pkl').write_bytes(b'corruption')
    (cache / '.nanocofold-molecules.json').write_text(json.dumps({'files': {}, 'verified_tar_sha256': '0' * 64}))
    with pytest.raises(ValueError, match='해시'):
        install._verify_boltz_molecules(cache, write_index=False)


def test_content_changed_after_full_check_is_not_cached(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    original = install._verify_boltz_molecule_contents
    file = cache / 'mols/ALA.pkl'
    def raced(*args, **kwargs):
        count = original(*args, **kwargs)
        file.write_bytes(b'changed after digest')
        return count
    monkeypatch.setattr(install, '_verify_boltz_molecule_contents', raced)
    with pytest.raises(ValueError, match='검증 중 변경'):
        install._verify_boltz_molecules(cache, write_index=False)
    assert not install._BOLTZ_MOLECULE_CACHE


def test_change_during_cache_hit_metadata_scan_cannot_return_success(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    install._verify_boltz_molecules(cache, write_index=False)
    original = install._boltz_molecule_inventory
    calls = 0
    def raced(*args):
        nonlocal calls
        signature = original(*args)
        calls += 1
        if calls == 1:
            (cache / 'mols/ALA.pkl').write_bytes(b'changed during cache-hit check')
        return signature
    monkeypatch.setattr(install, '_boltz_molecule_inventory', raced)
    with pytest.raises(ValueError, match='검증 중 변경'):
        install._verify_boltz_molecules(cache, write_index=False)


def test_concurrent_readers_publish_one_successful_verification(tmp_path, monkeypatch):
    cache = molecule_cache(tmp_path)
    calls = count_full_checks(monkeypatch)
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(lambda _: install._verify_boltz_molecules(cache, write_index=False), range(8)))
    assert result == [2] * 8 and len(calls) == 1


def test_success_cache_is_bounded_and_evicted_root_is_reverified(tmp_path, monkeypatch):
    calls = count_full_checks(monkeypatch)
    roots = [molecule_cache(tmp_path, str(i)) for i in range(install._BOLTZ_MOLECULE_CACHE_LIMIT + 2)]
    for root in roots:
        assert install._verify_boltz_molecules(root, write_index=False) == 2
    assert len(install._BOLTZ_MOLECULE_CACHE) == install._BOLTZ_MOLECULE_CACHE_LIMIT
    assert install._verify_boltz_molecules(roots[0], write_index=False) == 2
    assert len(calls) == len(roots) + 1
