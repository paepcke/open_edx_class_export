#!/bin/bash

# Build single-column CSV file with email addresses 
# of course participants who signed up since a given
# date, for courses that are open to the public.
#
# Usage:
#   o -u/--user           : MySQL user to use for query
#   o -p/--password       : Prompt for MySQL password
#   o -w/--mysqlpwd       : Provide MySQL pwd on command line
#   o -c/--cryptoPwd      : The password to use when encrypting .zip file; default: myClass
#   o -d/--destDir        : Destination directory for zip file
#   o -x/--xpunge         : If destination zip file exists, delete it. If not provided, refuse to overwrite
#   o -t/--testing        : If provided, uses db unittest.contents, instead of EdxForum.contents
#  date                   : Date from which on emails should be included.

USAGE="Usage: "`basename $0`" [-u user][-p password][-w mysqlpwd][-c cryptoPwd][-d destDir][-x xpunge][-i infoDest][-t testing] startDate"

# ----------------------------- Process CLI Parameters -------------

if [ $# -lt 1 ]
then
    echo $USAGE
    exit 1
fi

USERNAME=`whoami`
PASSWD=''
needPasswd=false
xpungeFiles=false
destDirGiven=false
DEST_DIR='/home/dataman/Data/CustomExcerpts'
INFO_DEST=''
pii=false
ENCRYPT_PWD='myClass'
RELATABLE=false
EMAIL_DB='edxprod'
TESTING=false

# Execute getopt
ARGS=`getopt -o "u:pw:c:xd:i:t" -l "user:,password,mysqlpwd:,cryptoPwd:,xpunge,destDir:,infoDest:,testing" \
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

    -c|--cryptoPwd)
      shift
      # Grab the option value:
      if [ -n "$1" ]
      then
        ENCRYPT_PWD=$1
        shift
      else
	echo $USAGE
	exit 1
      fi;;
    -d|--destDir)
      shift
      # Grab the option value:
      if [ -n "$1" ]
      then
        DEST_DIR=$1
	destDirGiven=true
        shift
      else
	echo $USAGE
	exit 1
      fi;;
    -x|--xpunge)
      xpungeFiles=true
      shift;;
    -i|--infoDest)
      shift
      # Grab the option value:
      if [ -n "$1" ]
      then
        INFO_DEST=$1
        shift
      else
	echo $USAGE
	exit 1
      fi;;
    -t|--testing)
      EMAIL_DB='unittest'
      TESTING=true
      shift;;
    --)
      shift
      break;;
  esac
done

# Make sure one arg is left after
# all the shifting above: the date
# from which on to get email addrs:

if [[ x$1 == 'x' ]]
then
  echo $USAGE
  exit 1
fi

DATE_JOINED=`date --iso-8601 --date=$1`
if [[ -z $DATE_JOINED ]]
then
    echo "Parameter $1 is not a valid date."
    exit 1
fi

#*********
# echo "Formatted date: $DATE_JOINED"
# exit
#*********

# ----------------------------- Determine Directory Path for CSV Tables -------------

# Create a prefix for the email table file name.
# The same prefix will also be used as
# the directory subname under ~dataman/Data/CustomExcerpts.
# Strategy: just concat 'Email_' and the email start date.

# The following SED expression has three repetitions
# of \([^/]*\)\/, which means all letters that are
# not forward slashes (the '[^/]*), followed by a 
# forward slash (the '\/'). The forward slash must
# be escaped b/c it's special in SED. 
# The escaped parentheses pairs form a group,
# which we then recall later, in the substitution
# part with \2 and \3 (the '/\2_\3/' part):

DIR_LEAF='Email_'$DATE_JOINED

#******************
#echo "DEST_LEAF after first xform: '$DIR_LEAF'<br>"
#******************

# If destination directory was not explicitly 
# provided, add a leaf directory to the
# standard directory to hold the result file:
if ! $destDirGiven
then
    DEST_DIR=$DEST_DIR/$DIR_LEAF
fi

#******************
#echo "DEST_DIR: $DEST_DIR\n\n"
#******************

# Make sure the directory path exists all the way:
mkdir -p $DEST_DIR

# Unfortunately, we cannot chmod when called
# from the Web, so this is commented out:
#chmod a+w $DEST_DIR

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

# Auth part for the subsequent mysql call:
if [ -z $PASSWD ]
then
    # Password empty...
    MYSQL_AUTH="-u $USERNAME"
else
    MYSQL_AUTH="-u $USERNAME -p$PASSWD"
fi

# ----------------------------- Create a full path for the forum table -------------

EMAIL_FNAME=$DEST_DIR/${DIR_LEAF}_Email.csv
ZIP_FNAME=$DEST_DIR/${DIR_LEAF}_Email.csv.zip

#************
# echo "EMAIL_FNAME: $EMAIL_FNAME"
# echo "ZIP_FNAME: $ZIP_FNAME"
# exit
#************

# ----------------------------- If requested on CL: Punt if tables exist -------------

# Refuse to overwrite existing files, unless the -x option
# was present in the CL:

# Check pre-existence of .zip file:
if [ -e $ZIP_FNAME ]
then
	if $xpungeFiles
	then
	    echo "Removing existing zipped csv file $ZIP_FNAME<br>"
	    rm $ZIP_FNAME
	else
	    echo "File $ZIP_FNAME already exists; aborting.<br>"
	    # Zero out the file in which we are to list
	    # the names of the result files:
	    if [ ! -z $INFO_DEST ]
	    then
		truncate -s 0 $INFO_DEST
	    fi
	    exit 1
	fi
fi

# ----------------------------- Create MySQL Command -------------

EMAIL_TMP_FILE=`mktemp -p /tmp`
PREVIEW_TMP_FILE=`mktemp -p /tmp`

# Ensure the files are cleaned up when script exits:
trap "rm -f $EMAIL_TMP_FILE $PREVIEW_TMP_FILE" EXIT

# MySQL will be asked to output emails to the EMAIL_TMP_FILE.
# The above mktemp created the (empty) file, and MySQL will
# refuse to write to it, unless:
unlink $EMAIL_TMP_FILE

EXPORT_EMAIL_CMD=" \
 USE "$EMAIL_DB"; \
 SELECT email \
 INTO OUTFILE '"$EMAIL_TMP_FILE"' \
  FIELDS TERMINATED BY ',' \
  LINES TERMINATED BY '\r\n' \
  FROM edxprod.student_courseenrollment LEFT JOIN edxprod.auth_user \
         ON edxprod.student_courseenrollment.user_id = edxprod.auth_user.id, \
       Edx.CourseInfo \
 WHERE created > '"$DATE_JOINED"' 
   AND Edx.CourseInfo.course_display_name = course_id \
   AND not Edx.CourseInfo.is_internal;"

# ----------------------------- Execute the Main MySQL Command -------------

#********************
#echo "MYSQL_AUTH: $MYSQL_AUTH"
#echo "EXPORT_EMAIL_CMD: $EXPORT_EMAIL_CMD"
#exit 0
*******************

echo "Creating email list ...<br>"

# Make pipe fail with error code saved: If pipefail is enabled, 
# the pipeline's return status is the value of the last (rightmost) 
# command to exit with a non-zero status, or zero if all commands 
# exit successfully:

set -o pipefail

# Exit on error:
set -e
echo "$EXPORT_EMAIL_CMD" | mysql $MYSQL_AUTH

echo "Done creating email list ...<br>"

#**********
#echo "Email file: " $EMAIL_TMP_FILE
#exit
#**********

# ----------------------------- Clean the emails -------------

echo 'Cleaning email list...<br>'
cat $EMAIL_TMP_FILE | sed '/^noreply/d' > $EMAIL_FNAME
echo 'Done cleaning email list...<br>'

# ---------------- Write File Size and Five Sample Lines to $INFO_DEST -------------

# Write path to the encrypted zip file to 
# path the caller provided:
if [ ! -z $INFO_DEST ]
then
	echo $ZIP_FNAME > $INFO_DEST
	echo "Appending number of lines to $INFO_DEST<br>"
	wc -l $EMAIL_FNAME | sed -n "s/\([0-9]*\).*/\1/p" >> $INFO_DEST 

	# Separator between the above table info and the
	# start of the sample lines. That division could
	# be made based on knowing that email lists only consists
	# of one table; but that is not true of other exports.
	# so the separator is required everywhere:
	echo 'herrgottzemenschnochamal!' >> $INFO_DEST

	echo "Appending sample lines to $INFO_DEST<br>"
	head -n5 $EMAIL_FNAME >> $INFO_DEST
fi


# ----------------------- Zip and Encrypt -------------

#***********
# echo "ENCRYPT_PWD: '"$ENCRYPT_PWD"'"
# echo "ZIP_FNAME: '"$ZIP_FNAME"'"
# echo "EMAIL_FNAME: '"$EMAIL_FNAME"'"
# echo "Zip command: zip --junk-paths --password "$ENCRYPT_PWD $ZIP_FNAME $EMAIL_FNAME
# exit 0
#***********

echo "Encrypting email list to $ZIP_FNAME...<br>"
# The --junk-paths puts just the files into
# the zip, not all the directories on their
# path from root to leaf:
zip --junk-paths --password $ENCRYPT_PWD $ZIP_FNAME $EMAIL_FNAME
rm $EMAIL_FNAME
chmod 644 $ZIP_FNAME
exit 0
