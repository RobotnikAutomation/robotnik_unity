#!/usr/bin/env python3
# utils/load_usd_and_run.py
#
# Unpacks a Unity simulation archive into a hash-validated user cache,
# ensures executable permissions, and runs the simulation.
#
# Only the two pinned runtime archives installed with this package are accepted.

import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_unity_archives import (  # noqa: E402
    EXPECTED_ARCHIVES,
    safe_extract_archive,
    verify_archive,
)

BINARY_NAME = 'PI_simulation_Unity_Robotnik.x86_64'
ARCHIVE_NAME = 'unity_simulation_only.tar.gz'
ARCHIVE_HASH_FILE = '.archive.sha256'


def archive_to_world_id(archive_name: str) -> str:
    """Derive a stable folder name from the archive file name."""
    name = Path(archive_name).name
    # Handle common compressed tar suffixes
    for suffix in ('.tar.gz', '.tgz', '.tar.bz2', '.tar.xz', '.tar.zst'):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    # Fallback to stem if it's something unusual
    return Path(name).stem


def find_binary(root_dir: Path, binary_name: str) -> Optional[Path]:
    """Search for the binary inside root_dir only (not the whole worlds dir)."""
    direct = root_dir / binary_name
    if direct.is_file() and not direct.is_symlink():
        return direct
    for p in root_dir.rglob(binary_name):
        if p.is_file() and not p.is_symlink():
            return p
    return None


def ensure_extracted(
    worlds_dir: Path,
    cache_dir: Path,
    archive_name: str,
    binary_name: str,
    *,
    expected_sha256: str | None = None,
    expected_world: str | None = None,
) -> Path:
    """
    Ensure that the requested world archive is extracted.

    Return the path to the simulation binary inside that world's folder.
    """
    if Path(archive_name).name != archive_name:
        raise ValueError('archive must be a file name inside the worlds directory')
    archive_path = worlds_dir / archive_name
    if not archive_path.is_file():
        raise FileNotFoundError(
            f'archive not found: {archive_path}; install the official Unity '
            'runtime archives with the unity_sim package'
        )

    verification = verify_archive(
        archive_path,
        expected_sha256=expected_sha256,
        expected_world=expected_world,
    )
    archive_sha256 = verification['archive_sha256']
    world_id = archive_to_world_id(archive_name)
    world_root = cache_dir / world_id
    if world_root.is_symlink():
        raise ValueError(f'refusing to use symlinked Unity cache: {world_root}')
    hash_marker = world_root / ARCHIVE_HASH_FILE

    # Reuse an extraction only when it came from this exact archive.
    sim_binary = find_binary(world_root, binary_name)
    try:
        cached_sha256 = hash_marker.read_text().strip()
    except OSError:
        cached_sha256 = ''
    if sim_binary and cached_sha256 == archive_sha256:
        print(f'[INFO] Using cached simulation: {sim_binary}')
        return sim_binary

    # Extract beside the cache and replace an old copy only after success.
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=f'.{world_id}.', dir=cache_dir))
    print(f'[INFO] Extracting {archive_path.name!r} into {str(temporary_root)!r}')
    try:
        safe_extract_archive(archive_path, temporary_root)
        sim_binary = find_binary(temporary_root, binary_name)
        if not sim_binary:
            raise ValueError(
                f'extraction completed but {binary_name!r} was not found'
            )

        (temporary_root / ARCHIVE_HASH_FILE).write_text(
            archive_sha256 + '\n', encoding='utf-8'
        )
        relative_binary = sim_binary.relative_to(temporary_root)
        if world_root.exists():
            shutil.rmtree(world_root)
        temporary_root.rename(world_root)
    except BaseException:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise
    return world_root / relative_binary


def build_unity_args(sim_binary: Path, batchmode: bool, render_fps: int) -> list[str]:
    """Build Unity arguments while retaining graphics in batch mode."""
    unity_args = [str(sim_binary), '-benchmark-render-fps', str(render_fps)]
    if batchmode:
        unity_args.extend(['-batchmode', '-logFile', '-'])
    return unity_args


def main(
    archive_name: str,
    binary_name: str = BINARY_NAME,
    batchmode: bool = False,
    render_fps: int = 60,
):
    # Base path is the folder of this script
    script_dir = Path(__file__).resolve().parent
    worlds_dir = script_dir.parent / 'worlds'
    default_cache = Path.home() / '.cache'
    cache_home = Path(os.environ.get('XDG_CACHE_HOME', default_cache))
    cache_dir = cache_home / 'robotnik_sim_benchmark' / 'unity_sim'

    expected = EXPECTED_ARCHIVES.get(archive_name)
    if expected is None:
        print(
            f'[ERROR] Unsupported Unity archive {archive_name!r}; expected one of: '
            + ', '.join(sorted(EXPECTED_ARCHIVES)),
            file=sys.stderr,
        )
        return 2

    try:
        sim_binary = ensure_extracted(
            worlds_dir,
            cache_dir,
            archive_name,
            binary_name,
            expected_sha256=expected['sha256'],
            expected_world=expected['world'],
        )
    except (OSError, ValueError) as exc:
        print(f'[ERROR] Unity runtime validation/extraction failed: {exc}', file=sys.stderr)
        return 1

    # Ensure executable permission (best-effort)
    try:
        sim_binary.chmod(0o755)
    except Exception as e:
        print(f'[WARN] Could not change executable permissions: {e}')

    os.environ.setdefault('ROBOTNIK_RENDER_FPS', str(render_fps))
    unity_args = build_unity_args(sim_binary, batchmode, render_fps)

    if batchmode:
        print('[INFO] Running in batchmode with graphics enabled for sensors')
    else:
        print('[INFO] Running in normal mode (with UI)')

    print(f"[INFO] Starting Unity simulation: {' '.join(unity_args)}")
    sys.stdout.flush()

    # Replace this bootstrap process with Unity itself.  ROS launch can then
    # supervise and signal the simulator directly instead of an intermediate
    # Python process.  Do not add -nographics: camera rendering is benchmarked.
    try:
        os.chdir(sim_binary.parent)
        os.execv(sim_binary, unity_args)
    except OSError as e:
        print(f'[ERROR] Failed to start simulation: {e}')
        return 1


if __name__ == '__main__':
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Load and run Unity simulation from a tar.gz archive.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 load_usd_and_run.py                           # Normal mode with default archive
  python3 load_usd_and_run.py unity_simulation.tar.gz   # Simple world
  python3 load_usd_and_run.py unity_simulation.tar.gz -b
  python3 load_usd_and_run.py --batchmode               # Batchmode with default archive
        """
    )
    parser.add_argument(
        'archive',
        nargs='?',
        default=ARCHIVE_NAME,
        choices=tuple(EXPECTED_ARCHIVES),
        help=f'Unity simulation archive (default: {ARCHIVE_NAME})',
    )
    parser.add_argument(
        '-b', '--batchmode',
        action='store_true',
        help='Run Unity in batchmode with graphics enabled',
    )
    def positive_render_fps(value: str) -> int:
        fps = int(value)
        if not 1 <= fps <= 1000:
            raise argparse.ArgumentTypeError('render FPS must be between 1 and 1000')
        return fps

    parser.add_argument(
        '--render-fps',
        type=positive_render_fps,
        default=positive_render_fps(os.environ.get('ROBOTNIK_RENDER_FPS', '60')),
        help='Unity render FPS target for benchmark runs',
    )
    args = parser.parse_args()

    if args.archive == ARCHIVE_NAME and len(sys.argv) == 1:
        print(f'[INFO] No archive name provided. Using default: {args.archive}')

    raise SystemExit(
        main(
            archive_name=args.archive,
            batchmode=args.batchmode,
            render_fps=args.render_fps,
        )
    )
