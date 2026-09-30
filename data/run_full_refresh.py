"""
Run this after exporting data from the browser-side incremental_update.js pipeline
(window.__exportIncremental(), then move the downloaded file next to this script,
or pass its path as an argument).

This does everything that does NOT require a browser:
  1. Merge the incremental export into match_performance.json and incremental_new_matches.json
  2. Rebuild the main unranked ladder (unranked_ladder.json)
  3. Rebuild per-map Elo (map_elo.json)
  4. Rebuild open/closed map-type Elo (openclosed_elo.json)
  5. Regenerate every player's Elo progress graph (graphs/elo_*.png)

Usage:
    python run_full_refresh.py [path_to_incremental_update_export.json] [--experimental-elo]

--experimental-elo: after the merge, build the experimental probability ladder
(../experimental_elo/) INSTEAD of the default Elo ladder, map Elo, open/closed Elo
and graphs.

If no export file is given (or the argument is omitted and the default path doesn't
exist), it skips the merge step and just rebuilds everything from whatever data is
already on disk - useful if you only want to regenerate outputs after manually editing
something, without a fresh browser harvest.
"""
import runpy
import subprocess
import sys
import os

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
EXPERIMENTAL_DIR = os.path.join(os.path.dirname(DATA_DIR), "experimental_elo")
DEFAULT_EXPORT_PATH = os.path.join(DATA_DIR, "incremental_update_export.json")


def run_script(name, argv=None):
    print(f"\n{'='*60}\nRunning {name}\n{'='*60}")
    saved_argv = sys.argv
    sys.argv = argv if argv is not None else [name]
    try:
        runpy.run_path(os.path.join(DATA_DIR, name), run_name="__main__")
    finally:
        sys.argv = saved_argv


def main():
    args = [a for a in sys.argv[1:] if a != "--experimental-elo"]
    experimental = "--experimental-elo" in sys.argv[1:]
    export_path = args[0] if args else DEFAULT_EXPORT_PATH

    if os.path.exists(export_path):
        print(f"\n{'='*60}\nMerging incremental export: {export_path}\n{'='*60}")
        run_script("merge_incremental_update.py", argv=["merge_incremental_update.py", export_path])
    else:
        print(f"\nNo incremental export found at {export_path} - skipping merge, "
              f"rebuilding from data already on disk.")

    if experimental:
        # Experimental probability ladder only; the default Elo outputs are left as-is.
        script = os.path.join(EXPERIMENTAL_DIR, "build_probability_ladder.py")
        print(f"\n{'='*60}\nRunning {script}\n{'='*60}")
        subprocess.run([sys.executable, script], check=True)
        print(f"\n{'='*60}\nExperimental refresh complete.\n{'='*60}")
        return

    run_script("build_unranked_ladder.py")
    run_script("build_map_elo.py")
    run_script("build_openclosed_elo.py")
    run_script("plot_elo_progress.py")

    print(f"\n{'='*60}\nFull refresh complete.\n{'='*60}")


if __name__ == "__main__":
    main()
