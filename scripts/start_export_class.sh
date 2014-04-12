#!/bin/bash

# Script called from /etc/init/export-class.conf
# so as to start during reboot, and be controllable
# via sudo service export-class start
#     sudo service export-class stop

# Get directory in which this script is running,
# and where its support scripts therefore live:
currScriptsDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

cd $currScriptsDir/..

startupCmd="export PYTHONPATH=$WORKON_HOME/online_learning_computations/lib/python2.7/site-packages/online_learning_computations-0.26-py2.7.egg/src:$PYTHONPATH \
export PYTHONPATH=$WORKON_HOME/online_learning_computations/lib/python2.7/site-packages/online_learning_computations-0.26-py2.7.egg:$PYTHONPATH \
export PYTHONPATH=$WORKON_HOME/open_edx_class_export/local/lib/python2.7/site-packages/pymysql_utils-0.25-py2.7.egg:$PYTHONPATH \
/usr/bin/python src/exportClass.py"

# This script is called as root.
# Drop root permissions to whose of the owner
# of this script:
SCRIPT_OWNER=`stat -c %U  $0`
su -m -c $startupCmd  $SCRIPT_OWNER
