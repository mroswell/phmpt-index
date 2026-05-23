"""Map orphan zip files to their corresponding main zip bundles.

For each orphan zip file that contains duplicate content, find which
main zip bundle from the multiple-file-downloads page contains the
same files.
"""

import json
from pathlib import Path
import zipfile_deflate64 as zipfile
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ORPHANS = DATA / "orphans.json"
TOC = DATA / "toc.json"
SCRATCH = ROOT / ".scratch" / "orphan-outliers"

def main():
    """Create mapping of orphan zip files to main zip bundles."""

    # Load data
    toc = json.loads(TOC.read_text())
    orphans = json.loads(ORPHANS.read_text())

    # Create filename -> zip_source mapping from TOC
    file_to_zip = {}
    for row in toc:
        filename = row["member_name"].rsplit("/", 1)[-1]
        file_to_zip[filename] = row["zip_source"]

    print(f"Loaded {len(toc):,} files from {len(set(row['zip_source'] for row in toc))} main zip bundles")

    # Get orphan zip files
    orphan_zips = [o for o in orphans if o["filename"].lower().endswith(".zip")]
    print(f"Found {len(orphan_zips):,} orphan zip files")

    duplicates = []
    new_files = []

    for orphan in orphan_zips:
        zip_path = SCRATCH / orphan["filename"]
        if not zip_path.exists():
            continue

        try:
            with zipfile.ZipFile(zip_path) as zf:
                members = [i.filename.rsplit("/", 1)[-1] for i in zf.infolist() if not i.is_dir()]
        except Exception as e:
            print(f"Error reading {orphan['filename']}: {e}")
            continue

        if not members:
            continue

        # Check if file exists in main bundles
        inner_file = members[0]  # All analyzed orphan zips have 1 file each
        main_zip = file_to_zip.get(inner_file)

        if main_zip:
            duplicates.append({
                "orphan_zip": orphan["filename"],
                "orphan_url": orphan["url"],
                "product_page": orphan["product_page"],
                "inner_file": inner_file,
                "main_zip_source": main_zip,
                "status": "duplicate"
            })
        else:
            new_files.append({
                "orphan_zip": orphan["filename"],
                "orphan_url": orphan["url"],
                "product_page": orphan["product_page"],
                "inner_file": inner_file,
                "main_zip_source": None,
                "status": "new"
            })

    print(f"\nResults:")
    print(f"  Duplicates: {len(duplicates)}")
    print(f"  New files: {len(new_files)}")

    # Group duplicates by main zip bundle
    by_main_zip = defaultdict(list)
    for dup in duplicates:
        by_main_zip[dup["main_zip_source"]].append(dup)

    print(f"\nDuplicates by main zip bundle:")
    for main_zip, items in sorted(by_main_zip.items()):
        print(f"  {main_zip}: {len(items)} orphan zip files")

    # Save results
    output = {
        "summary": {
            "total_orphan_zips": len(orphan_zips),
            "duplicates": len(duplicates),
            "new_files": len(new_files)
        },
        "duplicates": duplicates,
        "new_files": new_files,
        "by_main_zip": dict(by_main_zip)
    }

    output_file = DATA / "orphan_duplicate_mapping.json"
    output_file.write_text(json.dumps(output, indent=2))
    print(f"\nSaved mapping to {output_file}")

    # Print some examples
    print(f"\nFirst 5 duplicates:")
    for dup in duplicates[:5]:
        print(f"  {dup['orphan_zip']} -> {dup['main_zip_source']}")
        print(f"    Contains: {dup['inner_file']}")

    print(f"\nNew files:")
    for new in new_files:
        print(f"  {new['orphan_zip']}")
        print(f"    Contains: {new['inner_file']}")

if __name__ == "__main__":
    main()