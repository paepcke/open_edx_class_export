#!/bin/bash

LOG_FILE=/home/dataman/Data/EdX/NonTransformLogs/exportClass.log

# Get directory in which this script is running,
# and where its support scripts therefore live:
currScriptsDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

cd $currScriptsDir/..
ME=`whoami`

export PYTHONPATH=/lfs/datastage2/0/home/$ME/anaconda2/lib/python2.7/site-packages/online_learning_computations-0.35-py2.7.egg/src:$PYTHONPATH

if [ ! -f $LOG_FILE ]
then
    # Create directories to log file as needed:
    DIR_PART_LOG_FILE=`dirname $LOG_FILE`
    mkdir --parents $DIR_PART_LOG_FILE
    touch $LOG_FILE
fi

# Make sure we use Python 2.7, and not
# the system-wide 2.6:
/home/dataman/anaconda2/bin/python src/exportClass.py | tee --append $LOG_FILE
