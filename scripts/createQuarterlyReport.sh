#!/bin/bash

# This older version of searchCourseDisplayNames.sh is more general
# than the modern one. This one here includes the functionality of
# createQuarterlyReport.sh. 

# Outputs all course_display_name(s) and, optionally, their respective
# enrollment, plus, optionally, number of awarded certificates,
# ratio of certsAwarded to enrollment, and whether the course is
# Stanford-internal or not. The amount of information is controlled
# by CLI switches.
# Output sample:
#
#    Medicine/HRP214/Winter2014
#    Medicine/HRP214/Winter2014,26415
#    Medicine/HRP258/Statistics_in_Medicine,26415,500,0.0189,no
#
# By default all stats are output.
#
# Optionally, a MySQL regex pattern can be provided, which
# filters the course names. Source of the result is table
# student_courseenrollment.
#
# Also controlled by a CLI switch is whether only courses with
# minimum enrollment $MIN_ENROLLMENT are to be included. This
# filter is not applied in two cases:
#
#    - the -a (all courses) is requested, in which
#      case only course names are returned.
#    - the -n (no statistics) option is not provided.
#      That is, when full stats are requested (enrollment,
#      certificates, etc.), then even low enrollment courses
#      are included.
#
# Independently of the course name regex pattern and the enrollment
# minimum, the script tries to filter out
# course names that are clearly just tests, or course name
# misspellings that pollute the log files. This filter is always
# applied. 
#
# This script may be used from the command line. It is also used
# by exportClass.py in open_edx_class_export.

USAGE="Usage: "`basename $0`" [-u uid][-p][-w mySqlPwd][--silent][-q quarter][-y academicYear][-e enrollmentOnly][-n noStats][-a allCourses][courseNamePattern]"

HELP_TEXT="-u uid\t\t: the MySQL user id\r\n
           -p\t\t: ask for MySQL pwd\n
           -w pwd\t\t: provide pwd in CLI\n
           -q\t\t: academic quarter: fall,winter,spring, or summer.\n
                   \t\t\tDefault is all quarters.\n
           -y\t\t: academic year. Default is all years.\n
           -e\t\t: only output course names and enrollment\n
                   \t\t\tMinimum enrollment is applied\n
           -n\t\t: no statistics at all: only course names are\n
                   \t\t\treturned. Minimum enrollment is applied \n
           --silent\t: not column headers are output\n
"

# ----------------------------- Process CLI Parameters -------------

USERNAME=`whoami`
PASSWD=''
SILENT=false
COURSE_SUBSTR='%'
QUARTER='%'
ACADEMIC_YEAR='%'
ENROLL_ONLY=0
SKIP_STATS=0
ALL_COURSES=0
needPasswd=false

# Get directory in which this script is running,
# and where its support scripts therefore live:
currScriptsDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Number of course enrollees that a course
# must have in order to be listed:

MIN_ENROLLMENT=9

# Execute getopt
ARGS=`getopt -o "hu:pw:sq:y:ena" -l "help,user:,password,mysqlpwd:,silent,quarter,academic_year,enroll_only,no_stats,all_courses" \
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

    -e|--enroll_only)
      ENROLL_ONLY=1
      shift;;

    -n|--no_stats)
      SKIP_STATS=1
      shift;;

    -a|--all_courses)
      ALL_COURSES=1
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

ENROLLMENT_CONDITION="HAVING theSummedUsers > $MIN_ENROLLMENT"

if [[ $ALL_COURSES == 1 ]]
then
    ENROLLMENT_CONDITION=""
else
    ENROLLMENT_CONDITION="HAVING theSummedUsers > $MIN_ENROLLMENT"
fi;

# Find all the requested quarter's courses,
# collecting them into Misc.RelevantCoursesTmp.
# Can't use CREATE TEMPORARY, b/c the following
# SELECT would try to open that table twice, which
# is illegal.
#
# Use student_courseenrollment to compute enrollment
# (summing students), and certificates_generatedcertificate
# to count certs awarded in this course. Note, if provided
# with a quarter (-q) and an academic year (-y), this cmd
# picks only courses that had at least one activity during
# that quarter. Without both of these available, the 
# start quarter in CourseInfo is used:

if [[ $QUARTER != '%' && $ACADEMIC_YEAR != '%' ]]
then
    # Quarter and academic year of desired courses
    # were specified explicitly on the CL:
    COURSE_NAME_CREATION_CMD="DROP TABLE IF EXISTS Misc.RelevantCoursesTmp;
          CREATE TABLE Misc.RelevantCoursesTmp
	  SELECT DISTINCT EdxTrackEvent.course_display_name
	    FROM EdxTrackEvent
	   WHERE EdxTrackEvent.course_display_name LIKE '%'
    	     AND time BETWEEN makeLowQuarterDate('"$QUARTER"', "$ACADEMIC_YEAR") 
                        AND makeUpperQuarterDate('"$QUARTER"', "$ACADEMIC_YEAR");
    "
#     COURSE_NAME_CREATION_CMD="DROP TABLE IF EXISTS Misc.RelevantCoursesTmp;
#           CREATE TABLE Misc.RelevantCoursesTmp
# 	  SELECT DISTINCT QualifyingCourseNames.course_display_name, 
# 	                  IF(is_internal IS NULL,'n/a', IF(is_internal = 0,'no','yes')) AS is_internal
# 	    FROM 
# 	          (SELECT DISTINCT course_display_name
# 	             FROM EdxTrackEvent 
# 	            WHERE course_display_name LIKE '"$COURSE_SUBSTR"' 
# 	              AND EXISTS(SELECT 1 
# 	                  	FROM EdxTrackEvent 
# 	                 	       WHERE time BETWEEN makeLowQuarterDate('"$QUARTER"', "$ACADEMIC_YEAR") 
#                                                       AND makeUpperQuarterDate('"$QUARTER"', "$ACADEMIC_YEAR") 
# 	                  	 AND isUserEvent(event_type) 
# 	  			 AND isTrueCourseName(course_display_name)
# 	  			 AND quarter = '"${QUARTER}${ACADEMIC_YEAR}"' 
# 	                       )
# 	           ) AS QualifyingCourseNames
# 	  	 LEFT JOIN CourseInfo
# 	  	 ON CourseInfo.course_display_name = QualifyingCourseNames.course_display_name;
# 	  "
# elif [[ $QUARTER == '%' && $ACADEMIC_YEAR != '%' ]]
# then
#     # Quarter is wildcard, but year is specified:
#     COURSE_NAME_CREATION_CMD="DROP TABLE IF EXISTS Misc.RelevantCoursesTmp;
#           CREATE TABLE Misc.RelevantCoursesTmp
# 	  SELECT DISTINCT QualifyingCourseNames.course_display_name, 
# 	                  IF(is_internal IS NULL,'n/a', IF(is_internal = 0,'no','yes')) AS is_internal
# 	    FROM 
# 	          (SELECT DISTINCT course_display_name
# 	             FROM EdxTrackEvent 
# 	            WHERE course_display_name LIKE '"$COURSE_SUBSTR"' 
# 	              AND EXISTS(SELECT 1 
# 	                  	FROM EdxTrackEvent 
# 	                 	       WHERE YEAR(time) = "$ACADEMIC_YEAR"
# 	                  	 AND isUserEvent(event_type) 
# 	  			 AND isTrueCourseName(course_display_name)
# 	                       )
# 	           ) AS QualifyingCourseNames
# 	  	 LEFT JOIN CourseInfo
# 	  	 ON CourseInfo.course_display_name = QualifyingCourseNames.course_display_name;
# 	  "
# elif [[ $QUARTER != '%' && $ACADEMIC_YEAR == '%' ]]
# then
#     # Year is wildcard, but quarter is specified
#     COURSE_NAME_CREATION_CMD="DROP TABLE IF EXISTS Misc.RelevantCoursesTmp;
#           CREATE TABLE Misc.RelevantCoursesTmp
# 	  SELECT DISTINCT QualifyingCourseNames.course_display_name, 
# 	                  IF(is_internal IS NULL,'n/a', IF(is_internal = 0,'no','yes')) AS is_internal
# 	    FROM 
# 	          (SELECT DISTINCT course_display_name
# 	             FROM EdxTrackEvent 
# 	            WHERE course_display_name LIKE '"$COURSE_SUBSTR"' 
# 	              AND EXISTS(SELECT 1 
# 	                  	FROM EdxTrackEvent 
#                                  WHERE dateInQuarter(time,'"$QUARTER"','%')
# 	                  	 AND isUserEvent(event_type) 
# 	  			 AND isTrueCourseName(course_display_name)
# 	                       )
# 	           ) AS QualifyingCourseNames
# 	  	 LEFT JOIN CourseInfo
# 	  	 ON CourseInfo.course_display_name = QualifyingCourseNames.course_display_name;
# 	  "

# else
#     # Year is wildcard, but quarter is specified
#     COURSE_NAME_CREATION_CMD="DROP TABLE IF EXISTS Misc.RelevantCoursesTmp;
#           CREATE TABLE Misc.RelevantCoursesTmp
# 	  SELECT DISTINCT QualifyingCourseNames.course_display_name, 
# 	                  IF(is_internal IS NULL,'n/a', IF(is_internal = 0,'no','yes')) AS is_internal
# 	    FROM 
# 	          (SELECT DISTINCT course_display_name
# 	             FROM EdxTrackEvent 
# 	            WHERE course_display_name LIKE '"$COURSE_SUBSTR"' 
# 	              AND isUserEvent(event_type) 
#                       AND isTrueCourseName(course_display_name)
# 	           ) AS QualifyingCourseNames
# 	  	 LEFT JOIN CourseInfo
# 	  	 ON CourseInfo.course_display_name = QualifyingCourseNames.course_display_name;
# 	  "
fi

MYSQL_CMD="SELECT 'platform','course_display_name','quarter', 'academic_year','enrollment','num_certs', 'certs_ratio', 'is_internal'
           UNION
           SELECT 'OpenEdX',
                  SummedAwards.course_display_name,
                  '"$QUARTER"',
                  '"$ACADEMIC_YEAR"',
	          theSummedUsers AS enrollment,     
	          IF(theSummedAwards IS NULL,0,theSummedAwards) AS num_certs,
	          IF(theSummedAwards IS NULL,0,100*theSummedAwards/theSummedUsers) AS certs_ratio_perc,
                  IF(is_internal IS NULL,'n/a', IF(is_internal = 0,'no','yes')) AS is_internal
	   FROM (SELECT course_display_name, 
                 COUNT(user_id) AS theSummedUsers
	           FROM Misc.RelevantCoursesTmp LEFT JOIN edxprod.student_courseenrollment 
	             ON Misc.RelevantCoursesTmp.course_display_name = edxprod.student_courseenrollment.course_id
	         GROUP BY course_display_name "$ENROLLMENT_CONDITION"
	        ) AS SummedUsers
	      LEFT JOIN
	        (SELECT course_display_name, SUM(status = 'downloadable') AS theSummedAwards
	           FROM Misc.RelevantCoursesTmp LEFT JOIN edxprod.certificates_generatedcertificate
	             ON Misc.RelevantCoursesTmp.course_display_name = certificates_generatedcertificate.course_id
	         GROUP BY course_display_name
	        ) AS SummedAwards
	       ON SummedUsers.course_display_name = SummedAwards.course_display_name
              LEFT JOIN
                (SELECT course_display_name, is_internal FROM Edx.CourseInfo) AS IsInternalInfo
               ON IsInternalInfo.course_display_name = SummedAwards.course_display_name;
           "

#*************
#echo "COURSE_NAME_CREATION_CMD: "$COURSE_NAME_CREATION_CMD
#echo "MYSQL_CMD: $MYSQL_CMD"
#exit 0
#*************

# Create auxiliary table with affected course names
# and their is_internal status:
echo $COURSE_NAME_CREATION_CMD | mysql $MYSQL_AUTH Edx

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
