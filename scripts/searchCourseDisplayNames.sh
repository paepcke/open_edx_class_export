#!/bin/bash

# Outputs all course_display_name(s) and their respective
# enrollment. Output sampl:
#
#    Medicine/HRP214/Winter2014	32
#    Medicine/HRP258/Statistics_in_Medicine	26415
#    Medicine/HRP259/Fall2013	74
#
# Optionally, a MySQL regex pattern can be provided, which
# filters the course names. Source of the result is table
# student_courseenrollment.
#
# Independently of this regex pattern, the script tries to filter out
# course names that are clearly just tests, or course name
# misspellings that pollute the log files. Part of this filtering is
# that only courses with enrollment numbers greater than
# $MIN_ENROLLMENT.
#
# This script may be used from the command line. It is also used
# by exportClass.py in open_edx_class_export.

USAGE="Usage: "`basename $0`" [-u uid][-p][-w mySqlPwd][--silent][-q quarter][-y academicYear] [courseNamePattern]"

# ----------------------------- Process CLI Parameters -------------

USERNAME=`whoami`
PASSWD=''
SILENT=false
COURSE_SUBSTR='%'
QUARTER='%'
ACADEMIC_YEAR='%'
needPasswd=false

# Get directory in which this script is running,
# and where its support scripts therefore live:
currScriptsDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Number of course enrollees that a course
# must have in order to be listed:

MIN_ENROLLMENT=9

# Execute getopt
ARGS=`getopt -o "hu:pw:sq:y:" -l "help,user:,password,mysqlpwd:,silent,quarter,academic_year" \
      -n "getopt.sh" -- "$@"`
 
#Bad arguments
if [ $? -ne 0 ];
then
  exit 1
fi
 
# A little magic
eval set -- "$ARGS"
 
# Now go through all the options
while true;
do
  case "$1" in
    -h|--help)
      echo $USAGE
      exit 0;;
    -u|--user)
      shift
      # Grab the option value
      # unless it's null:
      if [ -n "$1" ]
      then
        USERNAME=$1
        shift
      else
	echo $USAGE
	exit 1
      fi;;
 
    -p|--password)
      needPasswd=true
      shift;;

    -s|--silent)
      SILENT=true
      shift;;

    -w|--mysqlpwd)
      shift
      # Grab the option value:
      if [ -n "$1" ]
      then
        PASSWD=$1
	needPasswd=false
        shift
      else
	echo $USAGE
	exit 1
      fi;;

    -q|--quarter)
      shift
      # Grab the option value
      # unless it's null:
      if [ -n "$1" ]
      then
        QUARTER=$1
        shift
      else
	echo $USAGE
	exit 1
      fi;;

    -y|--academic_year)
      shift
      # Grab the option value
      # unless it's null:
      if [ -n "$1" ]
      then
        ACADEMIC_YEAR=$1
        shift
      else
	echo $USAGE
	exit 1
      fi;;

    --)
      shift
      break;;
  esac
done

# Make sure one arg is left after
# all the shifting above: the search
# pattern for the course name:

if [ -z $1 ]
then
  COURSE_SUBSTR='%'
else
  COURSE_SUBSTR=$1
fi


# ----------------------------- Process or Lookup the Password -------------

if $needPasswd
then
    # The -s option suppresses echo:
    read -s -p "Password for user '$USERNAME' on `hostname`'s MySQL server: " PASSWD
    echo
else
    # MySQL pwd may have been provided via the -w option:
    if [ -z $PASSWD ]
    then
	# Password was not provided with -w option.
        # Get home directory of whichever user will
        # log into MySQL:
	HOME_DIR=$(getent passwd $USERNAME | cut -d: -f6)
        # If the home dir has a readable file called mysql in its .ssh
        # subdir, then pull the pwd from there:
	if test -f $HOME_DIR/.ssh/mysql && test -r $HOME_DIR/.ssh/mysql
	then
	    PASSWD=`cat $HOME_DIR/.ssh/mysql`
	fi
    fi
fi

#*************
# echo "Course substr: '$COURSE_SUBSTR'"
# echo "HOME_DIR: $HOME_DIR"
# echo "User: $USERNAME"
# echo "PWD: '$PASSWD'"
# if [ -z $PASSWD ]
# then
#     echo "PWD empty"
# else
#     echo "PWD full"
# fi
# echo "COURSE_SUBSTR: $COURSE_SUBSTR"
# exit 0
#*************

# Auth part for the subsequent mysql call:
if [ -z $PASSWD ]
then
    # Password empty...
    MYSQL_AUTH="-u $USERNAME"
else
    MYSQL_AUTH="-u $USERNAME -p$PASSWD"
fi

# Use edxprod.student_coursenrollment to find courses
# with enrollment greater than $MIN_ENROLLMENT. This constraint filters
# some course entries whose names are misspelled. The
# " AS '' " terms cause the column headers to be suppressed.
# That's useful when invoking this script from Python, which
# manages its own UI. The most appropriate col headers would
# be 'course_display_name', 'enrollment':

# MYSQL_CMD="SELECT course_id AS 'course_display_name', COUNT(user_id) AS 'enrollment'
# 	   FROM student_courseenrollment
# 	   WHERE course_id LIKE '"$COURSE_SUBSTR"'
# 	   GROUP BY course_id 
#            HAVING COUNT(user_id) > "$MIN_ENROLLMENT"
#                OR course_id LIKE 'ohsx%';\G"

# Find all the requested quarter's courses,
# collecting them into Misc.RelevantCoursesTmp.
# Can't use CREATE TEMPORARY, b/c the following
# SELECT would try to open that table twice, which
# is illegal.
#
# Use student_courseenrollment to compute enrollment
# (summing students), and certificates_generatedcertificate
# to count certs awarded in this course:

MYSQL_CMD="CREATE DATABASE IF NOT EXISTS Misc;
           DROP TABLE IF EXISTS Misc.RelevantCoursesTmp;
	   CREATE TABLE Misc.RelevantCoursesTmp
	   (SELECT course_display_name, is_internal
	           FROM Edx.CourseInfo
	          WHERE quarter LIKE '"$QUARTER"'
	            AND academic_year LIKE '"$ACADEMIC_YEAR"'
                    AND course_display_name LIKE '"$COURSE_SUBSTR"'
	   ); 
	   SELECT SummedAwards.course_display_name,
	          theSummedUsers AS enrollment,     
	          IF(theSummedAwards IS NULL,0,theSummedAwards) AS num_certs,
	          IF(theSummedAwards IS NULL,0,100*theSummedAwards/theSummedUsers) AS certs_ratio_perc,
	          SummedUsers.is_internal
	   FROM (SELECT course_display_name, 
                 COUNT(user_id) AS theSummedUsers, 
                 IF(is_internal = 1, 'yes','no') AS is_internal
	           FROM Misc.RelevantCoursesTmp LEFT JOIN edxprod.student_courseenrollment 
	             ON Misc.RelevantCoursesTmp.course_display_name = edxprod.student_courseenrollment.course_id
	         GROUP BY course_display_name
	        ) AS SummedUsers
	      LEFT JOIN
	        (SELECT course_display_name, SUM(status = 'downloadable') AS theSummedAwards
	           FROM Misc.RelevantCoursesTmp LEFT JOIN edxprod.certificates_generatedcertificate
	             ON Misc.RelevantCoursesTmp.course_display_name = certificates_generatedcertificate.course_id
	         GROUP BY course_display_name
	        ) AS SummedAwards
	       ON SummedUsers.course_display_name = SummedAwards.course_display_name;"

#*************
#echo "MYSQL_CMD: $MYSQL_CMD"
#*************

# --skip-column-names suppresses the col name 
# headers in the output:
if $SILENT
then
    COURSE_NAMES=`echo $MYSQL_CMD | mysql --skip-column-names $MYSQL_AUTH edxprod`
else
    COURSE_NAMES=`echo $MYSQL_CMD | mysql $MYSQL_AUTH edxprod`
fi

#*************
#echo "COURSE_NAMES: "$COURSE_NAMES
#exit 0
#*************

# In the following the first 'sed' call removes the
# line: "********** 1. row *********" and following rows.
# The second 'sed' call removes everything of the second
# line up to the ': '. Together this next line creates
# four (still tab-separated) columns.
# course mentions:
NAME_ACTIVITY_LINES=`echo "$COURSE_NAMES" | sed '/[*]*\s*[0-9]*\. row\s*[*]*$/d' | sed 's/[^:]*: //'`

# Now throw out all lines that are clearly 
# bad course names stemming from people creating
# test courses without adhering to any naming pattern;
# On the way, replace tabs with commas:
echo "${NAME_ACTIVITY_LINES}" | sed "s/\t/,/g"  | $currScriptsDir/filterCourseNames.sh
exit 0
