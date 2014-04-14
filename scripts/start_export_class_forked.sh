#!/bin/bash

LOG_FILE=/home/dataman/Data/EdX/NonTransformLogs/exportClass.log

# Get directory in which this script is running,
# and where its support scripts therefore live:
currScriptsDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

cd $currScriptsDir/..
ME=`whoami`

export PYTHONPATH=/home/$ME/.virtualenvs/online_learning_computations/lib/python2.7/site-packages/online_learning_computations-0.27-py2.7.egg/src:$PYTHONPATH
export PYTHONPATH=/home/$ME/.virtualenvs/open_edx_class_export/lib/python2.7/site-packages/pymysql_utils-0.30-py2.7.egg:$PYTHONPATH

if [ ! -f $LOG_FILE ]
then
    # Create directories to log file as needed:
    DIR_PART_LOG_FILE=`dirname $LOG_FILE`
    mkdir --parents $DIR_PART_LOG_FILE
    touch $LOG_FILE
fi

/usr/bin/python src/exportClass.py | tee --append $LOG_FILE