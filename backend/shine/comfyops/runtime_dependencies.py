"""Offline dependency locks and explicitly approved source-to-wheel builds.

The source hash and build argv belong to the installation plan. Derived wheels
are accepted only after their identity is checked, and keep their own hash and
source provenance. An old wheelhouse never supplies an unapproved dependency.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from email.parser import BytesParser
from pathlib import Path

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import SourceWheelBuild, safe_target


def canonical_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def file_sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def wheel_identity(path):
    with zipfile.ZipFile(path) as archive:
        metadata = [n for n in archive.namelist() if re.fullmatch(r'[^/\\]+\.dist-info/METADATA', n)]
        if len(metadata) != 1:
            raise ValueError('WHEEL_METADATA_INVALID')
        content = BytesParser().parsebytes(archive.read(metadata[0]))
    name, version = content.get('Name', ''), content.get('Version', '')
    if (len(content.get_all('Name', [])) != 1 or len(content.get_all('Version', [])) != 1
            or not re.fullmatch(r'[A-Za-z0-9_.-]+', name)
            or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', version)):
        raise ValueError('WHEEL_METADATA_INVALID')
    return canonical_name(name), version


def source_build_plans(root, packages, package_root, build_id):
    result = []
    seen = set()
    for package in packages:
        for raw in package['spec'].get('source_builds', []):
            build = SourceWheelBuild.model_validate(raw)
            spec = next(f for f in package['spec']['files'] if f['relative_path'] == build.relative_path)
            name = canonical_name(build.name)
            if name in seen:
                raise ValueError('DUPLICATE_SOURCE_BUILD')
            seen.add(name)
            source = safe_target(package_root / package['spec']['name'], build.relative_path)
            output = safe_target(root, f'.built-wheels/{build_id}/{spec["sha256"]}')
            result.append({**build.model_dump(), 'name': name, 'source': str(source),
                           'source_sha256': spec['sha256'], 'source_size': spec['size'],
                           'package_id': package['id'], 'package_hash': package['hash'], 'output': str(output)})
    return result


def dependency_commands(root, python, builds):
    install = [str(python), '-m', 'pip', 'install', '--no-index', '--require-hashes',
               '--find-links', str(root / '.wheelhouse'), '-r', str(root / '.shine-requirements.txt')]
    if not builds:
        return [install, [str(python), '-m', 'pip', 'check']]
    commands = [[*install, '--no-deps']]
    commands.extend([
        str(python), '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation',
        '--no-cache-dir', '--wheel-dir', build['output'], build['source'],
    ] for build in builds)
    install_built = list(install)
    install_built[install_built.index('-r') + 1] = str(root / '.shine-built-requirements.txt')
    return [*commands, [*install_built, '--no-deps', '--force-reinstall'],
            install, [str(python), '-m', 'pip', 'check']]


def dependency_lock(root, packages, package_root, *, pending_builds=False, built_wheels=()):
    wheelhouse = root / '.wheelhouse'
    wheelhouse.mkdir(exist_ok=True)
    wheels = {}
    pending = set()
    built_wheels = list(built_wheels)

    def add(path, digest, size):
        if path.stat().st_size != size or file_sha256(path) != digest:
            raise ValueError('DEPENDENCY_BYTES_CHANGED')
        name, version = wheel_identity(path)
        if name in wheels and wheels[name] != (version, digest):
            raise ValueError('DEPENDENCY_VERSION_CONFLICT')
        wheels[name] = (version, digest)
        shutil.copyfile(path, wheelhouse / path.name)

    for package in packages:
        sources = {b['relative_path']: b for b in package['spec'].get('source_builds', [])}
        for spec in package['spec']['files']:
            path = safe_target(package_root / package['spec']['name'], spec['relative_path'])
            if path.suffix == '.whl':
                add(path, spec['sha256'], spec['size'])
            elif spec['relative_path'] in sources:
                pending.add(canonical_name(sources[spec['relative_path']]['name']))
            elif package['spec']['kind'] == 'wheel':
                raise ValueError('UNDECLARED_SOURCE_BUILD')
    for built in built_wheels:
        name, version = wheel_identity(Path(built['path']))
        if name not in pending or name != built['name'] or version != built['version']:
            raise ValueError('BUILT_WHEEL_IDENTITY_CHANGED')
        add(Path(built['path']), built['sha256'], built['size'])
        pending.remove(name)
    if pending & set(wheels):
        raise ValueError('DEPENDENCY_VERSION_CONFLICT')
    if pending and not pending_builds:
        raise ValueError('PINNED_SOURCE_BUILD_REQUIRED')
    if {'setuptools', 'wheel'} - set(wheels) and pending:
        raise ValueError('PINNED_BUILD_TOOLS_MISSING')
    requirement_files = [root / 'requirements.txt', *list((root / 'custom_nodes').glob('*/requirements.txt'))]
    for requirements in requirement_files:
        if not requirements.exists():
            continue
        for line in requirements.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            name = re.match(r'^([A-Za-z0-9_.-]+)(?:\[|[<>=!~; ]|$)', line)
            if not name or canonical_name(name[1]) not in set(wheels) | pending:
                raise ValueError('PINNED_DEPENDENCY_WHEEL_MISSING')
    lock = '\n'.join(f'{name}=={v} --hash=sha256:{sha}' for name, (v, sha) in sorted(wheels.items())) + '\n'
    (root / '.shine-requirements.txt').write_text(lock, encoding='utf-8')
    if built_wheels:
        built_lock = '\n'.join(
            f'{built["name"]}=={built["version"]} --hash=sha256:{built["sha256"]}' for built in built_wheels
        ) + '\n'
        (root / '.shine-built-requirements.txt').write_text(built_lock, encoding='utf-8')
    return graph_hash(wheels)


def prepare_build_directories(root, builds):
    for build in builds:
        source = Path(build['source'])
        if (source.stat().st_size != build['source_size']
                or file_sha256(source) != build['source_sha256']):
            raise ValueError('SOURCE_BUILD_INPUT_CHANGED')
        output = Path(build['output'])
        if not output.resolve().is_relative_to((root / '.built-wheels').resolve()) or output.exists():
            raise ValueError('SOURCE_BUILD_OUTPUT_REQUIRES_RECONCILIATION')
        output.mkdir(parents=True)


def verified_built_wheels(root, builds):
    result = []
    for build in builds:
        source = Path(build['source'])
        if (source.stat().st_size != build['source_size']
                or file_sha256(source) != build['source_sha256']):
            raise ValueError('SOURCE_BUILD_INPUT_CHANGED')
        output = Path(build['output'])
        paths = list(output.iterdir())
        if (not output.resolve().is_relative_to((root / '.built-wheels').resolve())
                or len(paths) != 1 or not paths[0].is_file() or paths[0].suffix != '.whl'
                or paths[0].is_symlink() or paths[0].resolve().parent != output.resolve()):
            raise ValueError('SOURCE_BUILD_OUTPUT_INVALID')
        path = paths[0]
        name, version = wheel_identity(path)
        if name != build['name'] or version != build['version']:
            raise ValueError('BUILT_WHEEL_IDENTITY_CHANGED')
        result.append({**build, 'path': str(path), 'sha256': file_sha256(path),
                       'size': path.stat().st_size})
    return result
