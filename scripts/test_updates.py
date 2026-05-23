#!/usr/bin/env python3
"""Test script to check for phmpt.org updates locally.

This simulates what the GitHub Action does but runs locally for testing.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BACKUPS = ROOT / ".github" / "backups"

def backup_current_data():
    """Create backups of current data files."""
    BACKUPS.mkdir(parents=True, exist_ok=True)

    for filename in ["zips.json", "individual_urls.json", "orphans.json"]:
        current = DATA / filename
        backup = BACKUPS / f"{filename}.backup"

        if current.exists():
            shutil.copy2(current, backup)
            print(f"✅ Backed up {filename}")
        else:
            print(f"⚠️  {filename} doesn't exist yet")

def file_changed(current_file, backup_file):
    """Check if file content changed."""
    if not backup_file.exists():
        return current_file.exists()

    if not current_file.exists():
        return True

    try:
        current_data = json.loads(current_file.read_text())
        backup_data = json.loads(backup_file.read_text())
        return current_data != backup_data
    except Exception as e:
        print(f"Error comparing files: {e}")
        return True

def check_for_changes():
    """Check for changes in data files."""
    changes = {}

    zips_changed = file_changed(
        DATA / "zips.json",
        BACKUPS / "zips.json.backup"
    )
    if zips_changed:
        changes["multiple_file_downloads"] = "New ZIP bundles detected"

    individual_changed = file_changed(
        DATA / "individual_urls.json",
        BACKUPS / "individual_urls.json.backup"
    )
    if individual_changed:
        changes["individual_urls"] = "New individual file URLs detected"

    orphans_changed = file_changed(
        DATA / "orphans.json",
        BACKUPS / "orphans.json.backup"
    )
    if orphans_changed:
        changes["orphans"] = "New orphan files detected"

    return changes

def create_update_log(changes):
    """Create update log file."""
    update_log = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "changes_detected": bool(changes),
        "changes": changes,
        "files_updated": [],
        "status": "testing" if not changes else "changes_detected"
    }

    log_file = DATA / "update_log.json"
    log_file.write_text(json.dumps(update_log, indent=2))
    print(f"📝 Update log written to {log_file}")

    return update_log

def main():
    print("🔍 Testing PHMPT update detection...")
    print(f"Working directory: {ROOT}")
    print()

    # Create backups first
    print("📋 Creating backups of current data...")
    backup_current_data()
    print()

    # You would run the crawling scripts here in real usage:
    print("💡 To test with real data, run:")
    print("  uv run python scripts/bootstrap.py")
    print("  uv run python scripts/crawl_listing.py")
    print("  uv run python scripts/crawl_files.py")
    print()

    # Check for changes
    print("🔍 Checking for changes...")
    changes = check_for_changes()

    if changes:
        print("✅ Changes detected:")
        for key, desc in changes.items():
            print(f"  - {desc}")
    else:
        print("ℹ️  No changes detected")

    print()

    # Create update log
    update_log = create_update_log(changes)

    print(f"📊 Status: {update_log['status']}")
    print(f"🕐 Timestamp: {update_log['timestamp']}")

if __name__ == "__main__":
    main()