#!/bin/bash

# Outputs all course_display_name(s) and their respective
# enrollment, plus number of awarded certificates,
# ratio of certsAwarded to enrollment, and whether the course is
# Stanford-internal or not. The amount of information is controlled
# by CLI switches.
# Output sample:
#
#    Medicine/HRP214/Winter2014
#    Medicine/HRP214/Winter2014,26415
#    Medicine/HRP258/Statistics_in_Medicine,26415,500,0.0189,no
#
# Optionally, a MySQL regex pattern can be provided, which
# filters the course names.
#
# Beyond the regex pattern, course names to include in the output 
# may be from one of two sources, depending on the presence of
# the --byActivity option: if that option is provided the script includes 
# courses that show any activity during the requested quarter(s)
# and academic year. This option takes a little over 2 minutes,
# and will include more courses, b/c course material is accessed
# beyond course end dates.
# When the --byActivity option is absent, course names that ran during the 
# requested quarter(s)/year(s) are taken from table CourseInfo,
# which is constructed from the OpenEdX modulestore. This case
# is fast (about 2sec).
#
# Also controlled by a CLI is the minimum number of enrollments
# a course must have to be included in the output: -m/--minEnrollment.
# Default is 9.
#
# Independently of the course name regex pattern and the enrollment
# minimum, the script tries to filter out
# course names that are clearly just tests, or course name
# misspellings that pollute the log files. This filter is always
# applied. 
#
# This script may be used from the command line. It is also used
# by exportClass.py in open_edx_class_export.

USAGE="Usage: "`basename $0`" [-u uid][-p][-w mySqlPwd][--silent][-q quarter][-y academicYear][-m minEnrollment][-a allCourses][-b byActivity][-o outfileName][courseNamePattern]"

HELP_TEXT="-u uid\t\t: the MySQL user id\r\n
           -p\t\t: ask for MySQL pwd\n
           -w pwd\t\t: provide pwd in CLI\n
           -q\t\t: academic quarter: fall,winter,spring, or summer.\n
                   \t\t\tDefault is all quarters.\n
           -y\t\t: academic year. Default is all years.\n
           -m\t\t: only include courses with at least minEnrollment learners\n
	   -a\t\t: include all courses\n
           -o\t\t: name of file into which to output\n
           --silent\t: no column headers are output\n
           --byActivity\t: determine relevant course names by activity rather than course schedule\n
"

# Get MySQL version on this machine
MYSQL_VERSION=$(mysql --version | sed -ne 's/.*Distrib \([0-9][.][0-9]\).*/\1/p')
if [[ $MYSQL_VERSION > 5.5 ]]
then 
    MYSQL_VERSION='5.6+'
else 
    MYSQL_VERSION='5.5'
fi


# ----------------------------- Process CLI Parameters -------------

USERNAME=`whoami`
PASSWD=''
SILENT=false
COURSE_SUBSTR='%'
QUARTER='%'
ACADEMIC_YEAR='%'
BY_ACTIVITY=0
ALL_COURSES=0
needPasswd=false
OUTFILE_NAME=''

# Get directory in which this script is running,
# and where its support scripts therefore live:
currScriptsDir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Number of course enrollees that a course
# must have in order to be listed:

MIN_ENROLLMENT=9

# Execute getopt
ARGS=`getopt -o "hu:pw:sq:y:m:abo:" -l "help,user:,password,mysqlpwd:,silent,quarter:,academicYear:,minEnrollment:,allCourses,byActivity,outfileName:" \
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
      # The -e activates backslash cmd execution:
      echo -e $HELP_TEXT
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

    -b|--byActivity)
      BY_ACTIVITY=true
      shift;;

    -m|--minEnrollment)
      shift
      # Grab the option value:
      if [ -n "$1" ]
      then
        MIN_ENROLLMENT=$1
	re='^[0-9]+$'
	if ! [[ $MIN_ENROLLMENT =~ $re ]]
	then
	   echo "Minimum enrollment must be an integer. Was '$MIN_ENROLLMENT'"
	   echo $USAGE
	   exit 1
	fi
        shift
      else
	echo $USAGE
	exit 1
      fi;;

    -a|--allCourses)
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

    -y|--academicYear)
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

    -o|--outfileName)
      shift
      # Grab the file name: value
      # unless it's null:
      if [ -n "$1" ]
      then
        OUTFILE_NAME=$1
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
# echo "Output File: $OUTFILE_NAME"
# echo "COURSE_SUBSTR: $COURSE_SUBSTR"
# exit 0
#*************

# Auth part for the subsequent mysql call:
if [[ $MYSQL_VERSION == '5.6+' ]]
then
    MYSQL_AUTH="--login-path=root"
else
    if [ -z $PASSWD ]
    then
        # Password empty...
        MYSQL_AUTH="-u $USERNAME"
    else
        MYSQL_AUTH="-u $USERNAME -p$PASSWD"
    fi
fi



# Use edxprod.true_coursenrollment to find courses
# with enrollment greater than $MIN_ENROLLMENT. This constraint filters
# some course entries whose names are misspelled. The
# " AS '' " terms cause the column headers to be suppressed.
# That's useful when invoking this script from Python, which
# manages its own UI. The most appropriate col headers would
# be 'course_display_name', 'enrollment':

if [[ $ALL_COURSES == 1 ]]
then
    ENROLLMENT_CONDITION=""
else
    ENROLLMENT_CONDITION="WHERE theSummedUsers > $MIN_ENROLLMENT"
fi;

# The Quarter start and end dates:
FALL_START='-09-01'
FALL_END='-11-30'
WINTER_START='-12-01'
WINTER_END='-02-28'
SPRING_START='-03-01'
SPRING_END='-05-31'
SUMMER_START='-06-01'
SUMMER_END='-08-31'


# Ensure quarter lower case:
QUARTER=`echo $QUARTER | tr '[:upper:]' '[:lower:]'`

# Create a time MySQL time constraint, depending on
# which quarter is requested (or '%'):
if [[ $BY_ACTIVITY == 0 ]]
then
   TIME_CONSTRAINT="1"
else
    # Can't ask for byActivity for all years:
    if [[ $ACADEMIC_YEAR == '%' ]]
    then
        echo "Cannot ask for byActivity without providing an academic year (-y or --academicYear)"
	exit -1
    fi
    # Compute calendar year in which quarter will happen:
    CAL_YEAR=$((${ACADEMIC_YEAR}+1))

    if [[ $QUARTER == 'fall' ]]
    then
       TIME_CONSTRAINT="time BETWEEN '${ACADEMIC_YEAR}${FALL_START}' AND '${ACADEMIC_YEAR}${FALL_END}'"
    elif [[ $QUARTER == 'winter' ]]
    then
       TIME_CONSTRAINT="time BETWEEN '${ACADEMIC_YEAR}${WINTER_START}' AND '${CAL_YEAR}${WINTER_END}'"
    elif [[ $QUARTER == 'spring' ]]
    then
       TIME_CONSTRAINT="time BETWEEN '${CAL_YEAR}${SPRING_START}' AND '${CAL_YEAR}${SPRING_END}'"
    elif [[ $QUARTER == 'summer' ]]
    then
       TIME_CONSTRAINT="time BETWEEN '${CAL_YEAR}${SUMMER_START}' AND '${CAL_YEAR}${SUMMER_END}'"
    elif [[ $QUARTER == '%' ]]
    then
       TIME_CONSTRAINT="(time BETWEEN '${ACADEMIC_YEAR}${FALL_START}' AND ${ACADEMIC_YEAR}${FALL_END}' \
                      OR time BETWEEN '${CAL_YEAR}${WINTER_START}' AND '${CAL_YEAR}${WINTER_END}' \
                      OR time BETWEEN '${CAL_YEAR}${SPRING_START}' AND '${CAL_YEAR}${SPRING_END}' \
                      OR time BETWEEN '${CAL_YEAR}${SUMMER_START}' AND '${CAL_YEAR}${SUMMER_END}' \
                        )"
    else TIME_CONSTRAINT=1
    fi
fi
#****************
#echo "Time constraint: '"$TIME_CONSTRAINT"'"
#exit 0
#****************

# Create a SQL snippet that outputs MySQL into a file,
# if outFileName was provided on CL:

if [[ -z $OUTFILE_NAME ]]
then
    MYSQL_OUTPUT_SPEC=''
else
    MYSQL_OUTPUT_SPEC="INTO OUTFILE '"$OUTFILE_NAME"' FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' LINES TERMINATED BY '\n'"
fi

#****************
#echo "Output file spec: '"$MYSQL_OUTPUT_SPEC"'"
#exit 0
#****************

# Find all the requested quarter's courses,
# collecting them into Misc.RelevantCoursesTmp.
# Can't use CREATE TEMPORARY, b/c the following
# SELECT would try to open that table twice, which
# is illegal.

if [[ $BY_ACTIVITY == 0 ]]
then
    COURSE_NAME_CREATION_CMD="DROP TABLE IF EXISTS Misc.RelevantCoursesTmp;
    			  CREATE TABLE Misc.RelevantCoursesTmp
    			  SELECT course_display_name, quarter, academic_year
    			    FROM CourseInfo
    			   WHERE quarter LIKE '"$QUARTER"'
    			     AND academic_year LIKE '"$ACADEMIC_YEAR"';
                             "
else
    COURSE_NAME_CREATION_CMD="DROP TABLE IF EXISTS Misc.RelevantCoursesTmp;
    			  CREATE TABLE Misc.RelevantCoursesTmp
    			  SELECT course_display_name, quarter, academic_year
                          FROM CourseInfo
			  WHERE EXISTS(SELECT 1
			               FROM EventXtract
			  	      WHERE EventXtract.course_display_name = CourseInfo.course_display_name
			   	       AND $TIME_CONSTRAINT
			              );
                          "
fi			      

#*************
#echo "COURSE_NAME_CREATION_CMD: "$COURSE_NAME_CREATION_CMD
#exit 0
#*************

# Use true_courseenrollment to compute enrollment
# (summing students), and certificates_generatedcertificate
# to count certs awarded in this course. Note, if provided
# with a quarter (-q) and an academic year (-y), this cmd
# picks only courses that had at least one activity during
# that quarter. Without both of these available, the 
# start quarter in CourseInfo is used:


MYSQL_CMD="SELECT 'platform','course_display_name','quarter', 'academic_year','enrollment','num_certs', 'certs_ratio', 'is_internal'
           UNION
           SELECT 'OpenEdX',
                  SummedAwards.course_display_name,
                  SummedUsers.quarter,
                  SummedUsers.academic_year,
	          theSummedUsers AS enrollment,     
	          IF(theSummedAwards IS NULL,0,theSummedAwards) AS num_certs,
	          IF(theSummedAwards IS NULL,0,100*theSummedAwards/theSummedUsers) AS certs_ratio_perc,
                  IF(is_internal IS NULL,'n/a', IF(is_internal = 0,'no','yes')) AS is_internal
           "$MYSQL_OUTPUT_SPEC"
	   FROM (SELECT Misc.RelevantCoursesTmp.course_display_name, 
                 COUNT(user_id) AS theSummedUsers,
                 Misc.RelevantCoursesTmp.quarter AS quarter,
                 Misc.RelevantCoursesTmp.academic_year AS academic_year
	           FROM Misc.RelevantCoursesTmp LEFT JOIN edxprod.true_courseenrollment 
	             ON Misc.RelevantCoursesTmp.course_display_name = edxprod.true_courseenrollment.course_display_name
	         GROUP BY Misc.RelevantCoursesTmp.course_display_name
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
               ON IsInternalInfo.course_display_name = SummedAwards.course_display_name
            "$ENROLLMENT_CONDITION";
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
# headers in the output. If OUTPUT_FILENAME is
# given on the CL, then the result goes into a file,
# which we look at further down. In that case
# the pipes do nothing. But if output goes to stdout,
# then the first pipe turns tabs to commas, and the 
# second removes lines with bad course names:

if $SILENT
then
    echo $MYSQL_CMD | mysql --skip-column-names $MYSQL_AUTH edxprod | sed "s/\t/,/g" | $currScriptsDir/filterCourseNames.sh 
else
    echo $MYSQL_CMD | mysql $MYSQL_AUTH edxprod | sed "s/\t/,/g" | $currScriptsDir/filterCourseNames.sh 
fi

if [[ -z $OUTFILE_NAME ]]
then
    exit 0
fi

# Query result went to a file. Pipe that file through
# script filterCourseNames.sh to remove bad course names:

TMP_FILE=$(mktemp -t tmpQuarterlyRepXXXXXXXXXX)

#*********
#echo 'test/sandbox/foobar' >> $OUTFILE_NAME
#cat $OUTFILE_NAME
#echo "Tmpfile: $TMP_FILE"
#*********

cat ${OUTFILE_NAME} | $currScriptsDir/filterCourseNames.sh > $TMP_FILE
# Note: cannot use mv now, b/c OUTFILE_NAME is owned
# by MySQL:
cp ${TMP_FILE} ${OUTFILE_NAME}
rm ${TMP_FILE}
exit 0
