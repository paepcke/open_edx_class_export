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
        

    def enrollment(self, academicYear, quarter):
        self.courseNameDict = OrderedDict()
        mysqlCmd = "SELECT course_display_name, is_internal " +\
                   "FROM CourseInfo " +\
                   "WHERE academic_year = %d AND quarter = '%s'" \
                    % (academicYear, quarter)
        for (courseName, isInternal) in self.mysqlDb.query(mysqlCmd):
            self.courseNameDict[courseName] = isInternal
        
        self.output('course,is_internal,enrollment')
        for courseName in self.courseNameDict.keys():
            enrollment = self.getEnrollment(courseName)
            if enrollment is None:
                enrollment = 'n/a'
            self.output('%s,%s,%s' % (courseName, self.courseNameDict[courseName], enrollment))
            

    
    def ensureOpenMySQLDb(self):

        if self.mysqlDb is not None:
            return self.mysqlDb
        try:
            if self.mySQLPwd is None:
                with open('/home/%s/.ssh/mysql' % self.currUser, 'r') as fd:
                    self.mySQLPwd = fd.readline().strip()
                    self.mysqlDb = MySQLDB(user=self.mySQLUser, passwd=self.mySQLPwd, db=self.defaultDb)
            else:
                    self.mysqlDb = MySQLDB(user=self.mySQLUser, passwd=self.mySQLPwd, db=self.defaultDb)
        except Exception:
            try:
                # Try w/o a pwd:
                self.mySQLPwd = None
                self.mysqlDb = MySQLDB(user=self.currUser, db=self.defaultDb)
            except Exception as e:
                # Remember the error msg for later:
                self.dbError = `e`;
                self.mysqlDb = None
        return self.mysqlDb

    def getEnrollment(self, courseNameRegex):
        '''
        Given a MySQL regexp courseNameWildcard string, return a list
        of matchine course_display_name in the db. If self.mysql
        is None, indicating that the __init__() method was unable
        to log into the db, then return None.

        :param courseNameRegex: Course name regular expression in MySQL syntax.
        :type courseNameRegex: String
        **********
        :return: An array of matching course_display_name, which may
                 be empty. None if _init__() was unable to log into db.
                 If includeEnrollment is True, append enrollment to each course name.
        :rtype: {[String] | None}
        '''
        
        # Was asked for this exact name before?
        try:
            return self.enrollmentCache[courseNameRegex]
        except KeyError:
            pass
        
        
        # The --silent suppresses a column header line
        # from being displayed ('course_display_name' and 'enrollment'):
        mySqlCmd = [self.searchCourseNameScript,'-u',self.currUser,'--silent']
        if self.mySQLPwd is not None:
            mySqlCmd.extend(['-w',self.mySQLPwd])
        mySqlCmd.extend([courseNameRegex])
        
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
    parser.add_argument('acacemicYear',
                        action='store',
                        type=int,
                        help='The *acdemic* year of the course (not the calendar year).'
                        ) 
    
    # Optionally: any number of years as ints:
    parser.add_argument('quarter',
                        action='store',
                        help='One of fall, winter, spring, or summer.'
                        ) 
    
    args = parser.parse_args();
    
    if args.quarter not in ['fall', 'winter', 'spring', 'summer']:
        raise ValueError('Quarter must be fall, winter, spring, or summer.')
    
    if args.acacemicYear < 2012:
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

    myReporter = QuarterlyReportExporter()
    myReporter.enrollment(args.acacemicYear, args.quarter)

