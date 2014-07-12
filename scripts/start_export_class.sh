#!/bin/bash

# Script called from /etc/init/export-class.conf
# so as to start during reboot, and be controllable
# via sudo service export-class start
#     sudo service export-class stop
#
# Starts the exportClass.py script, setting up proper environment
# for all modules to be found, even when called as root. That is
# the case when started as 'service export-class start' 
#
# In /etc/init/export-class.conf start this script using setsid:
#
#   setsid ..../open_edx_class_export/scripts/start_export_class.sh
#
# Reason: this way the group id of all processes will be 1, and 
# we can kill all subprocesses via:
#    kill -- -<PID>
#
# Strategy: we drop from root to the user who owns this script.
#   We can do this b/c we start as root. As part of the 'su'
#   we start another script start_export_class_forked.sh, which
#   does the actual work.



# Get directory in which this script is running,
# and where its support scripts therefore live:
currScriptsDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# This script is called as root.
# Drop root permissions to whose of the owner
# of this script:
SCRIPT_OWNER=`stat -c %U  $0`

# Save own PID so that killing can use it:
echo $$ > /tmp/exportClass.pid

su --command=$currScriptsDir/start_export_class_forked.sh $SCRIPT_OWNER
exit 0
