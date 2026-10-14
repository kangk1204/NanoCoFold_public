"""Portable presentation metadata; native coordinates and internal receipts stay exact."""
from __future__ import annotations

import json
import re
from pathlib import Path

# A slash alone is not evidence of a host path: it also occurs in base64,
# chemical notation, integrity hashes, and ordinary multilingual prose.
_HOST_ROOT = r'/(?:home|Users|tmp|var|private|mnt|media|Volumes|opt|usr|etc|srv|scratch|work|workspace|data|root|run|proc|dev|Applications|Library|System|nfs|gpfs)(?=/|$)'
# Forward-slash drive notation needs a directory or filename, so the chain
# label A:/B is not mistaken for a Windows path. Backslashes are unambiguous.
_WINDOWS_ROOT = r'''(?:[A-Za-z]:\\|[A-Za-z]:/(?=[^\s/"'<>()[\]{};,`|:]+(?:/|\.))|\\\\(?:[?.]\\(?:[A-Za-z]:[\\/])?)?)'''
_FILE_ROOT = r'file://(?=\S)'
_PATH_ROOT = '(?:' + _HOST_ROOT + '|' + _WINDOWS_ROOT + '|' + _FILE_ROOT + ')'
_HOST_START = re.compile('^' + _PATH_ROOT)
_ABSOLUTE_START = re.compile(r'^(?:/|[A-Za-z]:[\\/]|\\\\)')
_BOUNDARY = r'''(?<![^\s"'=:(\[{,>`;|\u1100-\u11ff\u3130-\u318f\uac00-\ud7af])'''
_PATH_TAIL = r'''[^\s"'<>()[\]{};,`|:]*'''
_URL = re.compile(r'''((?:https?|ftp|s3|gs)://[^\s"'<>]+)''')
_BASE64 = re.compile(r'[A-Za-z0-9+/]{64,}={0,2}')
_INTEGRITY = re.compile(r'sha(?:256|384|512)-[A-Za-z0-9+/=]+')
_GPU_UUID = re.compile(r'\b(?:GPU|MIG)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', re.I)
_OPAQUE_FIELDS = frozenset({'content', 'raw_content', 'smiles', 'canonical_smiles', 'isomeric_smiles',
                            'integrity', 'sha256', 'sha384', 'sha512'})
_PATH_FIELDS = frozenset({'path', 'paths', 'root', 'source_root', 'workspace', 'cwd',
                          'env', 'outdir', 'directory', 'destination', 'file', 'filename'})


def _path_field(field):
    return field in _PATH_FIELDS or field.endswith(('_path', '_paths', '_dir', '_dirs', '_root', '_file', '_files', '_home'))


def _portable_path(value, root):
    if value.startswith('file://localhost/'):
        value = value[len('file://localhost'):]
    elif value.startswith('file:///'):
        value = value[len('file://'):]
    if value.startswith('/') and root is not None:
        # Lexical only: publication must not follow arbitrary filesystem paths.
        try:
            relative = Path(value).relative_to(Path(root).absolute())
            if '..' not in relative.parts:
                return relative.as_posix() or '.'
        except ValueError:
            pass
    return '[local path]'


def _path_patterns(root):
    path_root = _PATH_ROOT
    if root is not None:
        prefix = str(Path(root).absolute()).rstrip('/')
        if prefix:
            # A caller-supplied root is explicit evidence, including custom
            # mounts and spaces; it must not match a neighboring root name.
            path_root = '(?:' + re.escape(prefix) + r'(?=/|$)|' + path_root + ')'
    quoted = re.compile(r'''(["'])(''' + path_root + r'''[^\r\n]*?)\1''')
    continuation = r'''(?:[ \t]+[^\s"\'<>()[\]{};,`|:]+(?=/)''' + _PATH_TAIL + ")*"
    token = re.compile(_BOUNDARY + path_root + _PATH_TAIL + continuation)
    return quoted, token


def _portable(value, root, patterns, field=''):
    if field in _OPAQUE_FIELDS:
        return value
    if isinstance(value, dict):
        encoded = isinstance(value.get('encoding'), str) and 'base64' in value['encoding'].lower()
        return {key: item if key == 'data' and encoded else _portable(item, root, patterns, str(key).lower())
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_portable(item, root, patterns, field) for item in value]
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            pass
        else:
            result = _portable(parsed, root, patterns)
            return value if result == parsed else json.dumps(result, ensure_ascii=False)
    # Opaque data also appears in older/untyped metadata. Do not decode or
    # rewrite it. Explicit path fields still identify paths; ambiguous untyped
    # base64-shaped strings are preserved to protect native scientific data.
    if _INTEGRITY.fullmatch(value):
        return value
    if not _path_field(field) and _BASE64.fullmatch(value):
        return value
    # UUID-based device selection identifies the private host hardware. Keep
    # numerical CUDA ordinals and ordinary UUIDs, and only mask metadata copies;
    # opaque coordinates, hashes, encoded scientific data and internal runtime
    # receipts above remain exact.
    value = _GPU_UUID.sub('[GPU UUID]', value)
    url_parts = _URL.split(value)
    if len(url_parts) > 1:
        return ''.join(part if index % 2 else _portable(part, root, patterns, field)
                       for index, part in enumerate(url_parts))
    if _path_field(field) and _ABSOLUTE_START.match(value):
        diagnostic = re.match(r'(.+?)(:\s.*)', value, re.S)
        if diagnostic:
            return _portable_path(diagnostic[1], root) + diagnostic[2]
        if not re.search(r'[\r\n]', value):
            return _portable_path(value, root)
    # Free text is always tokenized: a path at the start of an error message
    # must not consume the explanation that follows it.
    quoted, token = patterns
    value = quoted.sub(lambda match: match[1] + _portable_path(match[2], root) + match[1], value)
    return token.sub(lambda match: _portable_path(match[0], root), value)


def portable_metadata(value, *, root: Path | None = None):
    """Copy metadata while preserving scientific strings and native payloads.

    Explicit pathname fields and known host roots are made relative to ``root``
    or withheld. Free text uses token boundaries, never arbitrary slash matches.
    Native ``content``, encoded arrays, SMILES and integrity fields are opaque.
    Host GPU UUIDs are withheld; numerical device ordinals remain recorded.
    Raw archives and internal runtime records do not use this presentation copy.
    """
    return _portable(value, root, _path_patterns(root))
