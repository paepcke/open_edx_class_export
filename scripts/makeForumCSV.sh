#!/bin/bash

# Exports one online class' Forum entries to an encrypted zip file.
# Usage:
#   o -u/--user           : MySQL user to use for query
#   o -p/--password       : Prompt for MySQL password
#   o -w/--mysqlpwd       : Provide MySQL pwd on command line
#   o -c/--cryptoPwd      : The password to use when encrypting .zip file; default: myClass
#   o -d/--destDir        : Destination directory for zip file
#   o -x/--xpunge         : If destination zip file exists, delete it. If not provided, refuse to overwrite
#   o -r/--relatable      : Provide the anon_screen_name column so that correlations between log data and forum can be explored
#   o -t/--testing        : If provided, uses db unittest.contents, instead of EdxForum.contents
#  courseNamePattern      : MySQL style pattern name of course whose Forum entries to export.
#
# The directory path and zip file name are determined as per
# section 'Determine Directory Path for CSV Tables'.
#
# The infoDest, if provided will hold in its first line
# the absolute path of the .zip file. The second line will
# hold the number of bytes in the (uncompressed) Forum file.
# Five following lines contain the first five lines of the forum file.
# Those lines can be used by callers to provide a few sample entries
# to users.

USAGE="Usage: "`basename $0`" [-u user][-p password][-w mysqlpwd][-c cryptoPwd][-d destDir][-x xpunge][-i infoDest][-r relatable][-t testing] courseNamePattern"

# Get MySQL version on this machine
MYSQL_VERSION=$(mysql --version | sed -ne 's/.*Distrib \([0-9][.][0-9]\).*/\1/p')
if [[ $MYSQL_VERSION > 5.5 ]]
then
    MYSQL_VERSION='5.6+'
else
    MYSQL_VERSION='5.5'
fi

# ----------------------------- Process CLI Parameters -------------

if [ $# -lt 1 ]
then
    echo $USAGE
    exit 1
fi

USERNAME=`whoami`
PASSWD=''
COURSE_SUBSTR=''
needPasswd=false
xpungeFiles=false
destDirGiven=false
DEST_DIR='/home/dataman/Data/CustomExcerpts'
INFO_DEST=''
pii=false
ENCRYPT_PWD='myClass'
RELATABLE=false
FORUM_DB='EdxForum'
TESTING=false

# Execute getopt
ARGS=`getopt -o "u:pw:c:xd:i:rt" -l "user:,password,mysqlpwd:,cryptoPwd:,xpunge,destDir:,infoDest:,relatable,testing" \
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
    -r|--relatable)
      RELATABLE=true
      shift;;
    -t|--testing)
      FORUM_DB='unittest'
      TESTING=true
      shift;;
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
  echo $USAGE
  exit 1
fi
COURSE_SUBSTR=$1

# ----------------------------- Determine Directory Path for CSV Tables -------------

# Create a prefix for the forum table file name.
# The same prefix will also be used as
# the directory subname under ~dataman/Data/CustomExcerpts.
# Strategy: if the COURSE_SUBSTR is a full, clean
#   course triplet part1/part2/part3, we use part1_part2_part3.
#   If we cannot create this part2_part3 name, b/c
#   $COURSE_SUBSTR is of a non-standard form, then
#   we use all of $COURSE_SUBSTR.
#   Finally, in either case, All MySQL regex '%' chars
#   are replaced by '_any'.
# Ex:
#   Engineering/CS106A/Fall2013 => Engineering_CS106A_Fall2013
#   Chemistry/CH%/Summer => Chemistry_CH_any_Summer

# The following SED expression has three repetitions
# of \([^/]*\)\/, which means all letters that are
# not forward slashes (the '[^/]*), followed by a
# forward slash (the '\/'). The forward slash must
# be escaped b/c it's special in SED.
# The escaped parentheses pairs form a group,
# which we then recall later, in the substitution
# part with \2 and \3 (the '/\2_\3/' part):

DIR_LEAF=`echo $COURSE_SUBSTR | sed -n "s/\([^/]*\)\/\([^/]*\)\/\(.*\)/\1_\2_\3/p"`

#******************
#echo "DEST_LEAF after first xform: '$DIR_LEAF'<br>"
#echo "COURSE_SUBSTR after first xform: '$COURSE_SUBSTR'<br>"
#******************

if [ -z $DIR_LEAF ]
then
    DIR_LEAF=`echo $COURSE_SUBSTR | sed s/[%]/_any/g`
else
    # Len of DIR_LEAF > 0.
    # Replace any '%' MySQL wildcards with
    # '_All':
    DIR_LEAF=`echo $DIR_LEAF | sed s/[%]/_any/g`
fi

#******************
#echo "DEST_LEAF after second xform: '$DIR_LEAF'<br>"
#echo "DEST_LEAF after second xform: '$DIR_LEAF'" > /tmp/trash.log
#******************

# Last step: remove all remaining '/' chars,
# and any leading underscore(s), if present; the
# -E option enables extended regexp, which seems
# needed for the OR option: \|
DIR_LEAF=`echo $DIR_LEAF | sed -E s/^[_]*\|[/]//g`

#******************
#echo "DEST_LEAF after third xform: '$DIR_LEAF'<br>"
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
# echo "DEST_DIR: '$DEST_DIR'"
# echo "COURSE_SUBSTR: $COURSE_SUBSTR"
# echo "DIR_LEAF: $DIR_LEAF"
# if $pii
# then
#    echo "Want pii"
# else
#    echo "No pii"
# fi
# if [ -z $ENCRYPT_PWD ]
# then
#     echo "ENCYPT_PWD empty"
# else
#     echo "Encryption pwd: $ENCRYPT_PWD"
# fi
#  exit 0
#*************

# ----------------------------- Get column headers for Forum Table -------------

# MySQL does not output column name headers for CSV files, only
# for tsv. So need to get those; it's messy. First, we get the
# column names into a bash variable. The var's content will look this
# this:
#
#*************************** 1. row ***************************
#GROUP_CONCAT(CONCAT("'",information_schema.COLUMNS.COLUMN_NAME,"'")): 'forum_post_id','anon_screen_name','type','anonymous',...
#
# Where the second row contains the table's columns
# after the colon. We use the sed command to get rid of the
# '**** 1. row ****' entirely. Then we use sed again to
# get rid of the 'GROUP_CONCAT...: 'part. We write the
# remaining info, the actual col names, to a tmp file. We
# do this for each of the three tables.
#
# Create all tmp files:

FORUM_HEADER_FILE=`mktemp -p /tmp`
PREVIEW_TMP_FILE=`mktemp -p /tmp`

# Ensure the files are cleaned up when script exits:
trap "rm -f $FORUM_HEADER_FILE, $PREVIEW_TMP_FILE" EXIT

# A tmp file for one table's csv data:
# Must be unlinked (the -u option), b/c
# otherwise MySQL complains that the file
# exists; The unlinking prevents us from
# deleting this file except as superuser.
# Need to fix that:

FORUM_VALUES_FNAME=`mktemp -u -p /tmp`

# Auth part for the subsequent three mysql calls:
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

# Start the Forum dump file by adding the column name header row:
FORUM_HEADER=`mysql $MYSQL_AUTH --batch -e "
              SELECT GROUP_CONCAT(CONCAT('\"',information_schema.COLUMNS.COLUMN_NAME,'\"'))
	      FROM information_schema.COLUMNS
	      WHERE TABLE_SCHEMA = '$FORUM_DB'
	         AND TABLE_NAME = 'contents'
	      ORDER BY ORDINAL_POSITION\G"`
# In the following the first 'sed' call removes the
# line: "********** 1. row *********" (see above).
# The second 'sed' call removes everything of the second
# line up to the ': '. The result finally is placed
# in a tempfile.

echo "${FORUM_HEADER}" | sed '/[*]*\s*1\. row\s*[*]*$/d' | sed 's/[^:]*: //'  | cat > $FORUM_HEADER_FILE

# Get column names without quotes around them,
# which the have in the col header row:
COL_NAMES=`cat $FORUM_HEADER_FILE | sed s/\"//g`


#*******************
# echo "Forum header line should be in $FORUM_HEADER_FILE"
# echo "Contents Forum header file:"
# cat $FORUM_HEADER_FILE
# exit 0
# echo "Dirleaf: $DIR_LEAF"
#*******************

# ----------------------------- Create a full path for the forum table -------------

FORUM_FNAME=$DEST_DIR/${DIR_LEAF}_Forum.csv
ZIP_FNAME=$DEST_DIR/${DIR_LEAF}_forum.csv.zip

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

# Create the MySQL export command that
# will write the table values. Two variants:
# with or without anon_screen_name filled in:

# Start by constructing the fields to export; first for relatable:
# The first IF branch creates something like:
# 'forum_post_id','EdxPrivate.idForum2Anon(forum_uid)','type','anonymous',...,0,body,...
# with the second ellipses being the rest of the forum columns.
# The first sed expression replaces anon_screen_name
# with EdxPrivate.idForum2Anon(forum_uid). That MySQL function
# call converts the uid scheme used in the forum
# table to an equivalent anon_screen_name value.
#
# The second sed expression causes MySQL to output the constant 0
# instead of the stored forum_uid. In the resulting table
# only the anon_screen_name is then usable as an identifier.
#
# If the forum output is to remain non-relatable to the rest
# of the data, then don't use sed, but retain the existing
# column names as the col names to output.

if $RELATABLE
then
    if $TESTING
    then
	COLS_TO_PULL=`echo $COL_NAMES | sed s/anon_screen_name/unittest.idForum2Anon\(forum_uid\)/ \
	    | sed s/[^\(]forum_uid/,'null'/`
    else
	COLS_TO_PULL=`echo $COL_NAMES | sed s/anon_screen_name/EdxPrivate.idForum2Anon\(forum_uid\)/ \
	    | sed s/[^\(]forum_uid/,'null'/`
    fi
else
    COLS_TO_PULL=`echo $COL_NAMES | sed s/forum_uid/EdxPrivate.idInt2Forum\(forum_uid\)/`
fi

COLS=`echo $COLS_TO_PULL | sed s=body=replace\(body,'\n','\\\\n'\)=g`
echo $COLS

# Enclosing delimiter should be '"', no slash needed
EXPORT_FORUM_CMD=" \
 USE "$FORUM_DB"; \
 SELECT "$COLS" \
 INTO OUTFILE '"$FORUM_VALUES_FNAME"' \
  FIELDS TERMINATED BY ',' \
  LINES TERMINATED BY '\r\n' \
 FROM contents \
 WHERE course_display_name LIKE '"$COURSE_SUBSTR"';"

#********************
# echo "EXPORT_FORUM_CMD: $EXPORT_FORUM_CMD"
# echo "FORUM_FNAME: $FORUM_FNAME"
# exit 0
#********************

# ----------------------------- Execute the Main MySQL Command -------------

#********************
# echo "MYSQL_AUTH: $MYSQL_AUTH"
# echo "EXPORT_FORUM_CMD: $EXPORT_FORUM_CMD"
# exit 0
#********************

echo "Creating Forum extract ...<br>"

# Make pipe fail with error code saved: If pipefail is enabled,
# the pipeline's return status is the value of the last (rightmost)
# command to exit with a non-zero status, or zero if all commands
# exit successfully:
set -o pipefail
# Exit on error:
set -e
echo -E "$EXPORT_FORUM_CMD" | mysql $MYSQL_AUTH

# Concatenate the col name header and the table,
#
#******cat $FORUM_HEADER_FILE $FORUM_VALUES_FNAME > $FORUM_FNAME
cat $FORUM_HEADER_FILE  > $FORUM_FNAME
echo -e "\r" >> $FORUM_FNAME
cat $FORUM_VALUES_FNAME >> $FORUM_FNAME

#**************
# cat $FORUM_FNAME | sed 's/"\r/"|/g' | head -5
# echo "FORUM_FNAME: $FORUM_FNAME"
# exit 0
#**************

echo "Done exporting Forum for class $COURSE_SUBSTR to CSV<br>"

# ---------------- Write File Size and Five Sample Lines to $INFO_DEST -------------

# Write path to the encrypted zip file to
# path the caller provided:
if [ ! -z $INFO_DEST ]
then
	echo $ZIP_FNAME > $INFO_DEST
	echo "Appending number of lines to $INFO_DEST<br>"
	wc -l $FORUM_FNAME | sed -n "s/\([0-9]*\).*/\1/p" >> $INFO_DEST

	# Separator between the above table info and the
	# start of the sample lines. That division could
	# be made based on knowing that forum only consists
	# of one table; but that is not true of other exports.
	# so the separator is required everywhere:
	echo 'herrgottzemenschnochamal!' >> $INFO_DEST

	echo "Appending sample lines to $INFO_DEST<br>"
	# To maximize usefulness of the five samples, remove
	# embedded newlines from the body; else the five lines
	# may be taken up by a single forum post. The mess below:
	#
	#    $FORUM_FNAME contains the entire result: header and forum.
	#    sed 's/"\r/"|/g': replace all newlines with the (arbitrary),
        #        temporary placeholder '|'.
	#    tr -d '[:cntrl:]': Remove all control chars, incl. all cr/lf
	#    sed 's/|/\n/g': replace all previously placed '|' with '\n'
	#    head -5: keep only the first five lines:
	#
	# The temp storage in $PREVIEW_TMP_FILE, with subsequent extraction
	# of the first five lines from there is needed because having
	# head -n5 >> $INFO_DEST the last element of the pipes failed. The
	# script quietly failed there. Without the >> $INFO_DEST it was OK.
	cat $FORUM_FNAME | sed 's/"\r/"|/g' | tr -d '[:cntrl:]' | sed 's/|/<br>\n/g' > $PREVIEW_TMP_FILE
	head -n5 $PREVIEW_TMP_FILE >> $INFO_DEST
fi

#*******************
# echo "INFO_DEST: $INFO_DEST"
# echo "FORUM_FNAME: $FORUM_FNAME"
# exit 0
#*******************

# ----------------------- Zip and Encrypt -------------

#***********
# echo "ENCRYPT_PWD: '"$ENCRYPT_PWD"'"
# echo "ZIP_FNAME: '"$ZIP_FNAME"'"
# echo "FORUM_FNAME: '"$FORUM_FNAME"'"
# echo "Zip command: zip --junk-paths --password "$ENCRYPT_PWD $ZIP_FNAME $FORUM_FNAME
# exit 0
#***********

echo "Encrypting Forum report to $ZIP_FNAME...<br>"
# The --junk-paths puts just the files into
# the zip, not all the directories on their
# path from root to leaf:
zip --junk-paths --password $ENCRYPT_PWD $ZIP_FNAME $FORUM_FNAME
rm $FORUM_FNAME
chmod 644 $ZIP_FNAME
exit 0
