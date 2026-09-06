"""Rebuild everything that does NOT require a browser.

Run this after exporting data from the browser-side incremental_update.js pipeline
(window.__exportIncremental(), then move the downloaded file next to this script, or pass
its path as an argument). fetch_incremental_update.py chains into it automatically.

Steps:
  1. Merge the incremental export into match_performance.json and incremental_new_matches.json
  2. Rebuild the main unranked ladder (unranked_ladder.json)
  3. Rebuild per-map Elo (map_elo.json)
  4. Rebuild open/closed map-type Elo (openclosed_elo.json)
  5. Regenerate every player's Elo progress graph (graphs/elo_*.png)
  6. Refresh perf_chunks/known_ids.json so the next discovery pass stops at the right place

Usage:
    python run_full_refresh.py [path_to_incremental_update_export.json]

With no export file (or a path that doesn't exist) it skips the merge and just rebuilds
from whatever is already on disk - useful to regenerate outputs after editing something by
hand, without a fresh browser harvest.
"""
import runpy
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# The scripts run below import ladder_common, so make sure this directory is importable
# even when the refresh is launched from somewhere else.
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import ladder_common as lc  # noqa: E402  (needs SCRIPT_DIR on sys.path first)


def run_script(name, argv=None):
    print(f"\n{'='*60}\nRunning {name}\n{'='*60}")
    saved_argv = sys.argv
    sys.argv = argv if argv is not None else [name]
    try:
        runpy.run_path(str(SCRIPT_DIR / name), run_name="__main__")
    finally:
        sys.argv = saved_argv


def main():
    export_path = Path(sys.argv[1]) if len(sys.argv) > 1 else lc.INCREMENTAL_EXPORT_PATH

    if export_path.exists():
        print(f"\n{'='*60}\nMerging incremental export: {export_path}\n{'='*60}")
        run_script("merge_incremental_update.py", argv=["merge_incremental_update.py", str(export_path)])
    else:
        print(f"\nNo incremental export found at {export_path} - skipping merge, "
              f"rebuilding from data already on disk.")

    run_script("build_unranked_ladder.py")
    run_script("build_map_elo.py")
    run_script("build_openclosed_elo.py")
    run_script("plot_elo_progress.py")

    # Last, so it covers anything the merge just added.
    ids = lc.write_known_ids()
    print(f"\nRefreshed {lc.KNOWN_IDS_PATH} ({len(ids)} known match ids)")

    print(f"\n{'='*60}\nFull refresh complete.\n{'='*60}")


if __name__ == "__main__":
    main()
