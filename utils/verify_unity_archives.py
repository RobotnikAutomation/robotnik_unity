#!/usr/bin/env python3
"""Validate Unity benchmark archives and their build provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile


BINARY_NAME = 'PI_simulation_Unity_Robotnik.x86_64'
EXPECTED_UNITY_VERSION = '6000.1.14f1'
EXPECTED_WORLDS = {'empty_world', 'simple_world'}
EXPECTED_ARCHIVES = {
    'unity_simulation.tar.gz': {
        'world': 'simple_world',
        'sha256': '481781c6381c80c04229960dff6bc32dbe4e6ae070e9d6749ae51138e29e762a',
    },
    'unity_simulation_only.tar.gz': {
        'world': 'empty_world',
        'sha256': '2385e388b9b7e24002d3fd9985b8ef84958017298cb464d8eebe85c9ed6b5c01',
    },
}


def _sha256_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 without loading a Player archive in memory."""
    with path.open('rb') as stream:
        return _sha256_stream(stream)


def _validated_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Return members only when every entry is safe for local extraction."""
    members = archive.getmembers()
    seen: set[PurePosixPath] = set()
    for member in members:
        member_path = PurePosixPath(member.name)
        if (
            member_path.is_absolute()
            or '..' in member_path.parts
            or '\\' in member.name
        ):
            raise ValueError(f'{archive.name}: unsafe archive path {member.name!r}')
        if member_path in seen:
            raise ValueError(f'{archive.name}: duplicate archive path {member.name!r}')
        seen.add(member_path)
        if not (member.isdir() or member.isfile()):
            raise ValueError(
                f'{archive.name}: unsupported archive entry {member.name!r} '
                f'(type {member.type!r})'
            )
    return members


def safe_extract_archive(path: Path, destination: Path) -> None:
    """Extract regular files/directories without following archive links."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode='r:gz') as archive:
        members = _validated_members(archive)
        for member in members:
            relative = PurePosixPath(member.name)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, member.mode & 0o777)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f'{path}: cannot read {member.name}')
            with source, target.open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(target, member.mode & 0o777)


def _single_member(archive: tarfile.TarFile, basename: str) -> tarfile.TarInfo:
    matches = [
        member
        for member in archive.getmembers()
        if member.isfile() and PurePosixPath(member.name).name == basename
    ]
    if len(matches) != 1:
        raise ValueError(
            f'{archive.name}: expected one {basename}, found {len(matches)}'
        )
    return matches[0]


def verify_archive(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_world: str | None = None,
) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f'archive does not exist: {path}')
    archive_sha256 = sha256_file(path)
    if expected_sha256 is not None and archive_sha256 != expected_sha256:
        raise ValueError(
            f'{path}: archive SHA-256 mismatch: expected {expected_sha256}, '
            f'got {archive_sha256}'
        )
    with tarfile.open(path, mode='r:gz') as archive:
        _validated_members(archive)

        binary = _single_member(archive, BINARY_NAME)
        if binary.mode & 0o111 == 0:
            raise ValueError(f'{path}: {BINARY_NAME} is not executable')

        assembly = _single_member(archive, 'Assembly-CSharp.dll')
        assembly_stream = archive.extractfile(assembly)
        if assembly_stream is None:
            raise ValueError(f'{path}: cannot read {assembly.name}')
        assembly_sha256 = _sha256_stream(assembly_stream)

        metadata_member = _single_member(archive, 'BUILD_METADATA.json')
        metadata_stream = archive.extractfile(metadata_member)
        if metadata_stream is None:
            raise ValueError(f'{path}: cannot read {metadata_member.name}')
        metadata = json.load(metadata_stream)

    if not isinstance(metadata, dict):
        raise ValueError(f'{path}: build metadata must be a JSON object')
    required = {
        'schema_version',
        'project_name',
        'source_repository_commit',
        'source_tree_sha256',
        'source_ref',
        'unity_version',
        'build_date_utc',
        'world',
        'scene',
        'assembly_csharp_sha256',
        'binary',
    }
    missing = sorted(required.difference(metadata))
    if missing:
        raise ValueError(f'{path}: metadata is missing {", ".join(missing)}')
    text_fields = required.difference({'schema_version'})
    invalid_text = sorted(
        field
        for field in text_fields
        if not isinstance(metadata[field], str) or not metadata[field]
    )
    if invalid_text:
        raise ValueError(
            f'{path}: metadata fields must be non-empty strings: '
            + ', '.join(invalid_text)
        )
    if metadata['schema_version'] != 1:
        raise ValueError(f'{path}: unsupported metadata schema {metadata["schema_version"]!r}')
    if metadata['binary'] != BINARY_NAME:
        raise ValueError(f'{path}: metadata names an unexpected Player binary')
    if metadata['unity_version'] != EXPECTED_UNITY_VERSION:
        raise ValueError(
            f'{path}: expected Unity {EXPECTED_UNITY_VERSION}, '
            f'got {metadata["unity_version"]}'
        )
    if metadata['world'] not in EXPECTED_WORLDS:
        raise ValueError(f'{path}: unknown world {metadata["world"]!r}')
    if expected_world is not None and metadata['world'] != expected_world:
        raise ValueError(
            f'{path}: expected world {expected_world!r}, got {metadata["world"]!r}'
        )
    expected_source_ref = (
        f'{metadata["source_repository_commit"]}'
        f'+tree-sha256:{metadata["source_tree_sha256"]}'
    )
    if metadata['source_ref'] != expected_source_ref:
        raise ValueError(f'{path}: source_ref does not match its commit/tree metadata')
    if metadata['assembly_csharp_sha256'] != assembly_sha256:
        raise ValueError(f'{path}: Assembly-CSharp.dll hash does not match metadata')

    return {
        'path': str(path),
        'archive_sha256': archive_sha256,
        'assembly_sha256': assembly_sha256,
        'source_ref': metadata['source_ref'],
        'world': metadata['world'],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', nargs='+', type=Path)
    args = parser.parse_args()

    try:
        results = []
        for path in args.archives:
            resolved = path.resolve()
            expected = EXPECTED_ARCHIVES.get(resolved.name, {})
            results.append(
                verify_archive(
                    resolved,
                    expected_sha256=expected.get('sha256'),
                    expected_world=expected.get('world'),
                )
            )
        worlds = {result['world'] for result in results}
        if len(results) == 2 and worlds != EXPECTED_WORLDS:
            raise ValueError(
                f'expected worlds {sorted(EXPECTED_WORLDS)}, got {sorted(worlds)}'
            )

        source_refs = {result['source_ref'] for result in results}
        if len(source_refs) != 1:
            raise ValueError('archives were not built from the same source revision')

        assembly_hashes = {result['assembly_sha256'] for result in results}
        if len(assembly_hashes) != 1:
            raise ValueError('archives do not contain the same Assembly-CSharp.dll')
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        tarfile.TarError,
    ) as exc:
        print(f'Unity archive validation failed: {exc}')
        return 1

    for result in results:
        print(
            f"{result['world']}: archive={result['archive_sha256']} "
            f"Assembly-CSharp.dll={result['assembly_sha256']}"
        )
    print(f"source={results[0]['source_ref']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
