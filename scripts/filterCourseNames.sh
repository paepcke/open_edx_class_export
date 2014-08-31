#!/bin/bash

# Takes a series of lines from stdin (e.g. a pipe), 
# filters out the lines that look like test course
# names, and outputs all other lines.
#
# This filter is intended to be one of only two places
# where course name filtering needs to be maintained.
# The other is the MySQL stored function isTrueCourseName()
# in json_to_relation/scripts/mysqlProcAndFuncBodies.sql

while read ONE_LINE
do
    echo "${ONE_LINE}" | 
    awk -F'\t' '/^[^-0-9]/'   |               # exclude names starting w/ a digit
                                              # filter all the pieces of course names that signal badness:
    awk 'tolower($0) !~ /jbau|janeu|sefu|davidu|caitlynx|josephtest|nickdupuniversity|nathanielu|gracelyou|sandbox|demo|sampleuniversity|.*zzz.*|\/test\/|joeU/' |
    awk -F'\t' '{ print }'
done;


