'''
Created on Sep 1, 2014

@author: paepcke
'''

import argparse
from collections import OrderedDict
import getpass
import os
import subprocess
import sys
import tempfile

from engagement import EngagementComputer
from pymysql_utils.pymysql_utils import MySQLDB


class QuarterlyReportExporter(object):
    '''
    classdocs
    '''


    def __init__(self,
                 dbHost='localhost', 
                 mySQLUser=None, 
                 mySQLPwd=None,
                 testing=False,
		 parent=None
                 ):
        '''
        Constructor
        '''
        self.dbHost = dbHost
        self.mySQLUser = mySQLUser
        self.mySQLPwd = mySQLPwd
	self.parent   = parent

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
        

    def enrollment(self, academicYear, quarter, minEnrollment=None, byActivity=None,outFile=None, printResultFilePath=True):
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
                
            
        if byActivity is not None and byActivity == True:
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
            
    def engagement(self, academicYear, quarter, outFile=None, printResultFilePath=True):
        '''
                
        :param academicYear:
        :type academicYear:
        :param quarter:
        :type quarter:
        :param outFile:
        :type outFile:
        :param printResultFilePath: whether or not to print msg to stdout about where 
            result file is to be found
        :type printResultFilePath: bool
        :return full path of file where results are stored
        :rtype string 
        '''
        
        if outFile is None:
            outFile = tempfile.NamedTemporaryFile(suffix='quarterRep_%sQ%s_engagement_summaries.csv' % (academicYear, quarter), delete=False)

        if isinstance(resFileName, str) or isinstance(resFileName, unicode):
            outFile = open(outFile, 'r')

        colHeaderGrabbed = False
        for courseName in self.mysqlDb.query("SELECT course_display_name " +\
                                             "FROM Edx.CourseInfo " +\
                                             "WHERE academic_year LIKE '%s' AND quarter LIKE '%s';"\
                                             % (str(academicYear), quarter)):
            # Query results come in tuples, like ('myUniversity/CS101/me',). Grab
            # the name itself:
            courseName = courseName[0]
            comp = EngagementComputer(dbHost=self.dbHost, mySQLUser=self.mySQLUser, mySQLPwd=self.mySQLPwd, courseToProfile=courseName)
            comp.run()
            (summaryFile, detailFile, weeklyEffortFile) = comp.writeResultsToDisk() #@UnusedVariable
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
        resFileName = outFile.name;
        if printResultFilePath:
            self.output('Engagement summaries for %s%s are in %s' % (academicYear,quarter,resFileName))
        outFile.close()
        return resFileName

    
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
    
    usage = 'Usage: quarterlyReportExporter.py [-u <MySQL user>] [-p<MySQL pwd>] [-w <MySQL pwd>] <academic_year-as-YYYY> quarter\n'

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
        raise ValueError('Quarter must be fall, winter, spring, summer, or all.')
    
    # Replace 'all' by MySQL wildcard: 
    if args.quarter == 'all':
        args.quarter = '%'
    
    if args.academicYear == 'all':
        args.academicYear = '%' 
    else:
        # If not 'all', then year must be an int:
        if not isinstance(args.academicYear, int):
            raise ValueError("The academic_year parameter must be 'all', or an integer year > 2011.")
        if args.academicYear < 2012:
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
        args.engagement = True
        args.engagement = True
    
    myReporter = QuarterlyReportExporter(mySQLUser=user, mySQLPwd=pwd)
    if args.engagement:
        myReporter.engagement(args.academicYear, args.quarter)
    if args.enrollment:
        myReporter.output('-------------------------------------')
        myReporter.enrollment(args.academicYear, args.quarter)

