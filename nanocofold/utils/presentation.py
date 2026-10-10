"""Portable presentation metadata; native coordinates and internal receipts stay exact."""
from __future__ import annotations

import json
import re
from pathlib import Path

# A slash alone is not evidence of a host path: it also occurs in base64,
# chemical notation, integrity hashes, and ordinary multilingual prose.
_HOST_ROOT = r'/(?:home|Users|tmp|var|private|mnt|media|Volumes|opt|usr|etc|srv|scratch|work|workspace|data|root|run|proc|dev|Applications|Library|System)(?=/|$)'
_WINDOWS_ROOT = r'(?:[A-Za-z]:[\\/]|\\\\(?:[?.]\\)?)'
_HOST_START = re.compile(r'^(?:' + _HOST_ROOT + '|' + _WINDOWS_ROOT + ')')
_BOUNDARY = r'''(?<![^\s"'=:(\[{])'''
_QUOTED_PATH = re.compile(r'''(["'])((?:''' + _HOST_ROOT + '|' + _WINDOWS_ROOT + r''')[^\r\n]*?)\1''')
_TOKEN_PATH = re.compile(_BOUNDARY + '(?:' + _HOST_ROOT + '|' + _WINDOWS_ROOT + r''')[^\s"'<>()[\]{};,]*''')
_URL = re.compile(r'''((?:https?|ftp|s3|gs)://[^\s"'<>]+)''')
_BASE64 = re.compile(r'[A-Za-z0-9+/]{64,}={0,2}')
_INTEGRITY = re.compile(r'sha(?:256|384|512)-[A-Za-z0-9+/=]+')
_OPAQUE_FIELDS = frozenset({'content', 'smiles', 'canonical_smiles', 'isomeric_smiles',
                            'integrity', 'sha256', 'sha384', 'sha512'})
_PATH_FIELDS = frozenset({'path', 'paths', 'root', 'source_root', 'workspace', 'cwd',
                          'env', 'outdir', 'directory', 'destination', 'file', 'filename'})


def _path_field(field):
    return field in _PATH_FIELDS or field.endswith(('_path', '_paths', '_dir', '_dirs', '_root', '_file', '_files'))


def _portable_path(value, root):
    if value.startswith('/') and root is not None:
        # Lexical only: publication must not follow arbitrary filesystem paths.
        try:
            relative = Path(value).relative_to(Path(root).absolute())
            if '..' not in relative.parts:
                return relative.as_posix() or '.'
        except ValueError:
            pass
    return '[local path]'


def _portable(value, root, field=''):
    if field in _OPAQUE_FIELDS:
        return value
    if isinstance(value, dict):
        encoded = isinstance(value.get('encoding'), str) and 'base64' in value['encoding'].lower()
        return {key: item if key == 'data' and encoded else _portable(item, root, str(key).lower())
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_portable(item, root, field) for item in value]
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            pass
        else:
            return json.dumps(_portable(parsed, root), ensure_ascii=False)
    # Opaque data also appears in older/untyped metadata. Do not decode or
    # rewrite it; a host-root or an explicit path field still identifies paths.
    if _INTEGRITY.fullmatch(value):
        return value
    if not _path_field(field) and not _HOST_START.match(value) and _BASE64.fullmatch(value):
        return value
    url_parts = _URL.split(value)
    if len(url_parts) > 1:
        return ''.join(part if index % 2 else _portable(part, root, field)
                       for index, part in enumerate(url_parts))
    if _HOST_START.match(value) or (value.startswith('/') and _path_field(field)):
        return _portable_path(value, root)
    # A supplied run root identifies custom mount points without guessing that
    # every standalone slash token is a pathname.
    if value.startswith('/') and root is not None:
        relative = _portable_path(value, root)
        if relative != '[local path]':
            return relative
    value = _QUOTED_PATH.sub(lambda match: match[1] + _portable_path(match[2], root) + match[1], value)
    return _TOKEN_PATH.sub(lambda match: _portable_path(match[0], root), value)


def portable_metadata(value, *, root: Path | None = None):
    """Copy metadata while preserving scientific strings and native payloads.

    Explicit pathname fields and known host roots are made relative to ``root``
    or withheld. Free text uses token boundaries, never arbitrary slash matches.
    Native ``content``, encoded arrays, SMILES and integrity fields are opaque.
    Raw archives and internal runtime records do not use this presentation copy.
    """
    return _portable(value, root)
