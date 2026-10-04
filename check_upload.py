"""Check the delivered folder against GitHub's browser upload limits (stdlib only)."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 25 * 1024 * 1024
SKIP_DIRS = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache'}
EXPECTED = {
    'media/Demo Video.mp4': 'e48ac09d5de7f650055967f04993e7e6a4b09a941a989762086bf478c029f0ac',
    'presentation/PRESENT.html': 'a2c86986ca1f6ecfb07848bfbeef3894cff0b1dee27da82a1a31393952d2407a',
    'artifacts/forecast_models.pkl': 'e487a1b7cb952fa78dba91fa62f7f3a059816cd53b814764d38f1ef505a607ac',
    'artifacts/synthetic_hourly.csv.gz': '81e1a6380d13f6e462991ffd842c29338548b2407da49b06678d4cc41bbdd23d',
}


def main():
    files = []
    errors = []
    # Walk without following links so unrelated folders can never enter the upload.
    def visit(directory):
        for path in directory.iterdir():
            relative = path.relative_to(ROOT).as_posix()
            if path.name in SKIP_DIRS:
                continue
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                errors.append('Linked path: ' + relative)
            elif path.is_dir():
                if path.name in {'node_modules', '.packages'}:
                    errors.append('Bundled dependencies: ' + relative)
                else:
                    visit(path)
            elif path.is_file():
                files.append(path)
                if path.stat().st_size >= MAX_BYTES:
                    errors.append('File reaches 25 MiB: ' + relative)
                if path.name == '.env' or path.suffix in {'.log', '.pyc', '.zip'}:
                    errors.append('Unnecessary/private file: ' + relative)
    visit(ROOT)
    if len(files) >= 100:
        errors.append('Keep the complete upload below 100 files.')
    for relative, digest in EXPECTED.items():
        path = ROOT / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append('Delivered asset changed or missing: ' + relative)
    largest = max(files, key=lambda p: p.stat().st_size) if files else None
    print(json.dumps({
        'passed': not errors, 'upload_files': len(files),
        'total_bytes': sum(p.stat().st_size for p in files),
        'largest_file': largest.relative_to(ROOT).as_posix() if largest else None,
        'largest_bytes': largest.stat().st_size if largest else 0,
        'individual_limit_bytes': MAX_BYTES,
        'verified_asset_hashes': len(EXPECTED) if not errors else None,
        'excluded_local_folders': sorted(SKIP_DIRS), 'errors': errors,
    }, indent=2))
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
