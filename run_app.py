"""
Duplicate Finder entry point.

Default (no args): launches the desktop GUI.
Headless:  DuplicateFinder --scan "/path/to/folder"
           runs a scan in the terminal and writes dedup_data.json, no window.
"""
import sys


def main():
    args = sys.argv[1:]
    if args and args[0] == "--scan":
        if len(args) < 2:
            print("usage: DuplicateFinder --scan <folder>")
            sys.exit(2)
        from dedup.main import run_scan
        run_scan(args[1], write_json=True)
        return
    from dedup.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
