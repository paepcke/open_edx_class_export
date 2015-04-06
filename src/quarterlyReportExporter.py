'''
Created on Sep 1, 2014

@author: paepcke
'''

import argparse
import getpass
import os
import subprocess
import tempfile
import sys
import multiprocessing
import functools

from engagement import EngagementComputer
from pymysql_utils.pymysql_utils import MySQLDB


#*****def computeEngagementMulticore(quarterlyReportExporterObj, courseName):
def computeEngagementMulticore(dbHost, mySQLUser, mySQLPwd, courseName):
    '''
    Computes engagement for one course.
    This function is used in a pool.map() statement to 
    launch engagement computations across multiple cores.
    In Python 2.7 only a function will work. Instance methods
    don't. We get around this by taking a QuarterlyReportExporter
    obj and a course name. In the engagement() method we
    curry this function to take the QuarterlyReportExporter as
    a constant. This is necessary, b/c at least in Python 2.7,
    map functions can only take one arg, as I understand it. 
    
    :param quarterlyReportExporterObj: an QuarterlyReportExporter object that will be called
              on to do the actual work.
    :type quarterlyReportExporterObj: QuarterlyReportExporter
    :param courseName: name of course whose engagement is to be computed
    :type courseName: str
    :return one file name that contains the summary engagement of just
            the given course (i.e. a one-row csv file with column header).
    :rtype str
    '''
  
    comp = EngagementComputer(dbHost=dbHost, 
                              mySQLUser=mySQLUser, 
                              mySQLPwd=mySQLPwd, 
                              courseToProfile=courseName)
 
    comp.run()
    (summaryFile, detailFile, weeklyEffortFile) = comp.writeResultsToDisk() #@UnusedVariable
    return summaryFile
    
class QuarterlyReportExporter(object):
    '''
    classdocs
    '''
    
    # We use 2/3 of available cores to compute
    # engagement for courses simultaneously (rounded up):

    NUM_OF_CORES_TO_USE = int(round(0.5 + 2*multiprocessing.cpu_count()/3))
    
    FALL_START='-09-01'
    FALL_END='-11-30'
    WINTER_START='-12-01'
    WINTER_END='-02-28'
    SPRING_START='-03-01'
    SPRING_END='-05-31'
    SUMMER_START='-06-01'
    SUMMER_END='-08-31'

    def __init__(self,
                 dbHost='localhost', 
                 mySQLUser=None, 
                 mySQLPwd=None,
                 testing=False,
                 parent=None
                 ):
        '''
        Constructor. 
        
        :param dbHost: host where MySQL server runs
        :type dbHost: string
        :param mySQLUser: user under whom to log into MySQL
        :type mySQLUser: string
        :param mySQLPwd: MySQL password to use
        :type mySQLPwd: string
        :param testing: if True, caller is a unittest
        :type testing: boolean
        :param parent: a calling object. Used when calling from exportClass. 
                       The quantitiy is then needed to know where to write error
                       or info output.
        :type parent: object
        '''
        self.dbHost = dbHost
        self.mySQLUser = mySQLUser
        self.mySQLPwd = mySQLPwd
        self.parent   = parent
        self.testing  = testing
        if testing:
            self.currUser = 'unittest'
        else:
            self.currUser = getpass.getuser()
        self.defaultDb = 'Edx'
        self.mysqlDb = None
        self.enrollmentCache = {}
        self.thisScriptDir = os.path.dirname(__file__)        
        self.courseInfoScript = os.path.join(self.thisScriptDir, '../scripts/createQuarterlyReport.sh')
        self.ensureOpenMySQLDb()
        

    def enrollment(self, academicYear, quarter, minEnrollment=None, byActivity=False, outFile=None, printResultFilePath=True):
        '''
        Call Bash script createQuarterlyReport.sh, getting enrollment, cerfification, and internal/external
        course information. 
        :param academicYear: academic year for which information is wanted. MySQL wildcard ('%')
                is acceptable
        :type academicYear: {string | int}
        :param quarter: the academic quarter within the given year(s) for 
                which output is wanted ('fall', 'winter', 'spring', 'summer', or '%')
        :type quarter: string
        :param minEnrollment: by default, script createQuarterlyReport.sh requires a 
                minimum enrollment for a course to be included in the output. Use
                this keyword arg to specify another minimum, including 0.
        :type minEnrollment: int
        :param byActivity: normally the createQuarterlyReport.sh script uses the CourseInfo
                table to find course names to include. But often courses get learner activity
                after the official end of the course. If True, this parameter instead has the
                script include all courses that had at least one action during the academicYear/quarter
                for which info is requested.
        :type byActivity: Boolean
        :param outFile: file name to which to direct output. If none provided, a temp file is created.
        :type outFile: {None | String}
        :param printResultFilePath: if True, method will write to stdout a sentence saying where the
                output was placed.
        :type printResultFilePath: Boolean
        '''

        if outFile is None:
        # Use NamedTemporaryFile to create a temp name. 
        # We need to remove the file, else MySQL further down
        # will vomit that its out file already exists. This
        # deletion does intoduce a race condition with other
        # processes that use NamedTemporaryFile:
            outFile = tempfile.NamedTemporaryFile(suffix='quarterRep_%sQ%s_enrollment.csv' % (academicYear, quarter), delete=True)
            resFileName = outFile.name
            outFile.close()
        else:
            resFileName = outFile

        if not (isinstance(resFileName, str) or isinstance(resFileName, unicode)):
            raise ValueError("Value for outFile, if given, must be a string; was %s; type: %s" % (str(resFileName), str(type(resFileName))))
                    
        # The --silent suppresses a column header line
        # from being displayed ('course_display_name' and 'enrollment').
        # don't provide all the statistics, like awarded certificates.
        shellCmd = [self.courseInfoScript,'-u',self.currUser, '--silent', '-q', quarter, '-y', str(academicYear), '-o', resFileName]
        
        if minEnrollment is not None:
            try:
                # Ensure that the value is an int (or str of an int):
                int(minEnrollment)
                shellCmd.extend(['--minEnrollment', minEnrollment])
            except ValueError:
                raise ValueError('Value of minEnrollment must be int (or str of an int); was %s' % str(minEnrollment))
                
            
        if byActivity == True:
            shellCmd.extend(['--byActivity'])
        
        if self.mySQLPwd is not None and self.mySQLPwd != '':
            shellCmd.extend(['-w',self.mySQLPwd])
        try:
            subprocess.call(shellCmd)
        except Exception as e:
            raise ValueError('Error while searching for course names: %s' % `e`)
        
        if printResultFilePath:
            self.output('Enrollment numbers for %s%s are in %s' % (academicYear,quarter,resFileName))

        #*************
        # self.parent.writeResult('progress', "resFileName is: %s\n" % str(resFileName))
        #**********

        return resFileName
            
    def engagement(self, academicYear, quarter, byActivity=False, outFile=None, printResultFilePath=True):
        '''
                
        :param academicYear:
        :type academicYear:
        :param quarter:
        :type quarter:
        :param byActivity: normally the createQuarterlyReport.sh script uses the CourseInfo
                table to find course names to include. But often courses get learner activity
                after the official end of the course. If True, this parameter instead has the
                script include all courses that had at least one action during the academicYear/quarter
                for which info is requested.
        :type byActivity: Boolean
        :param outFile:
        :type outFile:
        :param printResultFilePath: whether or not to print msg to stdout about where 
            result file is to be found
        :type printResultFilePath: bool
        :return full path of file where results are stored
        :rtype string 
        '''
        
        if outFile is None:                                                                                                                                                                 
            outFile = tempfile.NamedTemporaryFile(suffix='quarterRep_%sQ%s_engagement.csv' % (academicYear, quarter), delete=False)                                                          
            resFileName = outFile.name                                                                                                                                                      
        else:                                                                                                                                                                               
            resFileName = outFile
            try:
                outFile = open(resFileName, 'w')
            except Exception as e:
                raise ValueError("Method engagement(): argument '%s' cannot be used as file name (%s)" % (resFileName, `e`))
        
        allCourseNames = self.getQuarterCourseNames(academicYear, quarter, byActivity)
        colHeaderGrabbed = False

        pool = multiprocessing.Pool(QuarterlyReportExporter.NUM_OF_CORES_TO_USE)
        partial_computeEngagementMulticore = functools.partial(computeEngagementMulticore, self.dbHost, self.mySQLUser, self.mySQLPwd)
        summaryFiles = pool.map(partial_computeEngagementMulticore, allCourseNames)
        for summaryFile in summaryFiles:
            # Pull the summary data from the engagement summary
            # file, grabbing the col header only from the first
            # file:
            with open(summaryFile, 'r') as fd:
                # discard or transfer the column header line
                # depending on whether this is the first engagement
                # summary file we process:
                colHeaderLine = fd.readline()
                if not colHeaderGrabbed:
                    try:
                        outFile.write(colHeaderLine)
                    except IOError:
                        self.output('No column header line in first summary file: %s' % summaryFile)
                        continue
                    colHeaderGrabbed = True
                try:
                    # Grab second line from summary file and 
                    # write to outfile:
                    outFile.write(fd.readline())
                except IOError:
                    self.output('No rows in %s' % summaryFile)
                    continue
        if printResultFilePath:
            self.output('Engagement summaries for %s%s are in %s' % (academicYear,quarter,resFileName))
        outFile.close()
        return resFileName

    def demographics(self,academicYear, quarter, byActivity=False, outFile=None, printResultFilePath=True):
        '''
        We create a CSV file with one row for each course that ran
        during the requested quarter. Columns are (with interspersed
        explanations:
        
               platform, [Always 'openedx']    
               course_display_name,
            Number of learners by each gender:
               gender_female,
               gender_male,
               gender_other,
               gender_withheld,
            Number of distinct 3-letter country codes:
               num_countries,
            Number of learners in each 'prior education' level:
               edu_Doctorate,
               edu_Masters_ProfessionalDegree,
               edu_Bachelors,
               edu_Associates,
               edu_Secondary_HighSchool,
               edu_Secondary_Junior,
               edu_Elementary_Primary,
               edu_None,
               edu_Other,
               edu_UserWithheld,
               edu_SignupBeforeLevelCollected,
            Number of learners in particular age groups:
               age_1-10
               age_11-20
               age_21-30
               age_31-40
               age_41-50
               age_51-60
               age_61-70
               age_71-80
               age_81-90
               age_unspecified
        
        :param academicYear:
        :type academicYear:
        :param quarter:
        :type quarter:
        :param byActivity: normally the createQuarterlyReport.sh script uses the CourseInfo
                table to find course names to include. But often courses get learner activity
                after the official end of the course. If True, this parameter instead has the
                script include all courses that had at least one action during the academicYear/quarter
                for which info is requested.
        :type byActivity: Boolean
        :param outFile:
        :type outFile:
        :param printResultFilePath: whether or not to print msg to stdout about where 
            result file is to be found
        :type printResultFilePath: bool
        :return full path of file where results are stored
        :rtype string 
        '''

        if outFile is None:                                                                                                                                                                 
            outFile = tempfile.NamedTemporaryFile(suffix='quarterRep_%sQ%s_engagement.csv' % (academicYear, quarter), delete=False)                                                          
            resFileName = outFile.name                                                                                                                                                      
        else:                                                                                                                                                                               
            resFileName = outFile
            try:
                outFile = open(resFileName, 'w')
            except Exception as e:
                raise ValueError("Method demographics(): argument '%s' cannot be used as file name (%s)" % (resFileName, `e`))

        allCourseNames = self.getQuarterCourseNames(academicYear, quarter, byActivity)
        
        # There is probably a way to do all steps in
        # a single, hairy SQL query. Instead, we take
        # each category in turn (gender, edu, age). For
        # each course we end up with an array of results,
        # which we'll put into a dict with the course name
        # as key. Be very careful about arrays being treated
        # as references, when you think you are making a
        # new one:
        courseResults = {}
        for courseName in allCourseNames:
            thisCourseRow = ['openedx',courseName]
            # Create a MySQL temp table with just the unique
            # anon_screen_name of this course:
            if self.testing:
                db = 'unittest'
                dbPrivate = 'unittest'
                dbEdxprod = 'unittest'
            else:
                db = self.defaultDb
                dbPrivate = 'EdxPrivate'
                dbEdxprod = 'edxprod'
            self.mysqlDb.dropTable('%s.CourseMembers' % db)
            self.mysqlDb.createTable('%s.CourseMembers' % db, {'anon_screen_name' : 'varchar(40)'}, temporary=True)
            self.mysqlDb.execute("INSERT INTO %s.CourseMembers (anon_screen_name) " % db +\
                                 "SELECT %s.UserGrade.anon_screen_name " % db +\
                                 "FROM %s.UserGrade, %s.true_courseenrollment " % (dbPrivate, dbEdxprod) +\
                                 "WHERE %s.UserGrade.user_int_id = %s.true_courseenrollment.user_id " % (dbPrivate, dbEdxprod) +\
                                 "AND %s.true_courseenrollment.course_display_name = '%s';" % (db, courseName))
            # Create another temp table to hold demographics in that course,
            # one row for each learner (i.e. for each anon_screen_name):
            self.mysqlDb.dropTable('%s.CourseDemographics' % db)
            self.mysqlDb.createTable('%s.CourseDemographics' % db, 
                                     {'gender' : 'varchar(6)',
                                      'year_of_birth' : 'int',
                                      'level_of_education' : 'varchar(42)',
                                      'three_letter_country' : 'varchar(3)'
                                      }, temporary=True)
            # Populate the temp table: each row is demographics for one learner
            # in the course we are working on. The final GROUP BY is 
            # needed b/c some learners have multiple entries in UserCountry,
            # if they came in from IPs of varying countries during separate
            # events. The GROUP BY will pick one of those countries, rather
            # than taking the cross product of the first join with UserCountry
            # entries for the same learner:  
            self.mysqlDb.execute ("INSERT INTO %s.CourseDemographics (gender, year_of_birth, level_of_education, three_letter_country) " % db +\
                                  "SELECT %s.Demographics.gender, " % db +\
                                  "%s.Demographics.year_of_birth, " % db +\
                                  "%s.Demographics.level_of_education, " % db +\
                                  "%s.UserCountry.three_letter_country " % db  +\
                                  "FROM (%s.CourseMembers LEFT JOIN %s.Demographics " % (db,db)  +\
                                  "  ON %s.CourseMembers.anon_screen_name = %s.Demographics.anon_screen_name) " % (db,db)  +\
                                  " LEFT JOIN %s.UserCountry " % db +\
                                  "  ON %s.CourseMembers.anon_screen_name = %s.UserCountry.anon_screen_name " % (db,db)  +\
                                  "GROUP BY %s.UserCountry.anon_screen_name;" % db)
        
            # Now we have rows like:
            #    'f','1997','Secondary/High School','USA'
            # Need to pick out the various counts now.
            # The nicest query would be something like:
            #
            #     SELECT Withhelds.withheld_count AS gender_withheld,
            #            Males.male_count AS gender_male,
            #            Females.female_count AS gender_female,
            #            Others.other_count AS gender_other
            #     FROM (SELECT
            #             COUNT(gender) AS male_count FROM CourseDemographics WHERE gender = 'm') AS Males,
            #          (SELECT
            #             COUNT(gender) AS female_count FROM CourseDemographics WHERE gender = 'f') AS Females,
            #          (SELECT
            #             COUNT(gender) AS other_count FROM CourseDemographics WHERE gender = 'o') AS Others,
            #          (SELECT
            #             COUNT(gender) AS withheld_count FROM CourseDemographics WHERE gender = '') AS Withhelds;            
            #     
            #                 oneCourseDemographic = ['openedx',courseName]
            #
            # Unfortunately, MySQL doesn't let you refer to a tmp table twice.
            # So we do it in pieces; first gender:
            queryIt = self.mysqlDb.query("SELECT COUNT(gender) FROM %s.CourseDemographics WHERE gender = 'f';" % db)
            thisCourseRow.append(queryIt.next()[0])
            queryIt = self.mysqlDb.query("SELECT COUNT(gender) FROM %s.CourseDemographics WHERE gender = 'm';" % db)
            thisCourseRow.append(queryIt.next()[0])
            queryIt = self.mysqlDb.query("SELECT COUNT(gender) FROM %s.CourseDemographics WHERE gender = 'o';" % db)
            thisCourseRow.append(queryIt.next()[0])
            queryIt = self.mysqlDb.query("SELECT COUNT(gender) FROM %s.CourseDemographics WHERE gender = '';" % db)
            thisCourseRow.append(queryIt.next()[0])
            # Country count:
            queryIt = self.mysqlDb.query("SELECT COUNT(DISTINCT three_letter_country) FROM %s.CourseDemographics;" % db)
            thisCourseRow.append(queryIt.next()[0])
            # Education levels: from the following query we'll get results as tuples
            # like this: ('Doctorate', 10)
            # We'll build a dict to collect them, using the col names of
            # the final result table as keys; any scheme would do.
            # I'm sure this could be done very elegantly, but I
            # bet you understand this version:
            queryIt = self.mysqlDb.query("SELECT level_of_education, COUNT(level_of_education) FROM %s.CourseDemographics GROUP BY level_of_education;" % db)
            eduDict = {}
            for resTuple in queryIt:
                if resTuple[0] == 'p':
                    eduDict['edu_Doctorate'] = resTuple[1]
                elif resTuple[0] == 'm':
                    eduDict['edu_Masters_ProfessionalDegree'] = resTuple[1]
                elif resTuple[0] == 'b':
                    eduDict['edu_Bachelors'] = resTuple[1]
                elif resTuple[0] == 'a':
                    eduDict['edu_Associates'] = resTuple[1]
                elif resTuple[0] == 'hs':
                    eduDict['edu_Secondary_HighSchool'] = resTuple[1]
                elif resTuple[0] == 'jhs':
                    eduDict['edu_Secondary_Junior'] = resTuple[1]
                elif resTuple[0] == 'el':
                    eduDict['edu_Primary_Elementary'] = resTuple[1]
                elif resTuple[0] == 'none':
                    eduDict['edu_None'] = resTuple[1]
                elif resTuple[0] == 'other':
                    eduDict['edu_Other'] = resTuple[1]
                elif resTuple[0] == '':
                    eduDict['edu_UserWithheld'] = resTuple[1]
                elif resTuple[0] == 'NULL':
                    eduDict['edu_SignupBeforeLevelCollected'] = resTuple[1]
                
            # Transfer to the res row in the proper order:
            thisCourseRow.append(eduDict.get('edu_Doctorate', 0))
            thisCourseRow.append(eduDict.get('edu_Masters_ProfessionalDegree', 0))
            thisCourseRow.append(eduDict.get('edu_Associates', 0))
            thisCourseRow.append(eduDict.get('edu_Secondary_HighSchool', 0))
            thisCourseRow.append(eduDict.get('edu_Secondary_Junior', 0))
            thisCourseRow.append(eduDict.get('edu_Primary_Elementary', 0))
            thisCourseRow.append(eduDict.get('edu_None', 0))
            thisCourseRow.append(eduDict.get('edu_Other', 0))
            thisCourseRow.append(eduDict.get('edu_UserWithheld', 0))
            thisCourseRow.append(eduDict.get('edu_SignupBeforeLevelCollected', 0))
            
            # Now the age ranges. This one is crufty SQL (for me):
            #   SELECT CONCAT(
            #                1 + FLOOR((YEAR(CURDATE()) - year_of_birth)/10) * 10,
            #         '-',
            #                10 + FLOOR((YEAR(CURDATE()) - year_of_birth)/10) * 10
            #         ) AS age_range,
            #        COUNT(*) AS numLearnersAged
            #   FROM CourseDemographics
            #   GROUP BY 1
            #   ORDER BY age_range;
            #                           
            # Yields something like:
            # +-----------+-----------------+
            # | age_range | numLearnersAged |
            # +-----------+-----------------+
            # | NULL      |            1425 |
            # | 1-10      |               6 |
            # | 101-110   |               3 |
            # | 11-20     |             357 |
            # | 121-130   |               2 |
            # | 21-30     |            4941 |
            # | 31-40     |            2595 |
            # | 41-50     |            1038 |
            # | 51-60     |             392 |
            # | 61-70     |             154 |
            # | 71-80     |              24 |
            # | 81-90     |               4 |
            # +-----------+-----------------+
            #
            # The GROUP BY 1 groups by age_range
             
            queryIt = self.mysqlDb.query("SELECT CONCAT( " +\
                                            "1 + FLOOR((YEAR(CURDATE()) - year_of_birth)/10) * 10, " +\
                                         "'-'," +\
                                            "10 + FLOOR((YEAR(CURDATE()) - year_of_birth)/10) * 10" +\
                                         "   ) AS age_range, " +\
                                         "COUNT(*) AS numLearnersAged " +\
                                         "FROM %s.CourseDemographics " % db +\
                                         "GROUP BY 1 " +\
                                         "ORDER BY age_range;"
                                         )
            # We build a dict again to make *sure* the values
            # will be added to the result row in order:
            ageDict = {}
            for ageRangeRow in queryIt:
                if ageRangeRow[0].startswith('NULL'):
                    ageDict['age_unspecified'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('1-'):
                    ageDict['age_1-10'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('11-'):
                    ageDict['age_11-20'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('21-'):
                    ageDict['age_21-30'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('31-'):
                    ageDict['age_31-40'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('41-'):
                    ageDict['age_41-50'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('51-'):
                    ageDict['age_51-60'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('61-'):
                    ageDict['age_61-70'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('71-'):
                    ageDict['age_71-80'] = ageRangeRow[1]
                elif ageRangeRow[0].startswith('81-'):
                    ageDict['age_81-90'] = ageRangeRow[1]
            thisCourseRow.append(ageDict.get('age_1-10', 0))
            thisCourseRow.append(ageDict.get('age_11-20', 0))
            thisCourseRow.append(ageDict.get('age_21-30', 0))
            thisCourseRow.append(ageDict.get('age_31-40', 0))
            thisCourseRow.append(ageDict.get('age_41-50', 0))
            thisCourseRow.append(ageDict.get('age_51-60', 0))
            thisCourseRow.append(ageDict.get('age_61-70', 0))
            thisCourseRow.append(ageDict.get('age_71-80', 0))
            thisCourseRow.append(ageDict.get('age_81-90', 0))
            thisCourseRow.append(ageDict.get('age_unspecified', 0))
            
            courseResults[courseName] = thisCourseRow
            # loop back for next course
        
        # Now populate the result table; order of course rows 
        # doesn't matter; first the col headers:
        outFile.write(','.join([
                                "platform",
                                "course_display_name",
                                "gender_female",
                                "gender_male",
                                "gender_other",
                                "gender_withheld",
                                "num_countries",
                                "edu_Doctorate",
                                "edu_Masters_ProfessionalDegree",
                                "edu_Bachelors",
                                "edu_Associates",
                                "edu_Secondary_HighSchool",
                                "edu_Secondary_Junior",
                                "edu_Elementary_Primary",
                                "edu_None",
                                "edu_Other",
                                "edu_UserWithheld",
                                "edu_SignupBeforeLevelCollected",
                                "age_1-10",
                                "age_11-20",
                                "age_21-30",
                                "age_31-40",
                                "age_41-50",
                                "age_51-60",
                                "age_61-70",
                                "age_71-80",
                                "age_81-90",
                                "age_unspecified"
                                ]) + '\n')
        
        for row in courseResults.values():
            # For the join() all cols need to be
            # strings; so use a list comprehension
            # for the conversion:
            outFile.write(','.join([str(colValue) for colValue in row]) + '\n')
        
        outFile.close()
        
        return resFileName


    def getQuarterCourseNames(self, academicYear, quarter,  byActivity):
        '''
        Returns an array of course names that ran during the given
        academic year and quarter.
        
        :param academicYear: academic year in which desired quarter occurred
        :type academicYear: int
        :param quarter: quarter: one of fall,winter,spring,summer
        :type quarter: string
        :param byActivity: if true, all courses that showed any activity during
            the quarter in question will be included. Else only
            the courses that were scheduled to run during the quarter
            are included.
        :type byActivity: boolean
        '''
        if byActivity:
            if quarter == '%':
                # All quarters of given year:
                timeConstraint = "(time BETWEEN '%s' AND '%s' OR " % self.getQuarterCalendarStartEndDates('fall', academicYear) +\
                                 " time BETWEEN '%s' AND '%s' OR " % self.getQuarterCalendarStartEndDates('winter', academicYear) +\
                                 " time BETWEEN '%s' AND '%s' OR " % self.getQuarterCalendarStartEndDates('spring', academicYear) +\
                                 " time BETWEEN '%s' AND '%s')"  % self.getQuarterCalendarStartEndDates('summer', academicYear)
                               
            else:
                (quarterStartDate, quarterEndDate) = self.getQuarterCalendarStartEndDates(quarter, academicYear) 
                timeConstraint = "time BETWEEN '%s' AND '%s' " % (quarterStartDate, quarterEndDate)
             
            courseNameIt = self.mysqlDb.query("SELECT course_display_name, quarter, academic_year " +\
                                              "FROM CourseInfo " +\
                                              "WHERE EXISTS(SELECT 1 " +\
                                              "               FROM EventXtract " +\
                                                "              WHERE EventXtract.course_display_name = CourseInfo.course_display_name " +\
                                                 "                AND %s);" % timeConstraint
                                         % (str(academicYear), quarter))
        else:
            if self.testing:
                db = 'unittest'
            else:
                db = 'Edx'
            courseNameIt = self.mysqlDb.query("SELECT course_display_name " +\
                                             "FROM " + db + ".CourseInfo " +\
                                             "WHERE academic_year LIKE '%s' AND quarter LIKE '%s';"\
                                             % (str(academicYear), quarter))

        allCourseNames = [name[0] for name in courseNameIt]
        return allCourseNames


    def getQuarterCalendarStartEndDates(self, quarter, academic_year):
        '''
        Returns 2-tuple: start and end calendar date of given quarter
        in given academic year. Example: 'fall','2014' would return
        ("2014-09-01", "2014-11-30"). Whereas 'winter, '2014' would
        return ("2014-12-01", "2015-02-28")
        
        :param quarter: the academic quarter to date; case insensitive
        :type quarter: string
        :param academic_year: the academic year in which the quarter occurred.
        :type academic_year: {int|str}
        '''
        try:
            academic_year = int(academic_year)
        except ValueError:
            raise ValueError("getQuarterCalendarStartEndDates: Academic year must be a string or an int.")
        if not isinstance(quarter, str):
            raise ValueError("getQuarterCalendarStartEndDates: Quarter must be a string; was '%s'" % str(quarter))
        quarter  = quarter.lower()
        calYear  = academic_year + 1 
        if quarter == 'fall':
            return(str(academic_year) + QuarterlyReportExporter.FALL_START,
                   str(academic_year) + QuarterlyReportExporter.FALL_END)
        if quarter == 'winter':
            return(str(calYear) + QuarterlyReportExporter.WINTER_START,
                   str(calYear) + QuarterlyReportExporter.WINTER_END)
        if quarter == 'spring':
            return(str(calYear) + QuarterlyReportExporter.SPRING_START,
                   str(calYear) + QuarterlyReportExporter.SPRING_END)
        if quarter == 'summer':
            return(str(calYear) + QuarterlyReportExporter.SUMMER_START,
                   str(calYear) + QuarterlyReportExporter.SUMMER_END)
        raise ValueError("getQuarterCalendarStartEndDates: Illegal value for academic quarter: '%s'; legal values: fall,winter,spring,summer." % str(quarter))
            
    
    def ensureOpenMySQLDb(self):

        if self.mysqlDb is not None:
            return self.mysqlDb
        try:
            if self.mySQLPwd is None:
                self.output('Trying to access MySQL using pwd file...')
                with open('/home/%s/.ssh/mysql' % self.currUser, 'r') as fd:
                    self.mySQLPwd = fd.readline().strip()
                    self.mysqlDb = MySQLDB(user=self.mySQLUser, passwd=self.mySQLPwd, db=self.defaultDb)
                    self.output('Access to MySQL OK...')
            else:
                self.output('Trying to access MySQL using given pwd...')
                self.mysqlDb = MySQLDB(user=self.mySQLUser, passwd=self.mySQLPwd, db=self.defaultDb)
                self.output('Access to MySQL OK...')
        except Exception as e:
            try:
                # Try w/o a pwd:
                self.mySQLPwd = None
                self.output('Trying to access MySQL without a pwd...')
                self.mysqlDb = MySQLDB(user=self.currUser, db=self.defaultDb)
                self.output('Access to MySQL OK...')
            except Exception as e:
                # Remember the error msg for later:
                self.dbError = `e`;
                self.mysqlDb = None
                self.output('Failed to access MySQL.')
        return self.mysqlDb

    def getEnrollment(self, courseDisplayName):
        '''
        Given a MySQL regexp courseNameWildcard string, return a list
        of matchine course_display_name in the db. If self.mysql
        is None, indicating that the __init__() method was unable
        to log into the db, then return None.

        :param courseDisplayName: Course name whose enrollment to find.
        :type courseDisplayName: String

        :return: An array of matching course_display_name, which may
                 be empty. None if _init__() was unable to log into db.
                 If includeEnrollment is True, append enrollment to each course name.
        :rtype: {[String] | None}
        '''
        
        # Was asked for this exact name before?
        try:
            return self.enrollmentCache[courseDisplayName]
        except KeyError:
            pass
        
        
        # The --silent suppresses a column header line
        # from being displayed ('course_display_name' and 'enrollment'):
        mySqlCmd = [self.courseInfoScript,'-u',self.currUser,'--silent']
        if self.mySQLPwd is not None and self.mySQLPwd != '':
            mySqlCmd.extend(['-w',self.mySQLPwd])
        mySqlCmd.extend([courseDisplayName])
        
        try:
            pipeFromMySQL = subprocess.Popen(mySqlCmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
        except Exception as e:
            self.writeError('Error while searching for course names: %s' % `e`)
            return None
        enrollment = None
        for courseNameAndEnrollment in pipeFromMySQL:
            (courseName,enrollment) = courseNameAndEnrollment.strip().split('\t')
            if len(courseName) > 0:
                self.enrollmentCache[courseName] = int(enrollment)
        return enrollment
      
    
    def output(self, txt):
        print(txt)
        
if __name__ == '__main__':
    
    # -------------- Manage Input Parameters ---------------
    
    usage = 'Usage: quarterlyReportExporter.py [-u <MySQL user>] [-p<MySQL pwd>] [-w <MySQL pwd>] quarter <academic_year-as-YYYY>\n'

    parser = argparse.ArgumentParser(prog=os.path.basename(sys.argv[0]), formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('-u', '--user',
                        action='store',
                        help='User ID that is to log into MySQL. Default: the user who is invoking this script.')
    parser.add_argument('-p', '--pwd',
                        action='store_true',
                        help='Request to be asked for pwd for operating MySQL;\n' +\
                             '    default: content of scriptInvokingUser$Home/.ssh/mysql if --user is unspecified,\n' +\
                             '    or, if specified user is root, then the content of scriptInvokingUser$Home/.ssh/mysql_root.'
                        )
    parser.add_argument('-w', '--password',
                        action='store',
                        help='User explicitly provided password to log into MySQL.\n' +\
                             '    default: content of scriptInvokingUser$Home/.ssh/mysql if --user is unspecified,\n' +\
                             '    or, if specified user is root, then the content of scriptInvokingUser$Home/.ssh/mysql_root.'
                        )
    parser.add_argument('-e', '--enrollment',
                        action='store_true',
                        help='Request enrollment numbers; if neither --enrollment nor --engagement are requested,\n' +\
                             '    then both are provided.'
                        )
    parser.add_argument('-g', '--engagement',
                        action='store_true',
                        help='Request engagement numbers; if neither --enrollment nor --engagement are requested,\n' +\
                             '    then both are provided.'
                        )
    parser.add_argument('quarter',
                        action='store',
                        help="One of fall, winter, spring, or summer. Or 'all' if all quarter are of interest."
                        ) 
    
    parser.add_argument('academicYear',
                        action='store',
                        help="The *academic* year of the course (not the calendar year). Or 'all' if all years are of interest."
                        ) 
    
    args = parser.parse_args();
    
    if args.quarter not in ['fall', 'winter', 'spring', 'summer', 'all']:
        raise ValueError('Quarter must be fall, winter, spring, summer, or all; was %s' % args.quarter)
    
    # Replace 'all' by MySQL wildcard: 
    if args.quarter == 'all':
        args.quarter = '%'
    
    if args.academicYear == 'all':
        args.academicYear = '%' 
    else:
        # If not 'all', then year must be an int:
        try:
            academicYearIntOrPercentSign = int(args.academicYear)
        except ValueError:
            raise ValueError("The academic_year parameter must be 'all', or an integer year > 2011; was %s" % args.academicYear)
        if academicYearIntOrPercentSign < 2012:
            raise ValueError('Data only available for academic year 2012 onwards.')
    
    if args.user is None:
        user = getpass.getuser()
    else:
        user = args.user
        
    if args.password and args.pwd:
        raise ValueError('Use either -p, or -w, but not both.')
        
    if args.pwd:
        pwd = getpass.getpass("Enter %s's MySQL password on localhost: " % user)
    elif args.password:
        pwd = args.password
    else:
        # Try to find pwd in specified user's $HOME/.ssh/mysql
        currUserHomeDir = os.getenv('HOME')
        if currUserHomeDir is None:
            pwd = None
        else:
            # Don't really want the *current* user's homedir,
            # but the one specified in the -u cli arg:
            userHomeDir = os.path.join(os.path.dirname(currUserHomeDir), user)
            try:
                if user == 'root':
                    with open(os.path.join(currUserHomeDir, '.ssh/mysql_root')) as fd:
                        pwd = fd.readline().strip()
                else:
                    with open(os.path.join(userHomeDir, '.ssh/mysql')) as fd:
                        pwd = fd.readline().strip()
            except IOError:
                # No .ssh subdir of user's home, or no mysql inside .ssh:
                pwd = ''

    # If neither enrollment nor engagement were specifically
    # requested, then both are supplied:
    if not (args.engagement or args.enrollment):
        args.enrollment = True
        args.engagement = True
    
    myReporter = QuarterlyReportExporter(mySQLUser=user, mySQLPwd=pwd)
    if args.engagement:
        myReporter.engagement(academicYearIntOrPercentSign, args.quarter)
    if args.enrollment:
        myReporter.output('-------------------------------------')
        myReporter.enrollment(academicYearIntOrPercentSign, args.quarter)
