#!/usr/bin/env python
'''
Created on Jan 14, 2014

@author: paepcke

Web service partner to exportClass.html. Uses Tornado to listen to
port 8080 under SSL protocol. Accepts requests for exporting
the data of one class. Can export just anonymized part of the
class info, or more, i.e. data that includes potentially 
personally identifiable information (PII). If PII is involved,
data is encrypted into a .zip file. Either way the data is
deposited in /home/dataman/Data/CustomExcerpts/CourseSubdir/<tables>.csv.
'''

from collections import OrderedDict
import datetime
import getpass
import json
import os
import re
import shutil
import socket
import string
from subprocess import CalledProcessError
import subprocess
import sys
import tempfile
from threading import Timer
import time # @UnusedImport
import zipfile

from engagement import EngagementComputer

from pymysql_utils.pymysql_utils import MySQLDB


# Add json_to_relation source dir to $PATH
# for duration of this execution:
source_dir = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")]
source_dir.extend(sys.path)
sys.path = source_dir


import tornado;
from tornado.ioloop import IOLoop;
from tornado.websocket import WebSocketHandler;
from tornado.httpserver import HTTPServer;

# Enum for return whether a directory
# exists or not:
class PreExisted:
    DID_NOT_EXIST = 0
    EXISTED = 1

class CourseCSVServer(WebSocketHandler):
    
    LOG_LEVEL_NONE  = 0
    LOG_LEVEL_ERR   = 1
    LOG_LEVEL_INFO  = 2
    LOG_LEVEL_DEBUG = 3
    
    # Time interval after which a 'dot' or other progress
    # indicator is sent to the calling browser:
    PROGRESS_INTERVAL = 3 # seconds
    
    # Max number of lines from each csv table to output
    # as samples to the calling browser for human sanity 
    # checking:
    NUM_OF_TABLE_SAMPLE_LINES = 5
    
    # MySQL database on local server where the support tables
    # are stored (see header comments):
    SUPPORT_TABLES_DB = 'Misc'
    
    # Root of directory where computed tables are dropped.
    # The tables are in DELIVERY_HOME/<course_name>, where course_name
    # is the name of the course with slashes replaced by underscores:
    DELIVERY_HOME = '/home/dataman/Data/CustomExcerpts'
    
    # Regex to chop the front off a filename like:
    # '/tmp/tmpvOBuB1_engagement_CME_MedStats_2013-2015_weeklyEffort.csv'
    # group(0) will contain 'engagement...' to the end: 
    #ENGAGEMENT_FILE_CHOPPER_PATTERN = re.compile(r'[^_]*_(.*)')
    ENGAGEMENT_FILE_CHOPPER_PATTERN = re.compile(r'(engage.*)')

    # Bogus course name elimination pattern. This pattern is
    # to find course names that are just for demos or trials:
    # Sandbox, sandbox, /demo, and /Demo... The '?:' just 
    # causes the parenthesized expression to not be captured.
    # Would be no harm to let the parens be a capture group
    # as well, by elminiating the ?:.
    BOGUS_COURSE_NAME_PATTERN = re.compile(r'(?:[sS]andbox|/[dD]emo)')
    
    # Regex to chop the front off a filename like:
    # '/tmp/tmpvOBuB1_forum_CME_MedStats_2013-2015.csv'
    # group(0) will contain 'forum...' to the end: 
    FORUM_FILE_CHOPPER_PATTERN = re.compile(r'(forum.*)')
    
    def __init__(self, application, request, testing=False ):
        '''
        Invoked when browser accesses this server via ws://...
        Register this handler instance in the handler list.

        :param application: Application object that defines the collection of handlers.
        :type application: tornado.web.Application
        :param request: a request object holding details of the incoming request
        :type request:HTTPRequest.HTTPRequest
        :param kwargs: dict of additional parameters for operating this service.
        :type kwargs: dict
        '''
        if not testing:
            super(CourseCSVServer, self).__init__(application, request)
            self.defaultDb = 'Edx'
        else:
            self.defaultDb = 'unittest'
        self.testing = testing
        self.request = request;        

        self.loglevel = CourseCSVServer.LOG_LEVEL_DEBUG
        #self.loglevel = CourseCSVServer.LOG_LEVEL_NONE

        # Locate the makeCourseCSV.sh script:
        self.thisScriptDir = os.path.dirname(__file__)
        self.exportCSVScript = os.path.join(self.thisScriptDir, '../scripts/makeCourseCSVs.sh')
        self.searchCourseNameScript = os.path.join(self.thisScriptDir, '../scripts/searchCourseDisplayNames.sh')
        self.exportForumScript = os.path.join(self.thisScriptDir, '../scripts/makeForumCSV.sh')        
        self.exportEmailListScript = os.path.join(self.thisScriptDir, '../scripts/makeEmailListCSV.sh')        
        
        # A dict into which the various exporting methods
        # below will place instances of tempfile.NamedTemporaryFile().
        # Those are used as comm buffers between shell scripts
        # and this Python code: 
        self.infoTmpFiles = {}
        self.dbError = 'no error'
        if testing:
            self.currUser = 'unittest'
        else:
            self.currUser = getpass.getuser()
        try:
            with open('/home/%s/.ssh/mysql' % self.currUser, 'r') as fd:
                self.mySQLPwd = fd.readline().strip()
                self.mysqlDb = MySQLDB(user=self.currUser, passwd=self.mySQLPwd, db=self.defaultDb)
        except Exception:
            try:
                # Try w/o a pwd:
                self.mySQLPwd = None
                self.mysqlDb = MySQLDB(user=self.currUser, db=self.defaultDb)
            except Exception as e:
                # Remember the error msg for later:
                self.dbError = `e`;
                self.mysqlDb = None
            
        self.currTimer = None
    
    def allow_draft76(self):
        '''
        Allow WebSocket connections via the old Draft-76 protocol. It has some
        security issues, and was replaced. However, Safari (i.e. e.g. iPad)
        don't implement the new protocols yet. Overriding this method, and
        returning True will allow those connections.
        '''
        return True    
    
    def open(self): #@ReservedAssignment
        '''
        Called by WebSocket/tornado when a client connects. Method must
        be named 'open'
        '''
        self.logDebug("Open called")
    
    def on_message(self, message):
        '''
        Connected browser requests action: "<actionType>:<actionArg(s)>,
        where actionArgs is a single string or an array of items.

        :param message: message arriving from the browser
        :type message: string
        '''
        #print message
        try:
            requestDict = json.loads(message)
            self.logDebug("request received: %s" % str(message))
        except Exception as e:
            self.writeError("Bad JSON in request received at server: %s" % `e`)

        
        # Get the request name:
        try:
            requestName = requestDict['req']
            args        = requestDict['args']

            # Caller wants list of course names?
            if requestName == 'reqCourseNames':
                # For course name list requests, args is a 
                # MySQL regex that returned course names are to match:
                courseRegex = args.strip()
                self.handleCourseNamesReq(requestName, courseRegex)
                return
            # JavaScript at the browser 'helpfully' adds a newline
            # after course id that is checked by user. If the 
            # arguments include a 'courseId' key, then strip its
            # value of any trailing newlines:
            try:
                courseId = args['courseId']
                args['courseId'] = courseId.strip()
                courseIdWasPresent = True
            except (KeyError, TypeError, AttributeError):
                # Arguments either doesn't have a courseId key
                # (KeyError), or args isn't a dict in the first
                # place; both legitimate requests:
                courseIdWasPresent = False 
                pass

            # Check whether the target directory where we
            # will put results already exists. If so,
            # and if the directory contains files, then check
            # whether we are allowed to wipe the files. All
            # this only if the request will touch that directory.
            # That in turn is signaled by courseId being non-None.
            if courseIdWasPresent:
                (self.fullTargetDir, dirExisted) = self.constructCourseSpecificDeliveryDir(courseId)
            # Similarly for email list request:
            wantsEmailList = args.get('emailList', False) 
            if wantsEmailList:
                emailStartDate = args.get('emailStartDate', None)
                # Check that email list start date was delivered
                # with the request:
                if emailStartDate is None:
                    self.writeErr('In on_message: start date was not included; could not export email list.')
                    return
                (self.fullTargetDir, dirExisted) = self.constructEmailListDeliveryDir(emailStartDate)
            if courseIdWasPresent or wantsEmailList:
                # Check whether delivery file already exists, and deal 
                # with it if it does:                                                                      
                filesInTargetDir = os.listdir(self.fullTargetDir)
                if dirExisted and len(filesInTargetDir) > 0:
                    # Are we allowed to wipe the directory?
                    xpungeExisting = self.str2bool(args.get("wipeExisting", False))
                    if not xpungeExisting:
                        self.writeError("Table(s) for %s %s already existed, and Remove Previous Exports... was not checked." %\
                                        ('course' if courseIdWasPresent else 'email', courseId if courseIdWasPresent else ''))
                        return None
                    for oneFile in filesInTargetDir:
                        try:
                            os.remove(os.path.join(self.fullTargetDir, oneFile))
                        except:
                            # Best effort:
                            pass
            
            courseList = None

            if requestName == 'getData':
                startTime = datetime.datetime.now()
                if courseIdWasPresent and (courseId == 'None' or courseId is None):
                    # Need list of all courses, b/c we'll do
                    # engagement analysis for all; use MySQL wildcard:
                    courseList = self.queryCourseNameList('%')
                
                if args.get('basicData', False):
                    self.setTimer()
                    if courseList is not None:
                        for courseName in courseList:
                            args['courseId'] = courseName
                            self.exportClass(args)
                    else:
                        self.exportClass(args)
                if args.get('engagementData', False):
                    self.setTimer()
                    if courseList is not None:
                        for courseName in courseList:
                            args['courseId'] = courseName
                            self.exportTimeEngagement(args)
                    else:
                        self.exportTimeEngagement(args)
                        
                if args.get('edxForumRelatable', False) or args.get('edxForumIsolated', False):
                    self.setTimer()
                    if courseList is not None:
                        for courseName in courseList:
                            args['courseId'] = courseName
                            self.exportForum(args)
                    else:
                        self.exportForum(args)
                        
                if wantsEmailList:
                    self.setTimer()
                    self.exportEmailList(args)
                        
                        
                self.cancelTimer()
                endTime = datetime.datetime.now() - startTime

                deliveryUrl = self.printTableInfo()
                
                # Get a timedelta object with the microsecond
                # component subtracted to be 0, so that the
                # microseconds won't get printed:        
                duration = endTime - datetime.timedelta(microseconds=endTime.microseconds)
                self.writeResult('progress', "<br>Runtime: %s<br>" % str(duration))
                                
                # Add an example client letter:
                self.addClientInstructions(args, deliveryUrl)

            else:
                self.writeError("Unknown request name: %s" % requestName)
        except Exception as e:
            # Stop sending progress indicators to browser:
            self.cancelTimer()
            self.logErr('Error while processing req: %s' % `e`)
            # Need to escape double-quotes so that the 
            # browser-side JSON parser for this response
            # doesn't get confused:
            #safeResp = json.dumps('(%s): %s)' % (requestDict['req']+str(requestDict['args']), `e`))
            #self.writeError("Server could not extract request name/args from %s" % safeResp)
            self.writeError("%s" % `e`)
            
    def handleCourseNamesReq(self, requestName, courseRegex):
        '''
        Given a MySQL type regex in return a list of course
        names that match the regex.
        
        :param requestName:
        :type requestName:
        :param courseRegex:
        :type courseRegex:
        '''
        try:
            courseRegex = courseRegex
            matchingCourseNames = self.queryCourseNameList(courseRegex)
            # Check whether __init__() method was unable to log into 
            # the db:
            if matchingCourseNames is None:
                self.writeError('Server could not log into database: %s' % self.dbError)
                return
        except Exception as e:
            self.writeError(`e`)
            return
        
        # Remove the most obvious bogus courses.
        # We use list comprehension: keep all names
        # that do not match the BOGUS_COURSE_NAME_PATTERN:
        finalCourseList = [courseName for courseName in matchingCourseNames if CourseCSVServer.BOGUS_COURSE_NAME_PATTERN.search(courseName) is None]
        
        self.writeResult('courseList', finalCourseList)
        
    def writeError(self, msg):
        '''
        Writes a response to the JS running in the browser
        that indicates an error. Result action is "error",
        and "args" is the error message string:

        :param msg: error message to send to browser
        :type msg: String
        '''
        self.logDebug("Sending err to browser: %s" % msg)
        if not self.testing:
            errMsg = '{"resp" : "error", "args" : "%s"}' % msg
            self.write_message(errMsg)

    def writeResult(self, responseName, args):
        '''
        Write a JSON formatted result back to the browser.
        Format will be::
        
        {"resp" : "<respName>", "args" : "<jsonizedArgs>"}
        
        That is, the args will be turned into JSON that is the
        in the response's "args" value:

        :param responseName: name of result that is recognized by the JS in the browser
        :type responseName: String
        :param args: any Python datastructure that can be turned into JSON
        :type args: {int | String | [String] | ...}
        '''
        self.logDebug("Prep to send result to browser: %s" % responseName + ':' +  str(args))
        jsonArgs = json.dumps(args)
        msg = '{"resp" : "%s", "args" : %s}' % (responseName, jsonArgs)
        self.logDebug("Sending result to browser: %s" % msg)
        if not self.testing:
            self.write_message(msg)
        
    def exportClass(self, detailDict):
        '''
        Export basic info about one class: EventXtract, VideoInteraction, and ActivityGrade.
        {courseId : <the courseID>, wipeExisting : <true/false wipe existing class tables files>}

        :param detailDict: Dict with all info necessary to export standard class info. 
        :type detailDict: {String : String, String : Boolean}
        '''
        theCourseID = detailDict.get('courseId', '').strip()
        if len(theCourseID) == 0:     
            self.writeError('Please fill in the course ID field.')
            return False
        # Check whether we are to delete any already existing
        # csv files for this class:
        xpungeExisting = self.str2bool(detailDict.get("wipeExisting", False))
        inclPII = self.str2bool(detailDict.get("inclPII", False))
        cryptoPWD = detailDict.get("cryptoPwd", '')
        
        infoXchangeFile = tempfile.NamedTemporaryFile()
        self.infoTmpFiles['exportClass'] = infoXchangeFile
            
        # Build the CL command for script makeCourseCSV.sh
        scriptCmd = [self.exportCSVScript,'-u',self.currUser]
        if self.mySQLPwd is not None:
            scriptCmd.extend(['-w',self.mySQLPwd])
        if xpungeExisting:
            scriptCmd.append('-x')
        # Tell script where to report names of tmp files
        # where it deposited results:
        scriptCmd.extend(['-i', infoXchangeFile.name])
        if inclPII:
            scriptCmd.extend(['-c',cryptoPWD])
        scriptCmd.append(theCourseID)
        
        #************
        self.logDebug("Script cmd is: %s" % str(scriptCmd))
        #************
        
        # Call makeClassCSV.sh to export 
        # The  script will place a list of three
        # alternating file names and file sizes (in lines)
        # into infoXchangeFile. The files are csv file paths
        # of export results. After these six lines will 
        # be up to five sample lines from each file. The
        # sample batches will be separated by the string
        # "herrgottzemenschnochamal!" 
        try:
            #pipeFromScript = subprocess.Popen(scriptCmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            pipeFromScript = subprocess.Popen(scriptCmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            while pipeFromScript.poll() is None:
                (msgFromScript,errmsg) = pipeFromScript.communicate()
                if len(errmsg) > 0:
                    self.writeResult('progress', errmsg)
                else:
                    self.writeResult('progress', msgFromScript)
                    
            #**********8            
            #for msgFromScript in pipeFromScript:
            #    self.writeResult('progress', msgFromScript)
            #**********8                            
        except Exception as e:
            self.writeError(`e`)
        
        return True
    
    def exportTimeEngagement(self, detailDict):
        '''
        Export two CSV files: a summary of time effort aggregated over all students,
        and a per-student-per-week aggregation. 
        detailDict provides any necessary info: 
           {courseId : <the courseID>, wipeExisting : <true/false wipe existing class tables files>}
           
        Places names of three temp files into
        instance variables:
          - self.latestResultSummaryFilename = summaryFile
          - self.latestResultDetailFilename  = detailFile
          - self.latestResultWeeklyEffortFilename = weeklyEffortFile
           
        :param detailDict: Dict with all info necessary to export standard class info. 
        :type detailDict: {String : String, String : Boolean}
        :return: tri-tuple of three filenames: the engagement summary .csv file,
            the engagement detail file .csv file, and the engagement weekly effort
            filename.

        :rtype: (String,String,String)
        '''        
        
        # Get the courseID to profile. If that ID is None, 
        # then profile all courses. The Web UI may allow only
        # one course to profile at a time, but the capability
        # to do all is here; just have on_message put None
        # as the courseId:
        try:
            courseId = detailDict['courseId']
        except KeyError:
            self.logErr('In exportTimeEngagement: course ID was not included; could not compute engagement tables.')
            return
        
        inclPII = self.str2bool(detailDict.get("inclPII", False))
        cryptoPWD = detailDict.get("cryptoPwd", '')
                
        # Should we consider only classes that started 
        # during a particular year?
        try:
            startYearsArr = detailDict['startYearsArr'] 
        except KeyError:
            # no limit on the start year:
            startYearsArr = None

        # Get an engine that will compute the time engagement:
        invokingUser = getpass.getuser()
        self.mysqlDb.close()
        # Are we only to consider video events?
        videoEventsOnly = detailDict.get('engageVideoOnly', False)
        engagementComp = EngagementComputer(coursesStartYearsArr=startYearsArr,                                                                                        
                                    mySQLUser=invokingUser,                                                                                                    
                                    courseToProfile=courseId,
                                    videoOnly=(True if videoEventsOnly else False)                                                                                             
                                    )                                                                                                                          
        engagementComp.run()
        (summaryFile, detailFile, weeklyEffortFile) = engagementComp.writeResultsToDisk()
        # The files will be in paths like:
        #     /tmp/tmpAK5svP_engagement_Engineering_CRYP999_Cryptopgraphy_Repository_summary.csv
        #     /tmp/tmpxpo4Ng_engagement_CME_MedStats_2013-2015_allData.csv,
        #     /tmp/tmpvIXpVB_engagement_CME_MedStats_2013-2015_weeklyEffort.csv
        # That is: /tmp/<tmpfilePrefix>_engagement_<courseName>_<fileContent>.csv
        # 
        # Move the files to their delivery directory without the
        # leading temp prefix. First, see whether the directory exists:
        
        # For each of the file names, get the part starting
        # with 'engagement_...':
        try:
            self.latestResultSummaryFilename = CourseCSVServer.ENGAGEMENT_FILE_CHOPPER_PATTERN.search(summaryFile).group(0)
        except Exception as e:
            errmsg = 'Could not construct target file name for %s: %s' % (summaryFile, `e`) 
            self.writeResult('progress', errmsg)
            return
        try:
            self.latestResultDetailFilename = CourseCSVServer.ENGAGEMENT_FILE_CHOPPER_PATTERN.search(detailFile).group(0)
        except Exception as e:
            errmsg = 'Could not construct target file name for %s: %s' % (detailFile, `e`) 
            self.writeResult('progress', errmsg)
            return
        try:
            self.latestResultWeeklyEffortFilename = CourseCSVServer.ENGAGEMENT_FILE_CHOPPER_PATTERN.search(weeklyEffortFile).group(0)
        except Exception as e:
            errmsg = 'Could not construct target file name for %s: %s' % (weeklyEffortFile, `e`) 
            self.writeResult('progress', errmsg)
            return
        
        fullSummaryFile = os.path.join(self.fullTargetDir, self.latestResultSummaryFilename)
        fullDetailFile = os.path.join(self.fullTargetDir, self.latestResultDetailFilename)
        fullWeeklyFile  = os.path.join(self.fullTargetDir, self.latestResultWeeklyEffortFilename)
        
        # Move all three files to their final resting place.
        shutil.move(summaryFile, fullSummaryFile)
        shutil.move(detailFile, fullDetailFile)
        shutil.move(weeklyEffortFile, fullWeeklyFile)
        os.chmod(fullSummaryFile, 0644)
        os.chmod(fullDetailFile, 0644)
        os.chmod(fullWeeklyFile, 0644)
        
        self.latestResultSummaryFilename = fullSummaryFile
        self.latestResultDetailFilename  = fullDetailFile
        self.latestResultWeeklyEffortFilename = fullWeeklyFile
        
        # Save information for printTableInfo() method to fine:
        infoXchangeFile = tempfile.NamedTemporaryFile()
        self.infoTmpFiles['exportEngagement'] = infoXchangeFile

        infoXchangeFile.write(fullSummaryFile + '\n')
        infoXchangeFile.write(str(self.getNumFileLines(fullSummaryFile)) + '\n')
        
        infoXchangeFile.write(fullDetailFile + '\n')
        infoXchangeFile.write(str(self.getNumFileLines(fullDetailFile)) + '\n')

        infoXchangeFile.write(fullWeeklyFile + '\n')
        infoXchangeFile.write(str(self.getNumFileLines(fullWeeklyFile)) + '\n')
        
        # Add sample lines:
        infoXchangeFile.write('herrgottzemenschnochamal!\n')
        try:
            with open(fullSummaryFile, 'r') as fd:
                head = []
                for lineNum,line in enumerate(fd):
                    head.append(line)
                    if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                        break
                infoXchangeFile.write(''.join(head))
            infoXchangeFile.write('herrgottzemenschnochamal!\n')
            with open(fullDetailFile, 'r') as fd:
                head = []
                for lineNum,line in enumerate(fd):
                    head.append(line)
                    if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                        break
                infoXchangeFile.write(''.join(head))
            infoXchangeFile.write('herrgottzemenschnochamal!\n')
            with open(fullWeeklyFile, 'r') as fd:
                head = []
                for lineNum,line in enumerate(fd):
                    head.append(line)
                    if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                        break
                infoXchangeFile.write(''.join(head))
            infoXchangeFile.write('herrgottzemenschnochamal!\n')            
        except IOError as e:
            self.logErr('Could not write result sample lines: %s' % `e`)
                                  
        if inclPII:
            targetZipFileBasename = courseId.replace('/','_')
            targetZipFile = os.path.join(self.fullTargetDir,
                                         targetZipFileBasename + '_' + 'engagement_report.zip') 
            self.zipFiles(targetZipFile,
                          cryptoPWD,
                          [fullSummaryFile,
                           fullDetailFile,
                           fullWeeklyFile]
                          )
            
            # Remove the clear-text originals:
            try:
                os.remove(fullSummaryFile)
            except:
                pass
            try:
                os.remove(fullDetailFile)
            except:
                pass
            try:
                os.remove(fullWeeklyFile)
            except:
                pass
            os.chmod(targetZipFile, 0644)

        return (self.latestResultSummaryFilename, self.latestResultDetailFilename, self.latestResultWeeklyEffortFilename)

    def exportForum(self, detailDict):
        '''
        Export one CSV file: the forum data for the given course.
        Two cases: Web client asked for relatable data, or they asked
        for isolated data. Relatable data gets anon_screen_name filled
        in with the uid that is also used in the rest of the data archive.
        The forum_uid is then set to -1 for all rows.
        
        If Web client instead asked for isolated forum data, the data
        are delivered as they are stored in the database: anon_screen_name
        is redacted, and forum_uid is some encrypted string. Each string refers
        to one particular course participant, and can therefore be used for
        forum network analysis, post frequency, etc. But relating that data
        to, for instance, video usage data is not possible.
        
        In either case, the post bodies in the database are anonymized as best 
        we can: emails, phone numbers, zip codes, poster's name are all redacted.     
        
        detailDict provides any necessary info: 
           {courseDisplayName : <the courseID>, 
            relatable : <true/false>,
            cryptoPwd : <pwd to use for zip file encryption>}
           
        :param detailDict: Dict with all info necessary to export standard class info. 
        :type detailDict: {String : String, String : Boolean}
        :return: the encrypted zip filename that contains the result. 
        :rtype: String
        '''        
        
        # Get the courseID to profile. If that ID is None, 
        # then export all class' forum. The Web UI may allow only
        # one course to profile at a time, but the capability
        # to do all is here; just have on_message put None
        # as the courseDisplayName:
        try:
            courseDisplayName = detailDict['courseId']
        except KeyError:
            self.logErr('In exportForum: course ID was not included; could not export forum data.')
            return
        
        if len(courseDisplayName) == 0:     
            self.writeError('Please fill in the course ID field.')
            return False

        # Check whether we are to delete any already existing
        # csv files for this class:
        xpungeExisting = self.str2bool(detailDict.get("wipeExisting", False))
        makeRelatable = self.str2bool(detailDict.get("edxForumRelatable", False))
        cryptoPwd = detailDict.get("cryptoPwd", '')

        infoXchangeFile = tempfile.NamedTemporaryFile()
        self.infoTmpFiles['exportEmailList'] = infoXchangeFile

        # Build the CL command for script makeForumCSV.sh
        # script name plus options:
        scriptCmd = [self.exportForumScript,'-u',self.currUser]
        
        if self.mySQLPwd is not None:
            scriptCmd.extend(['-w',self.mySQLPwd])
            
        if xpungeExisting:
            scriptCmd.append('--xpunge')
            
        # Tell script where to report names of tmp files
        # where it deposited results:
        scriptCmd.extend(['--infoDest',infoXchangeFile.name])
        
        # Tell script whether it is to make the exported Forum
        # excerpt relatable:
        if makeRelatable:
            scriptCmd.extend(['--relatable'])
            
        # Provide the script with a pwd with which to encrypt the 
        # .csv.zip file:
        if cryptoPwd is None or len(cryptoPwd) == 0:
            self.logErr("Forum export needs to be encrypted, and therefore needs a crypto pwd to use.")
            return;
        scriptCmd.extend(['--cryptoPwd', cryptoPwd])
        
        # If unittesting, tell the script, so that it looks
        # for the 'contents' table in db unittest, rather 
        # than db EdxForum:
        if self.testing:
            scriptCmd.extend(['--testing'])
        
        # The argument:
        scriptCmd.append(courseDisplayName)
        
        #************
        self.logDebug("Script cmd is: %s" % str(scriptCmd))
        #************

        # Call makeForumCSV.sh to export:
        # The  script will place a file name and a file size (in lines)
        # into infoXchangeFile. The file is the csv file path
        # of export results. The third line to EOF will be 
        # five sample rows from the forum to be sent to the
        # browser for QA:
        try:
            pipeFromScript = subprocess.Popen(scriptCmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            while pipeFromScript.poll() is None:
                (msgFromScript,errmsg) = pipeFromScript.communicate()
                if len(errmsg) > 0:
                    self.writeResult('progress', errmsg)
                    if self.testing:
                        raise IOError('Error in makeForumCSV.sh: %s.' % errmsg)
                    return
                else:
                    self.writeResult('progress', msgFromScript)
                    
            #**********            
            #for msgFromScript in pipeFromScript:
            #    self.writeResult('progress', msgFromScript)
            #**********                            
        except Exception as e:
            self.writeError(`e`)
            if self.testing:
                raise

    def exportEmailList(self, detailDict):
        
        try:
            emailStartDate = detailDict['emailStartDate']
        except KeyError:
            self.logErr('In exportEmailList: start date was not included; could not export email list.')
            return
        
        # Check whether we are to delete any already existing
        # csv files for this class:
        xpungeExisting = self.str2bool(detailDict.get("wipeExisting", False))
        cryptoPwd = detailDict.get("cryptoPwd", '')

        infoXchangeFile = tempfile.NamedTemporaryFile()
        self.infoTmpFiles['exportEmailList'] = infoXchangeFile

        # Build the CL command for script makeEmailListCSV.sh
        # script name plus options:
        scriptCmd = [self.exportEmailListScript,'-u',self.currUser]
        
        if self.mySQLPwd is not None:
            scriptCmd.extend(['-w',self.mySQLPwd])
            
        if xpungeExisting:
            scriptCmd.append('--xpunge')
            
        # Tell script where to report names of tmp files
        # where it deposited results:
        scriptCmd.extend(['--infoDest',infoXchangeFile.name])
        
        # Provide the script with a pwd with which to encrypt the 
        # .csv.zip file:
        if cryptoPwd is None or len(cryptoPwd) == 0:
            self.logErr("Email list export needs to be encrypted, and therefore needs a crypto pwd to use.")
            return;
        scriptCmd.extend(['--cryptoPwd', cryptoPwd])
        
        # If unittesting, tell the script:
        if self.testing:
            scriptCmd.extend(['--testing'])
        
        # The argument:
        scriptCmd.append(emailStartDate)
        
        #************
        self.logDebug("Script cmd is: %s" % str(scriptCmd))
        #************

        # Call makeEmailListCSV.sh to export:
        # The  script will place a file name and a file size (in lines)
        # into infoXchangeFile. The file is the csv file path
        # of export results. The third line to EOF will be 
        # five sample rows from the list of emails to be sent to the
        # browser for QA:
        try:
            pipeFromScript = subprocess.Popen(scriptCmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            while pipeFromScript.poll() is None:
                (msgFromScript,errmsg) = pipeFromScript.communicate()
                if len(errmsg) > 0:
                    self.writeResult('progress', errmsg)
                    if self.testing:
                        raise IOError('Error in makeEmailListCSV.sh: %s.' % errmsg)
                    return
                else:
                    self.writeResult('progress', msgFromScript)
                    
            #**********            
            #for msgFromScript in pipeFromScript:
            #    self.writeResult('progress', msgFromScript)
            #**********                            
        except Exception as e:
            self.writeError(`e`)
            if self.testing:
                raise

    def getNumFileLines(self, fileFdOrPath):
        '''
        Given either a file descriptor or a file path string,
        return the number of lines in the file.
        :param fileFdOrPath:
        :type fileFdOrPath:
        '''
        if type(fileFdOrPath) == file:
            return sum(1 for line in fileFdOrPath) #@UnusedVariable
        else:
            return sum(1 for line in open(fileFdOrPath)) #@UnusedVariable
    
    def zipFiles(self, destZipFileName, cryptoPwd, filePathsToZip):
        '''
        Creates an encrypted zip file.

        :param destZipFileName: full path of the final zip file
        :type destZipFileName: String
        :param cryptoPwd: password to which zip file will be encrypted
        :type cryptoPwd: String
        :param filePathsToZip: array of full file paths to zip
        :type filePathsToZip: [String]
        '''
        # The --junk-paths below avoids having all file
        # names in the zip be full paths. Instead they 
        # will only be the basenames:
        zipCmd = ['zip',
                  '--junk-paths',
                  '--password',
                  cryptoPwd,
                  destZipFileName
                  ]
        # Add all the file names to be zipped to the command:
        zipCmd.extend(filePathsToZip)
        subprocess.call(zipCmd)
    
    def constructCourseSpecificDeliveryDir(self, courseName):
        '''
        Given a course name, construct a directory name where result
        files for that course will be stored to be visible on the 
        Web. The parent dir is expected in CourseCSVServer.DELIVERY_HOME.
        The leaf dir is constructed as DELIVERY_HOME/courseName

        :param courseName: course name for which results will be deposited in the dir
        :type courseName: String
        :return: Two-tuple: the already existing directory path, and flag PreExisted.EXISTED if 
                 the directory already existed. Method does nothing in this case. 
                 If the directory did not exist, the constructed directory plus PreExisting.DID_NOT_EXIST
                 are returned. Creation includes all intermediate subdirectories.

        :rtype: (String, PreExisting)
        '''
        # Ensure there are no forward slashes in the
        # coursename (there usually are); replace them
        # with underscores:
        courseName = courseName.replace('/','_')
        self.fullTargetDir = os.path.join(CourseCSVServer.DELIVERY_HOME, courseName).strip()
        if os.path.isdir(self.fullTargetDir):
            return (self.fullTargetDir, PreExisted.EXISTED)
        else:
            os.makedirs(self.fullTargetDir)
            return (self.fullTargetDir, PreExisted.DID_NOT_EXIST)
    
    def constructEmailListDeliveryDir(self, emailListStartDate):
        '''
        Given the start date of an email list export, construct a directory 
        name where result file for that export will be stored to be visible on the 
        Web. The parent dir is expected in CourseCSVServer.DELIVERY_HOME.
        The leaf dir is constructed as DELIVERY_HOME/Email_<emailListStartDate>

        :param emailListStartDate: date for first email to include
        :type courseName: String
        :return: Two-tuple: the already existing directory path, and flag PreExisted.EXISTED if 
                 the directory already existed. Method does nothing in this case. 
                 If the directory did not exist, the constructed directory plus PreExisting.DID_NOT_EXIST
                 are returned. Creation includes all intermediate subdirectories.

        :rtype: (String, PreExisting)
        '''
        self.fullTargetDir = os.path.join(CourseCSVServer.DELIVERY_HOME, 'Email_' + emailListStartDate)
        if os.path.isdir(self.fullTargetDir):
            return (self.fullTargetDir, PreExisted.EXISTED)
        else:
            os.makedirs(self.fullTargetDir)
            return (self.fullTargetDir, PreExisted.DID_NOT_EXIST)
    
    
    def printTableInfo(self):
        '''
        Writes html to browser that shows result table
        file names and sizes. Also sends a few lines
        from each table as samples.

        The information is in dict self.infoTmpFiles.
        Each exporting method above has its own entry 
        in the dict: exportClass, exportForum, and 
        exportEngagement. Each value is the name of an
        open tmp file that contains alternating: file name,
        file size in lines for as many tables as were output.
        
        After that information come batches of up to NUM_OF_TABLE_SAMPLE_LINES
        sample lines for each table. The batches are separated
        by the token "herrgottzemenschnochamal!"
        
        :return: full path of the last table file that was deposited in the Web pickup area.
             This info is used later to construct a pickup URL
        :rtype: String

        '''
        
        for exportFileKey in self.infoTmpFiles.keys():
            try:
                tmpFileFd = self.infoTmpFiles.get(exportFileKey)
                # Ensure we are at start of the tmp file:
                tmpFileFd.seek(0)
                eof = False
                tableInfoDict = OrderedDict()
                # Pull all file name/numlines out of the info file:
                while not eof:
                    tableFileName     = tmpFileFd.readline()
                    if len(tableFileName)  == 0 or tableFileName == 'herrgottzemenschnochamal!\n':
                        eof = True
                        continue 
                    tableFileNumLines = tmpFileFd.readline().strip()
                    tableInfoDict[tableFileName.strip()] = tableFileNumLines
                # Now get all the line samples in the right order:
                sampleLineBatches = []
                if tableFileName == 'herrgottzemenschnochamal!\n':
                    endOfSampleBatch = False
                    eof = False
                    while not eof:
                        sample = ""
                        while not endOfSampleBatch:
                            try:
                                sampleLine = tmpFileFd.readline()
                            except Exception as e:
                                print("Got it: %s" % `e`) #****************
                                
                            if len(sampleLine) == 0:
                                eof = True
                                endOfSampleBatch = True
                                continue
                            if sampleLine == 'herrgottzemenschnochamal!\n':
                                endOfSampleBatch = True
                                continue
                            sample += sampleLine.strip() + ' <br>'
                        sampleLineBatches.append(sample)
                        endOfSampleBatch = False
                    
                sampleBatchIndx = 0
                for tableFileName in tableInfoDict.keys():
                    # Get the table name from the table file name:
                    if tableFileName.find('EventXtract') > -1:
                        tblName = 'EventXtract'
                    elif tableFileName.find('VideoInteraction') > -1:
                        tblName = 'VideoInteraction'
                    elif tableFileName.find('ActivityGrade') > -1:
                        tblName = 'ActivityGrade'
                    elif re.match(r'.*(engagement).*(allData).csv', tableFileName):
                        tblName = 'EngagementDetails'
                    elif re.match(r'.*(engagement).*(summary).csv', tableFileName):
                        tblName = 'EngagementSummary'
                    elif re.match(r'.*(engagement).*(weeklyEffort).csv', tableFileName):
                        tblName = 'EngagementWeeklyEffort'
                    elif tableFileName.find('forum') > -1:
                        tblName = 'Forum'
                    elif tableFileName.find('Piazza') > -1:
                        tblName = 'Piazza'
                    elif tableFileName.find('Edcast') > -1:
                        tblName = 'Edcast'
                    elif tableFileName.find('Email') > -1:
                        tblName = 'EmailList'
        
                    else:
                        tblName = 'unknown table name'
                    
                    numLines = tableInfoDict[tableFileName]
                    # If number of lines is 1, then the table was empty.
                    # The single line is just the column header line:
                    if numLines <= 1:
                        self.writeResult('printTblInfo', 
                                     '<br><b>Table %s</b> is empty.</br>' % tblName)
                        continue # next table
                    self.writeResult('printTblInfo', 
                                     '<br><b>Table %s</b> (%s lines):</br>' % (tblName, numLines))
                    if len(sampleLineBatches) > 0:
                        self.writeResult('printTblInfo', sampleLineBatches[sampleBatchIndx])
                    sampleBatchIndx += 1
            finally:
                tmpFileFd.close()

            # Get the last part of the directory, where the tables are available
            # (i.e. the 'CourseSubdir' in:
            # /home/dataman/Data/CustomExcerpts/CourseSubdir/<tables>.csv:)
            tableDir = os.path.basename(os.path.dirname(tableFileName))
            thisFullyQualDomainName = socket.getfqdn()
            url = "https://%s/instructor/%s" % (thisFullyQualDomainName, tableDir)

            return url
     
    def addClientInstructions(self, args, url):
        '''
        Send the draft of an email message for the client
        back to the browsser. The message will contain the
        URL to where the client can pick up the result. If
        personally identifiable information was requested,
        the draft will include instructions for opening the
        zip file. Instructions for individual tables are added
        depending on which tables the remote caller requested.
        Each export module has its own client instruction HTML
        file that either is sent to the browser, or not.

        :param args: dict of arguments from the request message.
             See method on_message() for details.
        :type args: {String : String}
        :param url: URL where the tables are available to the client
        :type url: String
        '''
        self.writeResult('progress', "<p><b>Email draft for client; copy-paste into email program:</b><br>")
        msgStart = 'Hi,<br>your data is ready for pickup. Please visit our <a href="%s" target="_blank">pickup page</a>.<br>' % url
        self.writeResult('progress', msgStart)
        # The rest of the msg is in a file:
        try:
            emailLines = ""
            with open(os.path.join(self.thisScriptDir, 'clientInstructions.html'), 'r') as txtFd:
                emailLines = txtFd.readlines()
            
            if args.get('basicData', False):
                with open(os.path.join(self.thisScriptDir, 'clientInstructionsBasicTables.html'), 'r') as txtFd:
                    emailLines += txtFd.readlines()
            if args.get('engagementData', False):
                with open(os.path.join(self.thisScriptDir, 'clientInstructionsEngagement.html'), 'r') as txtFd:
                    emailLines += txtFd.readlines()
        
            if args.get('edxForumRelatable', False) or args.get('edxForumIsolated', False):
                with open(os.path.join(self.thisScriptDir, 'clientInstructionsForum.html'), 'r') as txtFd:
                    emailLines += txtFd.readlines()
        
        except Exception as e:
            self.writeError('Could not read client instruction file: %s' % `e`)
            return
        txt = '<br>'.join(emailLines)
        # Replace sequences '<br><p><br>' by '<p>'
        txt = re.sub('<br><p>\n<br>', '<p>', txt)
        txt = re.sub('<br><h2', '<h2', txt)
        txt = re.sub('</h2>\n<br>', '</h2>', txt)
        # Remove \n everywhere:
        txt = string.replace(txt, '\n', '')
        self.writeResult('progress', txt)
      
    def queryCourseNameList(self, courseID):
        '''
        Given a MySQL regexp courseID string, return a list
        of matchine course_display_name in the db. If self.mysql
        is None, indicating that the __init__() method was unable
        to log into the db, then return None.

        :param courseID: Course name regular expression in MySQL syntax.
        :type courseID: String
        :return: An array of matching course_display_name, which may
                 be empty. None if _init__() was unable to log into db.

        :rtype: {[String] | None}
        '''
        courseNames = []
        mySqlCmd = [self.searchCourseNameScript,'-u',self.currUser]
        if self.mySQLPwd is not None:
            mySqlCmd.extend(['-w',self.mySQLPwd])
        mySqlCmd.extend([courseID])
        self.logDebug("About to query for course names on regexp: '%s'" % mySqlCmd)
        
        try:
            pipeFromMySQL = subprocess.Popen(mySqlCmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
        except Exception as e:
            self.writeError('Error while searching for course names: %s' % `e`)
            return courseNames
        for courseName in pipeFromMySQL:
            courseName = courseName.strip()
            if len(courseName) > 0:
                courseNames.append(courseName)
        return courseNames
      
    def reportProgress(self):
        '''
        Writes a dot to remote browser to indicate liveness.
        Restarts timer for next dot.
        '''
        #*******************
        #return
        #*******************
        if not self.testing:
            self.writeResult('progress', '.')
        self.setTimer(CourseCSVServer.PROGRESS_INTERVAL)
     
    def getDeliveryURL(self, courseId):
        '''
        Given a course ID string, return a URL from which
        completed course tables can be picked up:

        :param courseId: course identifier, e.g.: /networking/EE120/Fall
        :type courseId: String
        :return: URL at which tables computed for a class are visible.

        :rtype: String 
        '''
        # FQDN of machine on which this service is running:
        thisFullyQualDomainName = socket.getfqdn()
        # Replace slashes in class by underscores, so that the
        # course ID can be used as part of a directory name:
        courseIdAsDirName = courseId.strip('/').replace('/','_')
        url = "https://%s/instructor/%s" % (thisFullyQualDomainName, courseIdAsDirName)
        return url
                
    def setTimer(self, time=None):
        self.cancelTimer()
        if time is None:
            time = CourseCSVServer.PROGRESS_INTERVAL
        self.currTimer = Timer(time, self.reportProgress)
        self.currTimer.start()

    def cancelTimer(self):
        if self.currTimer is not None:
            self.currTimer.cancel()
            self.currTimer = None
            #self.logDebug('Cancelling progress timer')
        
    def str2bool(self, val):
        '''
        Given a string value that likely indicates
        'False', return the boolean False. In all
        other cases, return the boolean True. Used
        to canonicalize values read off the wire.
        
        :param val: value to convert
        :type val: String
        :return: boolean equivalent
        :rtype: Bool
        '''
        if val in [False, 'false', 'False', '', 'no', 'none', 'None']:
            return False
        else:
            return True
            
    def logInfo(self, msg):
        if self.loglevel >= CourseCSVServer.LOG_LEVEL_INFO:
            print(str(datetime.datetime.now()) + ' info: ' + msg) 

    def logErr(self, msg):
        if self.loglevel >= CourseCSVServer.LOG_LEVEL_ERR:
            print(str(datetime.datetime.now()) + ' error: ' + msg) 

    def logDebug(self, msg):
        if self.loglevel >= CourseCSVServer.LOG_LEVEL_DEBUG:
            print(str(datetime.datetime.now()) + ' debug: ' + msg) 

     
    # -------------------------------------------  Testing  ------------------
                
    def echoParms(self):
        for parmName in self.parms.keys():
            print("Parm: '%s': '%s'" % (self.parms.getvalue(parmName, '')))


if __name__ == '__main__':
    
    application = tornado.web.Application([(r"/exportClass", CourseCSVServer),])
    #application.listen(8080)
    
    # To find the SSL certificate location, we assume
    # that it is stored in dir '.ssl' in the current 
    # user's home dir. 
    # We'll build string up to, and excl. '.crt'/'.key' in (for example):
    #     "/home/paepcke/.ssl/mono.stanford.edu.crt"
    # and "/home/paepcke/.ssl/mono.stanford.edu.key"
    # The home dir and fully qual. domain name
    # will vary by the machine this code runs on:    
    # We assume the cert and key files are called
    # <fqdn>.crt and <fqdn>.key:
    
    homeDir = os.path.expanduser("~")
    thisFQDN = socket.getfqdn()
    
    sslRoot = '%s/.ssl/%s' % (homeDir, thisFQDN)
    #*********
    # For self signed certificate:
    #sslRoot = '/home/paepcke/.ssl/server'
    #*********
    
    sslArgsDict = {
     "certfile": sslRoot + '.crt',
     "keyfile":  sslRoot + '.key',
     }  
    
    http_server = tornado.httpserver.HTTPServer(application,ssl_options=sslArgsDict)
    
    application.listen(8080, ssl_options=sslArgsDict)
        
    try:
        tornado.ioloop.IOLoop.instance().start()
    except Exception as e:
        print("Error inside Tornado ioloop; continuing: %s" % `e`)
    
#          Timer sending dots for progress not working b/c of
#          buffering:
#         *****server.setTimer()
#         exportSuccess = server.exportClass()
#         *****server.cancelTimer()
#         endTime = datetime.datetime.now() - startTime
#          Get a timedelta object with the microsecond
#          component subtracted to be 0, so that the
#          microseconds won't get printed:        
#         duration = endTime - datetime.timedelta(microseconds=endTime.microseconds)
#         server.writeResult('progress', "Runtime: %s" % str(duration))
#         if exportSuccess:
#             server.printClassTableInfo()
#             server.addClientInstructions()
#          
#     
#     sys.stdout.write("event: allDone\n")
#     sys.stdout.write("data: Done in %s.\n\n" % str(duration))
#     sys.stdout.flush()
