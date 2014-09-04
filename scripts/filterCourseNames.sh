#!/bin/bash

# Takes a series of lines from stdin (e.g. a pipe), 
# filters out the lines that look like test course
# names, and outputs all other lines.
#
# This filter is intended to be one of only three places
# where course name filtering needs to be maintained.
# The others are the MySQL stored function isTrueCourseName()
# in json_to_relation/scripts/mysqlProcAndFuncBodies.sql,
# and the JavaScript version in 
# json_to_relation/scripts/moduleScriptUtils.js. 

while read ONE_LINE
do
    echo "${ONE_LINE}" | 
    awk -F'\t' '/^[^-0-9]/'   |               # exclude names starting w/ a digit
                                              # filter all the pieces of course names that signal badness:
    awk 'tolower($0) !~ /jbau|janeu|sefu|davidu|caitlynx|josephtest|nickdupuniversity|nathanielu|gracelyou/' |
    awk 'tolower($0) !~ /sandbox|demo|sampleuniversity|joeu|grbuniversity/' |
    awk 'tolower($0) !~ /stanford_spcs\/001\/spcs_test_course1|testing_settings\/for_non_display/' |
    awk 'tolower($0) !~ /openedx\/testeduc2000c\/2013_sept|grb\/101\/grb_test_course|testing\/testing123\/evergreen/' |
    awk 'tolower($0) !~ /online\/bulldog\/summer2014,.bulldog test.|monx\/livetest\/2014/' |
    awk 'tolower($0) !~ /monx\//' |
    awk 'tolower($0) !~ /openedx\/testeduc2000c\/2013_sept|.*zzz.*|\/test\//' |
    awk 'tolower($0) !~ /stanford\/exp1\/experimental_assessment_test|stanford\/shib_only\/on_campus_stanford_only_test_class/' |
    awk 'tolower($0) !~ /business\/123\/gsb-test|worldview\/wvtest\/worldview_testing/' |
    awk 'tolower($0) !~ /foundation\/wtc01\/wadhwani_test_course|gsb\/af1\/alfresco_testing/' |
    awk 'tolower($0) !~ /tocc\/1\/eqptest|internal\/101\/private_testing_course/' |
    awk 'tolower($0) !~ /testtest|nickdup|stanford\/xxxx\/yyyy/' |
    awk -F'\t' '{ print }'
done;


