#!/usr/bin/env python3
"""Analyze individual files from company-documents pages.

1. Check for duplicates against ZIP bundle contents
2. Extract page counts for PDFs
3. Create comprehensive analysis report
"""

import json
import re
from pathlib import Path
from collections import defaultdict
import httpx
from tqdm import tqdm

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    print("Warning: PyMuPDF not available, page counts will be skipped")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOC = DATA / "toc.json"
ORPHANS = DATA / "orphans.json"
OUTPUT = DATA / "individual_files_analysis.json"

def extract_page_count(url: str) -> int | None:
    """Extract page count from PDF at URL without downloading full file."""
    if not HAS_PYMUPDF:
        return None

    try:
        # Stream PDF to get page count without full download
        with httpx.stream("GET", url, timeout=30.0, follow_redirects=True) as response:
            if response.status_code != 200:
                return None

            # Read enough bytes to get PDF structure
            pdf_bytes = b""
            for chunk in response.iter_bytes(chunk_size=8192):
                pdf_bytes += chunk
                # Stop after reasonable amount for page count
                if len(pdf_bytes) > 5 * 1024 * 1024:  # 5MB limit
                    break

        # Try to open PDF from bytes
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        doc.close()
        return page_count

    except Exception:
        return None

def main():
    """Analyze individual files for duplicates and missing metadata."""

    # Load data
    toc = json.loads(TOC.read_text())
    orphans = json.loads(ORPHANS.read_text())

    # Create set of all filenames from ZIP bundles
    zip_filenames = set()
    for row in toc:
        filename = row["member_name"].rsplit("/", 1)[-1]
        zip_filenames.add(filename)

    print(f"Loaded {len(toc):,} files from ZIP bundles")
    print(f"Loaded {len(orphans):,} orphan files")

    # Get individual files (non-zip orphans)
    individual_files = [o for o in orphans if not o["filename"].lower().endswith(".zip")]
    print(f"Found {len(individual_files):,} individual files")

    # Analysis results
    results = {
        "summary": {
            "total_individual_files": len(individual_files),
            "duplicates_of_zip_content": 0,
            "unique_individual_files": 0,
            "pdfs_analyzed": 0,
            "pdfs_with_page_counts": 0
        },
        "duplicate_individuals": [],
        "unique_individuals": [],
        "page_count_updates": [],
        "by_extension": defaultdict(list)
    }

    # Check each individual file
    print("\nAnalyzing individual files...")

    for ind_file in tqdm(individual_files):
        filename = ind_file["filename"]
        url = ind_file["url"]

        # Check if this filename exists in ZIP bundles
        is_duplicate = filename in zip_filenames

        # Categorize by extension
        ext = filename.split(".")[-1].lower() if "." in filename else "no_ext"
        results["by_extension"][ext].append({
            "filename": filename,
            "url": url,
            "is_duplicate": is_duplicate,
            "product_page": ind_file.get("product_page", "")
        })

        if is_duplicate:
            results["summary"]["duplicates_of_zip_content"] += 1
            results["duplicate_individuals"].append({
                "filename": filename,
                "individual_url": url,
                "product_page": ind_file.get("product_page", ""),
                "also_in_zip": True
            })
        else:
            results["summary"]["unique_individual_files"] += 1
            results["unique_individuals"].append({
                "filename": filename,
                "individual_url": url,
                "product_page": ind_file.get("product_page", ""),
                "unique_to_individual": True
            })

        # Extract page count for PDFs
        if ext == "pdf":
            results["summary"]["pdfs_analyzed"] += 1
            print(f"\nExtracting page count for: {filename}")

            page_count = extract_page_count(url)
            if page_count is not None:
                results["summary"]["pdfs_with_page_counts"] += 1
                results["page_count_updates"].append({
                    "filename": filename,
                    "url": url,
                    "page_count": page_count
                })
                print(f"  → {page_count} pages")
            else:
                print(f"  → Failed to extract page count")

    # Convert defaultdict to regular dict
    results["by_extension"] = dict(results["by_extension"])

    # Summary statistics
    print("\n" + "="*60)
    print("INDIVIDUAL FILES ANALYSIS RESULTS")
    print("="*60)
    print(f"Total individual files: {results['summary']['total_individual_files']:,}")
    print(f"Duplicates of ZIP content: {results['summary']['duplicates_of_zip_content']:,}")
    print(f"Unique to individual pages: {results['summary']['unique_individual_files']:,}")
    print()

    print("File types:")
    for ext, files in sorted(results["by_extension"].items()):
        duplicates = sum(1 for f in files if f["is_duplicate"])
        unique = len(files) - duplicates
        print(f"  {ext}: {len(files)} total ({duplicates} duplicate, {unique} unique)")

    if results["page_count_updates"]:
        print(f"\nPDF page counts extracted: {len(results['page_count_updates'])}")
        print("Examples:")
        for update in results["page_count_updates"][:5]:
            print(f"  {update['filename']}: {update['page_count']} pages")

    if results["duplicate_individuals"]:
        print(f"\nDuplicate individual files (first 10):")
        for dup in results["duplicate_individuals"][:10]:
            print(f"  {dup['filename']} (also in ZIP)")

    # Save results
    OUTPUT.write_text(json.dumps(results, indent=2))
    print(f"\nComplete analysis saved to: {OUTPUT}")

if __name__ == "__main__":
    main()