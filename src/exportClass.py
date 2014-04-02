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
    
    def __init__(self, application, request, testing=False ):
        '''
        Invoked when browser accesses this server via ws://...
        Register this handler instance in the handler list.
        @param application: Application object that defines the collection of handlers.
        @type application: tornado.web.Application
        @param request: a request object holding details of the incoming request
        @type request:HTTPRequest.HTTPRequest
        @param kwargs: dict of additional parameters for operating this service.
        @type kwargs: dict
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
        
        # A tempfile passed to the makeCourseCSVs.sh script.
        # That script will place file paths to all created 
        # tables into that file:
        self.infoTmpFile = tempfile.NamedTemporaryFile()
        self.dbError = 'no error'
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
        @param message: message arriving from the browser
        @type message: string
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

            # JavaScript at the browser 'helpfully' adds a newline
            # after course id that is checked by user. If the 
            # arguments include a 'courseId' key, then strip its
            # value of any trailing newlines:
            try:
                courseId = args['courseId']
                args['courseId'] = courseId.strip()
                courseIdWasPresent = True
            except (KeyError, TypeError):
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
            # That in turn is signaled by courseId being non-None:
            if courseIdWasPresent:
                (self.fullTargetDir, dirExisted) = self.constructDeliveryDir(courseId)
                filesInTargetDir = os.listdir(self.fullTargetDir)
                if dirExisted and len(filesInTargetDir) > 0:
                    # Are we allowed to wipe the directory?
                    xpungeExisting = args.get("wipeExisting", False)
                    if not xpungeExisting or xpungeExisting == 'False':
                        self.writeError("Tables for course %s already existed, and Remove Previous Exports... was not checked." % courseId)
                        return None
                    for oneFile in filesInTargetDir:
                        try:
                            os.remove(os.path.join(self.fullTargetDir, oneFile))
                        except:
                            # Best effort:
                            pass
            
            # Make an array of result csv file paths,
            # which gets filled by the handlers called
            # in the conditional below:
            self.csvFilePaths = []
            courseList = None

            if requestName == 'reqCourseNames':
                self.handleCourseNamesReq(requestName, args)
            elif requestName == 'getData':
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
                self.cancelTimer()
                endTime = datetime.datetime.now() - startTime

                # Send table row samples to browser:
                inclPII = args.get("inclPII", False)
                self.printClassTableInfo(args.get("inclPII", False))
                
                # Get a timedelta object with the microsecond
                # component subtracted to be 0, so that the
                # microseconds won't get printed:        
                duration = endTime - datetime.timedelta(microseconds=endTime.microseconds)
                self.writeResult('progress', "<br>Runtime: %s<br>" % str(duration))
                                
                # Add an example client letter:
                self.addClientInstructions(inclPII)

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
            
    def handleCourseNamesReq(self, requestName, args):
        try:
            courseRegex = args
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
        @param msg: error message to send to browser
        @type msg: String
        '''
        self.logDebug("Sending err to browser: %s" % msg)
        if not self.testing:
            errMsg = '{"resp" : "error", "args" : "%s"}' % msg
            self.write_message(errMsg)

    def writeResult(self, responseName, args):
        '''
        Write a JSON formatted result back to the browser.
        Format will be {"resp" : "<respName>", "args" : "<jsonizedArgs>"},
        That is, the args will be turned into JSON that is the
        in the response's "args" value:
        @param responseName: name of result that is recognized by the JS in the browser
        @type responseName: String
        @param args: any Python datastructure that can be turned into JSON
        @type args: {int | String | [String] | ...}
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
        @param detailDict: Dict with all info necessary to export standard class info. 
        @type detailDict: {String : String, String : Boolean}
        '''
        theCourseID = detailDict.get('courseId', '').strip()
        if len(theCourseID) == 0:     
            self.writeError('Please fill in the course ID field.')
            return False
        # Check whether we are to delete any already existing
        # csv files for this class:
        xpungeExisting = detailDict.get("wipeExisting", False)
        inclPII = detailDict.get("inclPII", False)
        cryptoPWD = detailDict.get("cryptoPwd", '')
            
        # Build the CL command for script makeCourseCSV.sh
        scriptCmd = [self.exportCSVScript,'-u',self.currUser]
        if self.mySQLPwd is not None:
            scriptCmd.extend(['-w',self.mySQLPwd])
        if xpungeExisting:
            scriptCmd.append('-x')
        # Tell script where to report names of tmp files
        # where it deposited results:
        scriptCmd.extend(['-i',self.infoTmpFile.name])
        if inclPII:
            scriptCmd.extend(['-n',cryptoPWD])
        scriptCmd.append(theCourseID)
        
        #************
        self.logDebug("Script cmd is: %s" % str(scriptCmd))
        #************
        
        # Call makeClassCSV.sh to export:
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
        
        # The bash script will have placed a list of
        # output files it has created into self.infoTmpFile.
        # If the script aborted b/c it did not wish to overwrite
        # existing files, then the script truncated 
        # the file to zero:
        
        if os.path.getsize(self.infoTmpFile.name) > 0:
            self.infoTmpFile.seek(0)
            # Add each file name to self.csvFilePaths
            # for the caller to work with:
            for csvFilePath in self.infoTmpFile:
                self.csvFilePaths.append(csvFilePath.strip())        
                
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
           
        @param detailDict: Dict with all info necessary to export standard class info. 
        @type detailDict: {String : String, String : Boolean}
        @return: tri-tuple of three filenames: the engagement summary .csv file,
            the engagement detail file .csv file, and the engagement weekly effort
            filename.
        @rtype: (String,String,String)
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
        
        inclPII = detailDict.get("inclPII", False)
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
        engagementComp = EngagementComputer(startYearsArr, # Only profile courses that started in one of the given years.
                                            'localhost',   # MySQL server
                                            CourseCSVServer.SUPPORT_TABLES_DB if not self.testing else 'unittest', # DB within that server 
                                            'Activities',  # Support table (must have been created earlier via
                                                           # prepEngagementAnalysis.sql),
                                            #*****openMySQLDB=self.mysqlDb,
                                            mySQLUser=invokingUser, 
                                            mySQLPwd=None, # EngagementComputer will figure it out
                                            courseToProfile=courseId) # Which course to analyze
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
            self.csvFilePaths.append(targetZipFile)
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
        else:
            self.csvFilePaths.extend([fullSummaryFile, fullDetailFile, fullWeeklyFile])

        return (self.latestResultSummaryFilename, self.latestResultDetailFilename, self.latestResultWeeklyEffortFilename)
    
    def zipFiles(self, destZipFileName, cryptoPwd, filePathsToZip):
        '''
        Creates an encrypted zip file.
        @param destZipFileName: full path of the final zip file
        @type destZipFileName: String
        @param cryptoPwd: password to which zip file will be encrypted
        @type cryptoPwd: String
        @param filePathsToZip: array of full file paths to zip
        @type filePathsToZip: [String]
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
    
    def constructDeliveryDir(self, courseName):
        '''
        Given a course name, construct a directory name where result
        files for that course will be stored to be visible on the 
        Web. The parent dir is expected in CourseCSVServer.DELIVERY_HOME.
        The leaf dir is constructed as DELIVERY_HOME/courseName
        @param courseName: course name for which results will be deposited in the dir
        @type courseName: String
        @return: Two-tuple: the already existing directory path, and flag PreExisted.EXISTED if 
                 the directory already existed. Method does nothing in this case. 
                 If the directory did not exist, the constructed directory plus PreExisting.DID_NOT_EXIST
                 are returned. Creation includes all intermediate subdirectories.
        @rtype: (String, PreExisting)
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
    
    def printClassTableInfo(self, inclPII):
        '''
        Writes html to browser that shows result table
        file names and sizes. Also sends a few lines
        from each table as samples.
        In case of PII-including reports, only one file
        exists, and it is zipped and encrypted.
        @param inclPII: whether or not the report includes PII
        @type inclPII: Boolean 
        '''
        
        if inclPII:
            self.writeResult('printTblInfo', '<br><b>Tables are zipped and encrypted</b></br>')
            return
        
        self.logDebug('Getting table names from %s' % str(self.csvFilePaths))
        
        for csvFilePath in self.csvFilePaths:
            tblFileSize = os.path.getsize(csvFilePath)
            # Get the line count:
            lineCnt = 'unknown'
            try:
                # Get a string like: '23 fileName\n', where 23 is an ex. for the line count:
                lineCntAndFilename = subprocess.check_output(['wc', '-l', csvFilePath])
                # Isolate the line count:
                lineCnt = lineCntAndFilename.split(' ')[0]
            except (CalledProcessError, IndexError):
                pass
            
            # Get the table name from the table file name:
            if csvFilePath.find('EventXtract') > -1:
                tblName = 'EventXtract'
            elif csvFilePath.find('VideoInteraction') > -1:
                tblName = 'VideoInteraction'
            elif csvFilePath.find('ActivityGrade') > -1:
                tblName = 'ActivityGrade'
            elif re.match(r'.*(engagement).*(allData).csv', csvFilePath):
                tblName = 'EngagementDetails'
            elif re.match(r'.*(engagement).*(summary).csv', csvFilePath):
                tblName = 'EngagementSummary'
            elif re.match(r'.*(engagement).*(weeklyEffort).csv', csvFilePath):
                tblName = 'EngagementWeeklyEffort'
            else:
                tblName = 'unknown table name'
            
            # Only output size and sample rows if table
            # wasn't empty. Line count of an empty
            # table will be 1, b/c the col header will
            # have been placed in it. So tblFileSize == 0
            # won't happen, unless we change that:
            if tblFileSize == 0 or lineCnt == '1':
                self.writeResult('printTblInfo', '<br><b>Table %s</b> is empty.' % tblName)
                continue
            
            self.writeResult('printTblInfo', 
                             '<br><b>Table %s</b></br>' % tblName +\
                             '(file %s size: %d bytes, %s line(s))<br>' % (csvFilePath, tblFileSize, lineCnt) +\
                             'Sample rows:<br>')
            if tblFileSize > 0:
                lineCounter = 0
                with open(csvFilePath) as infoFd:
                    while lineCounter < CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                        tableRow = infoFd.readline()
                        if len(tableRow) > 0:
                            self.writeResult('printTblInfo', tableRow + '<br>')
                        lineCounter += 1
        #****self.writeResult('<br>')
     
    def addClientInstructions(self, inclPII):
        '''
        Send the draft of an email message for the client
        back to the browsser. The message will contain the
        URL to where the client can pick up the result. If
        personally identifiable information was requested,
        the draft will include instructions for opening the
        zip file.
        @param inclPII: whether or not PII was requested
        @type inclPII: Boolean
        '''
        # Get just the first table path, we just
        # need its subdirectory name to build the
        # URL:
        if len(self.csvFilePaths) == 0:
            #self.writeError("Cannot create client instructions: file %s did not contain table paths.<br>" % self.infoTmpFile.name)
            return
        # Get the last part of the directory,
        # which is the 'CourseSubdir' in
        # /home/dataman/Data/CustomExcerpts/CourseSubdir/<tables>.csv:
        tableDir = os.path.basename(os.path.dirname(self.csvFilePaths[0]))
        thisFullyQualDomainName = socket.getfqdn()
        url = "https://%s/instructor/%s" % (thisFullyQualDomainName, tableDir)
        self.writeResult('progress', "<p><b>Email draft for client; copy-paste into email program:</b><br>")
        msgStart = 'Hi,<br>your data is ready for pickup. Please visit our <a href="%s">pickup page</a>.<br>' % url
        self.writeResult('progress', msgStart)
        # The rest of the msg is in a file:
        try:
            if inclPII:
                with open(os.path.join(self.thisScriptDir, 'clientInstructionsSecure.html'), 'r') as txtFd:
                    lines = txtFd.readlines()
            else:
                with open(os.path.join(self.thisScriptDir, 'clientInstructions.html'), 'r') as txtFd:
                    lines = txtFd.readlines()
        except Exception as e:
            self.writeError('Could not read client instruction file: %s' % `e`)
            return
        txt = '<br>'.join(lines)
        # Remove \n everywhere:
        txt = string.replace(txt, '\n', '')
        self.writeResult('progress', txt)
      
    def queryCourseNameList(self, courseID):
        '''
        Given a MySQL regexp courseID string, return a list
        of matchine course_display_name in the db. If self.mysql
        is None, indicating that the __init__() method was unable
        to log into the db, then return None.
        @param courseID: Course name regular expression in MySQL syntax.
        @type courseID: String
        @return: An array of matching course_display_name, which may
                 be empty. None if _init__() was unable to log into db.
        @rtype: {[String] | None}
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
            if len(courseName) > 0:
                courseNames.append(courseName)
        return courseNames
      
    def reportProgress(self):
        '''
        Writes a dot to remote browser to indicate liveness.
        Restarts timer for next dot.
        '''
        if not self.testing:
            self.writeResult('progress', '.')
        self.setTimer(CourseCSVServer.PROGRESS_INTERVAL)
     
    def getDeliveryURL(self, courseId):
        '''
        Given a course ID string, return a URL from which
        completed course tables can be picked up:
        @param courseId: course identifier, e.g.: /networking/EE120/Fall
        @type courseId: String
        @return: URL at which tables computed for a class are visible.
        @rtype: String 
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
            self.logDebug('Cancelling progress timer')
            
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
