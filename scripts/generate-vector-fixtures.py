"""Create the shared synthetic multi-format acceptance data using real GDAL drivers."""
import argparse
import json
import runpy
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    fixture_file = Path(__file__).resolve().parents[1] / "gis-engine/tests/vector_fixtures.py"
    fixtures = runpy.run_path(str(fixture_file))["create_fixture_bundle"](directory)
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(fixtures, indent=2), encoding="utf-8")
    print(manifest)


if __name__ == "__main__":
    main()
