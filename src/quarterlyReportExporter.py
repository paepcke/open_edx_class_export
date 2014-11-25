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
                 testing=False 
                 ):
        '''
        Constructor
        '''
        self.dbHost = dbHost
        self.mySQLUser = mySQLUser
        self.mySQLPwd = mySQLPwd

        if testing:
            self.currUser = 'unittest'
        else:
            self.currUser = getpass.getuser()
        self.defaultDb = 'Edx'
        self.mysqlDb = None
        self.enrollmentCache = {}
        self.thisScriptDir = os.path.dirname(__file__)        
        self.searchCourseNameScript = os.path.join(self.thisScriptDir, '../scripts/searchCourseDisplayNames.sh')
        self.ensureOpenMySQLDb()
        

    def enrollment(self, academicYear, quarter, outFile=None):

        if outFile is None:
            outFile = tempfile.NamedTemporaryFile(suffix='quarterRep_%sQ%s_enrollment.csv' % (academicYear, quarter), delete=False)

        if type(outFile) == str:
            outFile = open(outFile, 'r')
            
        # The --silent suppresses a column header line
        # from being displayed ('course_display_name' and 'enrollment'):
        mySqlCmd = [self.searchCourseNameScript,'-u',self.currUser, '-q', quarter, '-y', str(academicYear), '>', outFile.name]
        if self.mySQLPwd is not None and self.mySQLPwd != '':
            mySqlCmd.extend(['-w',self.mySQLPwd])
        
        try:
            subprocess.call(mySqlCmd)
        except Exception as e:
            self.output('Error while searching for course names: %s' % `e`)
            return None
        self.output('Enrollment numbers for %s%s are in %s' % (academicYear,quarter,outFile.name))
        outFile.close()
            
    def engagement(self, academicYear, quarter, outFile=None):
        
        if outFile is None:
            outFile = tempfile.NamedTemporaryFile(suffix='quarterRep_%sQ%s_engagement_summaries.csv' % (academicYear, quarter), delete=False)

        if type(outFile) == str:
            outFile = open(outFile, 'r')

        colHeaderGrabbed = False
        for courseName in self.mysqlDb.query("SELECT course_display_name " +\
                                             "FROM Edx.CourseInfo " +\
                                             "WHERE academic_year = %d AND quarter = '%s';"\
                                             % (academicYear, quarter)):
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
        self.output('Engagement summaries for %s%s are in %s' % (academicYear,quarter,outFile.name))
        outFile.close()

    
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
        mySqlCmd = [self.searchCourseNameScript,'-u',self.currUser,'--silent']
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
                        help='Request enrollment numbers; if neither --enrollment nor -engagement are requested,\n' +\
                             '    then both are provided.'
                        )
    parser.add_argument('-g', '--engagement',
                        action='store_true',
                        help='Request engagement numbers; if neither --enrollment nor --engagement are requested,\n' +\
                             '    then both are provided.'
                        )
    parser.add_argument('academicYear',
                        action='store',
                        type=int,
                        help='The *academic* year of the course (not the calendar year).'
                        ) 
    
    parser.add_argument('quarter',
                        action='store',
                        help='One of fall, winter, spring, or summer.'
                        ) 
    
    args = parser.parse_args();
    
    if args.quarter not in ['fall', 'winter', 'spring', 'summer', '%']:
        raise ValueError('Quarter must be fall, winter, spring, or summer.')
    
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

