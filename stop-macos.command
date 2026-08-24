#!/bin/bash
# Double-click in Finder to stop the NERVIS ecosystem.
#
# Finder runs a .command from the user's home directory rather than from where
# the file lives, so the first thing this does is move to its own directory.
# Everything else is in tools/run.py, shared with the Linux and Windows
# launchers so the sequence cannot drift between platforms.
cd "$(dirname "$0")" || exit 1
python3 tools/run.py stop
status=$?
# The services are detached and keep running after this window closes. The
# pause is so a failure message is still readable when Finder would otherwise
# close the window on exit.
[ $status -ne 0 ] && read -r -p "Press return to close…"
exit $status
