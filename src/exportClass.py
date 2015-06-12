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
import glob
import json
import os
import random
import re
import shutil
import socket
import string
from string import Template
from subprocess import CalledProcessError
import subprocess
import sys
import tempfile
from threading import Timer
import threading
import time # @UnusedImport
import traceback
import zipfile

from engagement import EngagementComputer
from pymysql_utils.pymysql_utils import MySQLDB

from quarterlyReportExporter import QuarterlyReportExporter


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

# New exception class used in checkForOldOutputFiles().
# This exception carries one pieced of added information
# to make feedback to browser more clear:
class ExistingOutFile(Exception):
    actionRequest = None
    def __init__(self, msg, theActionRequest):
        super(ExistingOutFile, self).__init__(msg)
        self.actionRequest = theActionRequest

class CourseCSVServer(WebSocketHandler):

    LOG_LEVEL_NONE  = 0
    LOG_LEVEL_ERR   = 1
    LOG_LEVEL_INFO  = 2
    LOG_LEVEL_DEBUG = 3

    # Time interval after which a 'dot' or other progress
    # indicator is sent to the calling browser as heartbeat:
    PROGRESS_INTERVAL = 3 # seconds

    # Time interval after which heartbeat sending is
    # written to the debug log:
    PROGRESS_LOGGING_INTERVAL = 30 # seconds

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

    # Regex to separate first col from second
    # col in something like 'foo bar': returns 'foo':
    COURSE_NAME_SEP_PATTERN = re.compile(r'([^\s]*)')

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

        #self.loglevel = CourseCSVServer.LOG_LEVEL_DEBUG
        self.loglevel = CourseCSVServer.LOG_LEVEL_INFO
        #self.loglevel = CourseCSVServer.LOG_LEVEL_NONE

        # Get and remember the fully qualified domain name
        # of this server, *as seen from the outside*, i.e.
        # from the WAN, outside any router that this server
        # might be behind:
        self.FQDN = self.getFQDN()

        # Interval between logging the sending of
        # the heartbeat:
        self.latestHeartbeatLogTime = None

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
            if requestDict['req'] == 'keepAlive':
                # Could return live-ping here! But shouldn't
                # need to, because sending the '.' periodically
                # during long ops is enough. Sending that dot
                # will cause the browser to send its keep-alive:
                return
            else:
                self.logInfo("request received: %s" % str(message))
        except Exception as e:
            self.writeError("Bad JSON in request received at server: %s" % `e`)

        self.logDebug("About to fork thread for request '%s'" % str(requestDict))

        serverThread = DataServer(requestDict, self, self.testing)
        serverThread.start()
        # If we are testing the unittest needs to wait
        # for the thread to finish, so that results can
        # be checked. During production ops we return
        # to the Tornado main loop as quickly as we can.
        if self.testing:
            serverThread.join()


    def logInfo(self, msg):
        if self.loglevel >= CourseCSVServer.LOG_LEVEL_INFO:
            print(str(datetime.datetime.now()) + ' info: ' + msg)

    def logErr(self, msg):
        if self.loglevel >= CourseCSVServer.LOG_LEVEL_ERR:
            print(str(datetime.datetime.now()) + ' error: ' + msg)

    def logDebug(self, msg):
        if self.loglevel >= CourseCSVServer.LOG_LEVEL_DEBUG:
            print(str(datetime.datetime.now()) + ' debug: ' + msg)

    def getFQDN(self):
        '''
        Obtain true fully qualified domain name of server, as
        seen from the 'outside' of any router behind which the
        server may be hiding. Strategy: use shell cmd "wget -q -O- icanhazip.com"
        to get IP address as seen from the outside. Then use
        gethostbyaddr() to do reverse DNS lookup.

        @return: this server's fully qualified IP name.
        @rtype: string
        @raise ValueError: if either the WAN IP lookup, or the subsequent
            reverse DNS lookup fail.
        '''

        try:
            ip = subprocess.check_output(['wget', '-q', '-O-', 'icanhazip.com'])
        except CalledProcessError:
            # Could not get the outside IP address. Fall back
            # on using the FQDN obtained locally:
            return socket.getfqdn()

        try:
            fqdn = socket.gethostbyaddr(ip.strip())[0]
        except socket.gaierror:
            raise("ValueError: could not find server's fully qualified domain name from IP address '%s'" % ip.string())
        except Exception as e:
            raise("ValueError: could not find server's fully qualified domain: '%s'" % `e`)
        return fqdn

    def getFQDNWithoutDigits(self):
        '''
        Returns fully qualified domain name of server, but with
        all digits removed. Thus if the current machine's FQDN
        is datastage2.stanford.edu, then datastage.stanford.edu
        is returned. Note getFQDN() is much preferrable to this
        hack. Don't use this method if you don't have to. The
        idea of this one is that you can use multiple server names
        with numbers added, where the server names are known to
        be the WAN-visible names. So: if WAN visible hostname
        (i.e. the router/firewall's) hostname is datastage.stanford.edu,
        and multiple servers behind the router take turns serving,
        then one could call those servers datastage1.stanford.edu,
        datastage2.stanford.edu, etc. and this method would still always
        return datastage.stanford.edu. A hack.

        '''
        fullyQualDomainName = socket.getfqdn()
        return(re.sub(r'[0-9]', '', fullyQualDomainName))

    @classmethod
    def getCertAndKey(self):
        '''
        Return a 2-tuple with full paths, respectively to
        the SSL certificate, and private key.
        To find the SSL certificate location, we assume
        that it is stored in dir '.ssl' in the current
        user's home dir.
        We assume the cert file either ends in .cer, or
        in .crt, and that the key file ends in .key.
        The first matching files in the .ssl directory
        are grabbed.

        @return: two-tuple with full path to SSL certificate, and key files.
        @rtype: (str,str)
        @raise ValueError: if either of the files are not found.

        '''
        homeDir = os.path.expanduser("~")
        sslDir = '%s/.ssl/' % homeDir
        try:
            certFileName = next(fileName for fileName in os.listdir(sslDir)
	                               if fileName.endswith('.cer') or fileName.endswith('.crt'))
        except StopIteration:
            raise(ValueError("Could not find ssl certificate file in %s" % sslDir))

        try:
            privateKeyFileName = next(fileName for fileName in os.listdir(sslDir)
	                                     if fileName.endswith('.key'))
        except StopIteration:
            raise(ValueError("Could not find ssl private key file in %s" % sslDir))
        return (os.path.join(sslDir, certFileName),
                os.path.join(sslDir, privateKeyFileName))

class DataServer(threading.Thread):

    def __init__(self, requestDict, mainThread, testing=False):

        threading.Thread.__init__(self)

        self.mainThread = mainThread
        self.testing = testing


        if testing:
            self.currUser  = 'unittest'
            self.defaultDb = 'unittest'
        else:
            self.currUser = getpass.getuser()
            self.defaultD = 'Edx'

        self.ensureOpenMySQLDb()

        # Locate the makeCourseCSV.sh script:
        self.thisScriptDir = os.path.dirname(__file__)
        self.exportCSVScript = os.path.join(self.thisScriptDir, '../scripts/makeCourseCSVs.sh')
        self.courseInfoScript = os.path.join(self.thisScriptDir, '../scripts/searchCourseDisplayNames.sh')
        self.exportForumScript = os.path.join(self.thisScriptDir, '../scripts/makeForumCSV.sh')
        self.exportEmailListScript = os.path.join(self.thisScriptDir, '../scripts/makeEmailListCSV.sh')

        # A dict into which the various exporting methods
        # below will place instances of tempfile.NamedTemporaryFile().
        # Those are used as comm buffers between shell scripts
        # and this Python code:
        self.infoTmpFiles = {}
        self.dbError = 'no error'
        self.requestDict = requestDict

        self.currTimer = None

        # Make fullEmailTargetDir predictable:
        self.fullEmailTargetDir = None

    def ensureOpenMySQLDb(self):
        try:
            with open('/home/%s/.ssh/mysql' % self.currUser, 'r') as fd:
                self.mySQLPwd = fd.readline().strip()
                self.mysqlDb = MySQLDB(user=self.currUser, passwd=self.mySQLPwd, db=self.mainThread.defaultDb)
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

    def run(self):
        self.serveOneDataRequest(self.requestDict)

    def serveOneDataRequest(self, requestDict):
        # Get the request name:
        try:
            requestName = requestDict['req']
            args        = requestDict['args']

            if requestName == 'keepAlive':
                return

            # Caller wants list of course names?
            if requestName == 'reqCourseNames':
#                 #*********
#                 self.mainThread.logInfo('Sleep-and-loop')
#                 for i in range(10):
#                     time.sleep(1)
#                 self.mainThread.logInfo('Back to serving')
#                 #*********
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

            # Ensure that email start date is present if
            # email export is requested:
            emailStartDate = args.get('emailStartDate', None)
            if emailStartDate is None and requestName == 'emailList':
                self.writeErr('In on_message: start date was not included; could not export email list.')
                return

            # Conversely, if this request is not for emails,
            # then set the email start date to None, so that
            # we can use that start date value reliably further down:
            if requestName != 'emailList':
                emailStartDate = None

            # Check whether any of the requests
            # were previously issued, and output files
            # were therefore created. If so, then delete
            # those if allowed ('Remove any previous...'
            # box is checked in the UI). Else return error
            # to browser:
            try:
                xpungeExisting = self.str2bool(args.get("wipeExisting", False))
                self.checkForOldOutputFiles([action for action in args.keys() if args[action] == True],
                                           xpungeExisting,
                                           args['courseId'],
                                           emailStartDate)
            except ExistingOutFile as e:
                # At least one of the actions was called earlier.
                # That invocation produced an output file, and the
                # "Remove any previous exports of same type' option
                # was not checked.
                # When email or quarterly report are requested, then there is
                # no associated courseId. Keeping this in mind, find the pickup
                # URL of the existing data:
                if not courseIdWasPresent:
                    if self.fullEmailTargetDir is not None:
                        oldDeliveryUrl = self.getDeliveryURL(os.path.basename(self.fullEmailTargetDir))
                    else:
                        oldDeliveryUrl = self.getDeliveryURL(courseId)

                self.writeError("Request '%s': table(s) for %s %s already existed, and Remove Previous Exports... was not checked. Pickup at %s." %\
                                (e.actionRequest, 'course' if courseIdWasPresent else 'email', courseId if courseIdWasPresent else '', oldDeliveryUrl))
                return None

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

                if args.get('learnerPerf', False):
                    self.setTimer()
                    if courseList is not None:
                        for courseName in courseList:
                            args['courseId'] = courseName
                            self.exportLearnerPerf(args)
                    else:
                        self.exportLearnerPerf(args)

                if args.get('demographics', False):
                    self.setTimer()
                    if courseList is not None:
                        for courseName in courseList:
                            args['courseId'] = courseName
                            self.exportDemographics(args)
                    else:
                        self.exportDemographics(args)

                if args.get('qualtrics', False):
                    self.setTimer()
                    if courseList is not None:
                        for courseName in courseList:
                            args['courseId'] = courseName
                            self.exportQualtrics(args)
                    else:
                        self.exportQualtrics(args)

                if args.get('edxForumRelatable', False) or args.get('edxForumIsolated', False):
                    self.setTimer()
                    if courseList is not None:
                        for courseName in courseList:
                            args['courseId'] = courseName
                            self.exportForum(args)
                    else:
                        self.exportForum(args)

                if args.get('learnerPII', False):
                    self.setTimer()
                    if courseList is not None:
                        for courseName in courseList:
                            args['courseId'] = courseName
                            self.exportPIIDetails(args)
                    else:
                        self.exportPIIDetails(args)

                if args.get('emailList', False):
                    self.setTimer()
                    self.exportEmailList(args)

                if args.get('quarterRep', False):
                    self.setTimer()
                    self.exportQuarterlyReport(args)

                self.cancelTimer()
                endTime = datetime.datetime.now() - startTime

                deliveryUrl = self.printTableInfo()

                # Get a timedelta object with the microsecond
                # component subtracted to be 0, so that the
                # microseconds won't get printed:
                duration = endTime - datetime.timedelta(microseconds=endTime.microseconds)
                self.writeResult('progress', "<br>Runtime: %s<br>" % str(duration))

                # Add an example client letter,
                # unless export method wrote directly to
                # the browser, rather than writing to
                # a file:
                if deliveryUrl is not None:
                    self.addClientInstructions(args, deliveryUrl)

            else:
                self.writeError("Unknown request name: %s" % requestName)
        except Exception as e:
            # Stop sending progress indicators to browser:
            self.cancelTimer()
            #self.loglevel = CourseCSVServer.LOG_LEVEL_DEBUG
            if self.mainThread.loglevel == CourseCSVServer.LOG_LEVEL_NONE:
                return
            elif self.mainThread.loglevel == CourseCSVServer.LOG_LEVEL_INFO:
                self.mainThread.logErr('Error while processing req: %s' % `e`)
            elif self.mainThread.loglevel == CourseCSVServer.LOG_LEVEL_DEBUG:
                self.mainThread.logErr('Error while processing req: %s' % str(traceback.print_exc()))
            # Need to escape double-quotes so that the
            # browser-side JSON parser for this response
            # doesn't get confused:
            #safeResp = json.dumps('(%s): %s)' % (requestDict['req']+str(requestDict['args']), `e`))
            #self.writeError("Server could not extract request name/args from %s" % safeResp)
            self.writeError("%s" % `e`)
        finally:
            try:
                self.mysqlDb.close()
            except Exception as e:
                self.writeError("Error during MySQL driver close: '%s'" % `e`)

    def checkForOldOutputFiles(self, actions, mayDelete, courseDisplayName, emailStartDate):
        '''
        Given an action requested by the end user (e.g. 'basicData', 'engagementData', etc.)
        check whether any output files already exist for that type
        of request. If so, then the passed-in mayDelete controls
        whether the method may delete the found files. If not allowed
        to delete, raises IOError, else deletes the respective file(s)

        :param actions: list of output requests (e.g. 'basicData', 'engagementData', etc.)
        :type actions: String
        :param mayDelete: whether or not method may delete prior output files it finds.
        :type mayDelete: Boolean
        :param courseDisplayName: name of course for which data is being exported.
            None if no specific course is involved, as for email address export.
        :type courseDisplayName: {String | None}
        :param emailStartDate: start date for email export, or None if no email export.
        :type String
        :raises ExistingOutFile when at least one output file already existes, and mayDelete is False.
            The exception's actionRequest contains the action that had an outfile present.
        '''
        dirExisted = False
        if courseDisplayName is not None:
            (self.fullTargetDir, dirExisted) = self.constructCourseSpecificDeliveryDir(courseDisplayName)
        if dirExisted:
            for action in actions:
                if (action == 'basicData'):
                    existingFiles = glob.glob(os.path.join(self.fullTargetDir,'*ActivityGrade.csv')) +\
                                    glob.glob(os.path.join(self.fullTargetDir,'*VideoInteraction.csv')) +\
                                    glob.glob(os.path.join(self.fullTargetDir,'*EventXtract.csv'))
                    if len(existingFiles) > 0:
                        if mayDelete:
                            for fileName in existingFiles:
                                os.remove(fileName)
                        else:
                            raise(ExistingOutFile('File(s) for action %s already exist in %s' % (action, self.fullTargetDir), 'Basic course info'))
                if (action == 'engagementData'):
                    existingFiles = glob.glob(os.path.join(self.fullTargetDir,'*allData.csv')) +\
                                    glob.glob(os.path.join(self.fullTargetDir,'*summary.csv')) +\
                                    glob.glob(os.path.join(self.fullTargetDir,'*weeklyEffort.csv'))
                    if len(existingFiles) > 0:
                        if mayDelete:
                            for fileName in existingFiles:
                                os.remove(fileName)
                        else:
                            raise(ExistingOutFile('File(s) for action %s already exist in %s' % (action, self.fullTargetDir), 'Time on task'))

                if (action == 'demographics'):
                    existingFiles = glob.glob(os.path.join(self.fullTargetDir,'*demographics.csv'))
                    if len(existingFiles) > 0:
                        if mayDelete:
                            for fileName in existingFiles:
                                os.remove(fileName)
                        else:
                            raise(ExistingOutFile('File(s) for action %s already exist in %s' % (action, self.fullTargetDir), 'Demographics'))

                if (action == 'edxForumRelatable') or (action == 'edxForumIsolated'):
                    existingFiles = glob.glob(os.path.join(self.fullTargetDir,'*forum.csv.zip'))
                    if len(existingFiles) > 0:
                        if mayDelete:
                            for fileName in existingFiles:
                                os.remove(fileName)
                        else:
                            raise(ExistingOutFile('File(s) for action %s already exist in %s' % (action, self.fullTargetDir), 'Forum data'))

                if (action == 'learnerPII'):
                    existingFiles = glob.glob(os.path.join(self.fullTargetDir,'*piiData.*'))
                    if len(existingFiles) > 0:
                        if mayDelete:
                            for fileName in existingFiles:
                                os.remove(fileName)
                        else:
                            raise(ExistingOutFile('File(s) for action %s already exist in %s' % (action, self.fullTargetDir), 'Learner PII'))

        # If email export requested, an export start date
        # will have been provided:
        if emailStartDate is not None:
            (self.fullEmailTargetDir, dirExisted) = self.constructEmailListDeliveryDir(emailStartDate)
            existingFiles = glob.glob(os.path.join(self.fullEmailTargetDir,'Email*'))
            if len(existingFiles) > 0:
                if mayDelete:
                    for fileName in existingFiles:
                        os.remove(fileName)
                else:
                    raise(ExistingOutFile('File(s) for action %s already exist in %s' % ('getEmailAddresses', self.fullEmailTargetDir), 'Email addresses'))

        return True

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
            courseNamesAndEnrollments = self.queryCourseNameList(courseRegex, includeEnrollment=True)
            # Check whether __init__() method was unable to log into
            # the db:
            if courseNamesAndEnrollments is None:
                self.writeError('Server could not log into database: %s' % self.dbError)
                return
        except Exception as e:
            self.writeError(`e`)
            return
        self.writeResult('progress', '')
        self.writeResult('courseList', courseNamesAndEnrollments)

    def writeError(self, msg):
        '''
        Writes a response to the JS running in the browser
        that indicates an error. Result action is "error",
        and "args" is the error message string:

        :param msg: error message to send to browser
        :type msg: String
        '''
        self.mainThread.logDebug("Sending err to browser: %s" % msg)
        if not self.testing:
            errMsg = '{"resp" : "error", "args" : "%s"}' % msg.replace('"', "`")
            try:
                self.mainThread.write_message(errMsg)
            except IOError as e:
                self.mainThread.logErr('IOError while writing error to browser; msg attempted to write; "%s" (%s)' % (msg, `e`))

    def writeResult(self, responseName, args):
        '''
        Write a JSON formatted result back to the browser.
        Format will be::

        {"resp" : "<respName>", "args" : "<jsonizedArgs>"}

        That is, the args will be turned into JSON that is
        in the response's "args" value. The responseName parameter
        tells the browser what type of msg is coming back;
        currently 'progress', 'courseList', and 'printTblInfo'.

        :param responseName: name of result that is recognized by the JS in the browser
        :type responseName: String
        :param args: any Python datastructure that can be turned into JSON
        :type args: {int | String | [String] | ...}
        '''
        self.mainThread.logDebug("Prep to send result to browser: %s" % responseName + ':' +  str(args))
        # The decode() is applied for safety: Forum strings
        # are notorious for bad unicode, which would then lead
        # to a UnicodeDecodeError during the dumps:
        jsonArgs = json.dumps(args.decode('utf-8', 'ignore') if type(args) == str else args)
        msg = '{"resp" : "%s", "args" : %s}' % (responseName, jsonArgs)
        self.mainThread.logDebug("Sending result to browser: %s" % msg)
        if not self.testing:
            self.mainThread.write_message(msg)

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

        courseQuarter = detailDict.get('basicDataQuarter', None)
        courseAcademicYear = detailDict.get('basicDataAcademicYear', None)
        if (courseQuarter is None) or (courseAcademicYear is None) or \
            (courseQuarter == 'blank') or (courseAcademicYear == 'blank'):
            quarter = None
        else:
            quarter = "%s%s" % (courseQuarter,courseAcademicYear)

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

        if quarter is not None:
            scriptCmd.extend(['-q', quarter])

        #************
        self.mainThread.logDebug("Script cmd is: %s" % str(scriptCmd))
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
            self.mainThread.logErr('In exportTimeEngagement: course ID was not included; could not compute engagement tables.')
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
        # EngagementComputer will open its own MySQLDB instance:
        self.mysqlDb.close()
        # Are we only to consider video events?
        engageVideoOnly = detailDict.get('engageVideoOnly', False)
        try:
            engagementComp = EngagementComputer(coursesStartYearsArr=startYearsArr,
                                        mySQLUser=invokingUser,
                                        courseToProfile=courseId,
                                        videoOnly=(True if engageVideoOnly else False)
                                        )

            engagementComp.run()
            (summaryFile, detailFile, weeklyEffortFile) = engagementComp.writeResultsToDisk()
        finally:
            # Re-open MySQLDB instance for this instance:
            self.ensureOpenMySQLDb()

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
            self.mainThread.logErr('Could not write result sample lines: %s' % `e`)

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
            self.mainThread.logErr('In exportForum: course ID was not included; could not export forum data.')
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
        self.infoTmpFiles['exportForum'] = infoXchangeFile

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
            self.mainThread.logErr("Forum export needs to be encrypted, and therefore needs a crypto pwd to use.")
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
        self.mainThread.logDebug("Script cmd is: %s" % str(scriptCmd))
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

    def exportPIIDetails(self, detailDict):
        '''
        Get the courseID to get PII for. If that ID is None,
        then PII for all courses. The Web UI may allow only
        one course to profile at a time, but the capability
        to do all is here; just have on_message put None
        as the courseId:

        :param detailDict: dict of arguments; expected: 'courseId', cryptoPwd
        :type detailDict: {String : String}
        :return full path of outputfile
        :rtype String
        '''

        if self.mysqlDb is None:
            self.writeError('In exportPIIData: Database is disconnected; have to give up.')
            return

        try:
            courseId = detailDict['courseId']
        except KeyError:
            self.writeError('In exportPIIDetails: course ID was not included; could not construct PII table.')
            return

        if courseId is not None:
            courseNameNoSpaces = string.replace(string.replace(courseId,' ',''), '/', '_')
        else:
            courseNameNoSpaces = 'allCourses'

        # File name for eventual final result:
        outFilePIIName = os.path.join(self.fullTargetDir, '%s_piiData.csv' % courseNameNoSpaces)

        # Get tmp file name for MySQL to write its
        # result table to. Can't use built-in tempfile module,
        # b/c it creates a file, which then has MySQL
        # complain.
        # Create a random num sequence seeded with
        # this instance object:
        random.seed(self)
        tmpFileForPII =  '/tmp/classExportPIITmp' + str(time.time()) + str(random.randint(1,10000)) + '.csv'
        # Ensure the file doesn't exist (highly unlikely):
        try:
            os.remove(tmpFileForPII)
        except OSError:
            pass

        try:
            for courseName in self.queryCourseNameList(courseId):
                mySqlCmd = ' '.join([
                'SELECT EdxPrivate.idInt2Anon(Enrollment.user_int_id) AS anon_screen_name, ',
                '       Enrollment.user_int_id, ',
                '       auth_user.username AS screen_name, ',
                '       EdxPrivate.idInt2Forum(auth_user.id) AS forum_id, ',
                '       auth_user.email, ',
                '       edxprod.student_anonymoususerid.anonymous_user_id as external_lti_id, ',
                '       auth_user.date_joined, ',
                '       Enrollment.course_display_name  ',
                'INTO OUTFILE "%s"' % tmpFileForPII,
                'FIELDS TERMINATED BY "," OPTIONALLY ENCLOSED BY \'"\'',
                'LINES TERMINATED BY "\n"',
                'FROM edxprod.auth_user, ',
                '     edxprod.student_anonymoususerid,',
                '     ( SELECT user_id as user_int_id, ',
                '              EdxPrivate.idInt2Anon(user_id) as anon_screen_name, ',
                '          course_id AS course_display_name  ',
                '       FROM edxprod.student_courseenrollment  ',
                '       WHERE EdxPrivate.idInt2Anon(user_id) != "9c1185a5c5e9fc54612808977ee8f548b2258d31"  ',
                '       AND course_id="%s"' % courseName,
                '     ) AS Enrollment',
                'WHERE edxprod.student_anonymoususerid.user_id = Enrollment.user_int_id',
                '  AND edxprod.auth_user.id = Enrollment.user_int_id;'
                ])

            for piiResultLine in self.mysqlDb.query(mySqlCmd):
                tmpFileForPII.write(','.join(piiResultLine) + '\n')

            # Create the final output file, prepending the column
            # name header:
            with open(outFilePIIName, 'w') as fd:
                fd.write('anon_screen_name,user_int_id,screen_name,forum_id,email,external_lti_id,date_joined,course_display_name\n')
            self.catFiles(outFilePIIName, tmpFileForPII, mode='a')

        finally:
            try:
                os.remove(tmpFileForPII)
            except OSError:
                pass
        # Save information for printTableInfo() method to find:
        infoXchangeFile = tempfile.NamedTemporaryFile()
        self.infoTmpFiles['exportPIIDetails'] = infoXchangeFile

        infoXchangeFile.write(outFilePIIName + '\n')
        infoXchangeFile.write(str(self.getNumFileLines(outFilePIIName)) + '\n')

        # Add sample lines:
        infoXchangeFile.write('herrgottzemenschnochamal!\n')
        try:
            with open(outFilePIIName, 'r') as fd:
                head = []
                for lineNum,line in enumerate(fd):
                    head.append(line)
                    if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                        break
                infoXchangeFile.write(''.join(head))
            infoXchangeFile.write('herrgottzemenschnochamal!\n')
        except IOError as e:
            self.mainThread.logErr('Could not write result sample lines: %s' % `e`)

        # zip-encrypt the Zip file:
        cryptoPwd = detailDict.get("cryptoPwd", '')
        self.zipFiles(outFilePIIName + '.zip', cryptoPwd, [outFilePIIName])

        # Remove the un-encrypted original:
        try:
            os.remove(outFilePIIName)
        except OSError:
            pass

        return outFilePIIName + '.zip'

    def exportDemographics(self, detailDict):
        '''
        Exports demographic information for each learner in a given
        course. Places name of result file into self.mainThread.latestDemographicsFilename,
        so that unittests can find it. The output table will include
        the following:
            anon_screen_name, gender, year_of_birth, level_of_education, country_three_letters, country_name

        :param detailDict: dict of arguments; expected: 'courseId'
        :type detailDict: {String : String}
        :return full path of outputfile
        :rtype String
        '''

        # For unittests: None-out the self.mainThread.latestDemographicsFilename
        # so the test can wait for it to fill:
        self.mainThread.latestDemographicsFilename = None

        if self.mysqlDb is None:
            self.writeError('In exportDemographics: Database is disconnected; have to give up.')
            return

        try:
            courseId = detailDict['courseId']
        except KeyError:
            self.writeError('In exportDemographics: course ID was not included; could not construct lerner performance table.')
            return

        if courseId is not None:
            courseNameNoSpaces = string.replace(string.replace(courseId,' ',''), '/', '_')
        else:
            courseNameNoSpaces = 'allCourses'

        # File name for eventual final result:
        outFileDemographicsName = os.path.join(self.fullTargetDir, '%s_demographics.csv' % courseNameNoSpaces)

        #*************
        #print("outFileDemographicsName: '%s'" % outFileDemographicsName)
        #*************
        for courseName in self.queryCourseNameList(courseId):
            if self.testing:
                courseName   = 'testtest/MedStats/2013-2015'
                userGradeDb  = 'unittest'
                trueEnrollDb = 'unittest'
            else:
                userGradeDb  = 'EdxPrivate'
                trueEnrollDb = 'edxprod'
            mySqlCmd = ' '.join([
                            "SELECT 'anon_screen_name','gender','year_of_birth','level_of_education','country_three_letters','country_name' " +\
                            "UNION " +\
                            "SELECT Demographics.anon_screen_name," +\
                            "Demographics.gender," +\
                            "CAST(Demographics.year_of_birth AS CHAR) AS year_of_birth," +\
                            "Demographics.level_of_education," +\
                            "Demographics.country_three_letters," +\
                            "Demographics.country_name " +\
                            "INTO OUTFILE '" + outFileDemographicsName + "' FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' LINES TERMINATED BY '\n' "
                            "FROM (SELECT anon_screen_name" +\
                            "        FROM " + trueEnrollDb + ".true_courseenrollment LEFT JOIN " + userGradeDb + ".UserGrade" +\
                            "      ON user_int_id = user_id" +\
                            "        WHERE " + trueEnrollDb + ".true_courseenrollment.course_display_name = '" + courseName + "') AS Students " +\
                            "LEFT JOIN Demographics" +\
                            "  ON Demographics.anon_screen_name = Students.anon_screen_name;"                                                                       ])
            #***************
            #self.mainThread.logDebug("mySqlCmd: %s" % mySqlCmd)
            #with open('/home/dataman/Data/EdX/NonTransformLogs/exportClass.log', 'a') as errFd:
            #    errFd.write("mySqlCmd: '%s'\n" % str(mySqlCmd))
            #***************
            try:
                resIterator = self.mysqlDb.query(mySqlCmd)
                resIterator.next()
            except StopIteration:
                pass
            except Exception as e:
                raise
                #***************
                #with open('/home/dataman/Data/EdX/NonTransformLogs/exportClass.log', 'a') as errFd:
                #    exc_type, exc_value, exc_traceback = sys.exc_info() #@UnusedVariable
                #    traceback.print_tb(exc_traceback, file=errFd)
                #    errFd.write("********MySQL query failed: '%s'\n" % `e`)
                #***************
        # Save information for printTableInfo() method to find:
        infoXchangeFile = tempfile.NamedTemporaryFile()
        self.infoTmpFiles['exportDemographics'] = infoXchangeFile

        infoXchangeFile.write(outFileDemographicsName + '\n')
        infoXchangeFile.write(str(self.getNumFileLines(outFileDemographicsName)) + '\n')

        # Add sample lines:
        infoXchangeFile.write('herrgottzemenschnochamal!\n')
        try:
            with open(outFileDemographicsName, 'r') as fd:
                head = []
                for lineNum,line in enumerate(fd):
                    head.append(line)
                    if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                        break
                infoXchangeFile.write(''.join(head))
            infoXchangeFile.write('herrgottzemenschnochamal!\n')
        except IOError as e:
            self.mainThread.logErr('Could not write result sample lines: %s' % `e`)


        # Allow unit tests to find the result file:
        self.mainThread.latestDemographicsFilename = outFileDemographicsName

        return outFileDemographicsName

    def exportQualtrics(self, detailDict):
        '''
        Exports surveys and survey responses as three tables, Survey, Answer, and AnswerMeta.

        :param detailDict: dict of arguments; expected: 'courseId', 'wipeExisting'
        :type detailDict: {String : String, String : Boolean}
        '''
        courseId = detailDict.get('courseId', '')
        courseNameNoSpaces = string.replace(string.replace(courseId,' ',''), '/', '_')
        surveyOutfile = os.path.join(self.fullTargetDir, '%s_survey.csv' % courseNameNoSpaces)

        infoXchangeFile = tempfile.NamedTemporaryFile()
        self.infoTmpFiles['exportQualtrics'] = infoXchangeFile

        infoXchangeFile.write(surveyOutfile + '\n')
        infoXchangeFile.write(str(9001) + '\n')

        infoXchangeFile.write('herrgottzemenschnochamal!\n')
        for field in detailDict.keys():
            infoXchangeFile.write(field + ': ' + str(detailDict[field]) + '\n')
        infoXchangeFile.write('herrgottzemenschnochamal!\n')

        # # Set course ID and format for filenames
        # courseID = detailDict.get('courseId', '').strip()
        # courseNameNoSpaces = string.replace(string.replace(courseId,' ',''), '/', '_')
        #
        # # Get list of survey IDs
        # idgetter = "SELECT SurveyId FROM EdxQualtrics.SurveyInfo WHERE course_display_name = '%s'" % courseID
        # svIDs = self.mysqlDb.query(idgetter)
        #
        # # Define query template
        # dbQuery = Template( """
        #                     SELECT *
        #                     INTO OUTFILE {filename}
        #                     FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' LINES TERMINATED BY '\n'
        #                     FROM EdxQualtrics.{table}
        #                     WHERE SurveyId = '{svID}'
        #                     """ )
        #
        # # Export data for each survey ID (usually not more than 2 surveys)
        # for idx, surveyID in enumerate(svIDs):
        #     surveyOutfile = os.path.join(self.fullTargetDir, '%s_survey%d.csv' % (courseNameNoSpaces, idx+1))
        #     surveyQuery = dbQuery.substitute(filename=surveyOutfile, table="Survey", svID=surveyID)
        #     self.mysqlDb.query(surveyQuery).next()
        #
        #     answerOutfile = os.path.join(self.fullTargetDir, '%s_survey%d_answer.csv' % (courseNameNoSpaces, idx+1))
        #     answerQuery = dbQuery.substitute(filename=answerOutfile, table="Answer", svID=surveyID)
        #     self.mysqlDb.query(answerQuery).next()
        #
        #     answermetaOutfile = os.path.join(self.fullTargetDir, '%s_survey%d_answermeta.csv' % (courseNameNoSpaces, idx+1))
        #     answermetaQuery = dbQuery.substitute(filename=answermetaOutfile, table="AnswerMeta", svID=surveyID)
        #     self.mysqlDb.query(answermetaQuery).next()
        #
        # # Save information for printTableInfo() method to find:
        # infoXchangeFile = tempfile.NamedTemporaryFile()
        # self.infoTmpFiles['exportQualtrics'] = infoXchangeFile
        #
        # infoXchangeFile.write(surveyOutfile + '\n')
        # infoXchangeFile.write(str(self.getNumFileLines(surveyOutfile)) + '\n')
        #
        # infoXchangeFile.write(answerOutfile + '\n')
        # infoXchangeFile.write(str(self.getNumFileLines(answerOutfile)) + '\n')
        #
        # infoXchangeFile.write(answermetaOutfile + '\n')
        # infoXchangeFile.write(str(self.getNumFileLines(answermetaOutfile)) + '\n')
        #
        # # Add sample lines:
        # infoXchangeFile.write('herrgottzemenschnochamal!\n')
        # try:
        #     with open(surveyOutfile, 'r') as fd:
        #         head = []
        #         for lineNum,line in enumerate(fd):
        #             head.append(line)
        #             if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
        #                 break
        #         infoXchangeFile.write(''.join(head))
        #     infoXchangeFile.write('herrgottzemenschnochamal!\n')
        #
        #     with open(answerOutfile, 'r') as fd:
        #         head = []
        #         for lineNum,line in enumerate(fd):
        #             head.append(line)
        #             if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
        #                 break
        #         infoXchangeFile.write(''.join(head))
        #     infoXchangeFile.write('herrgottzemenschnochamal!\n')
        #
        #     with open(answermetaOutfile, 'r') as fd:
        #         head = []
        #         for lineNum,line in enumerate(fd):
        #             head.append(line)
        #             if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
        #                 break
        #         infoXchangeFile.write(''.join(head))
        #     infoXchangeFile.write('herrgottzemenschnochamal!\n')
        # except IOError as e:
        #     self.mainThread.logErr('Could not write result sample lines: %s' % `e`)


    def exportLearnerPerf(self, detailDict):
        #***** To be completed:
        if self.mysqlDb is None:
            self.writeError('In exportLearnerPerf: Database is disconnected; have to give up.')
            return

        try:
            courseId = detailDict['courseId']
        except KeyError:
            self.writeError('In exportLearnerPerf: course ID was not included; could not construct lerner performance table.')
            return

        if courseId is not None:
            courseNameNoSpaces = string.replace(string.replace(courseId,' ',''), '/', '_')
        else:
            courseNameNoSpaces = 'allCourses'

        # File name for eventual final result:
        outFileLearnerPerfName = os.path.join(self.fullTargetDir, '%s_learnerPerf.csv' % courseNameNoSpaces)

        # Get tmp file name for MySQL to write its
        # result table to. Can't use built-in tempfile module,
        # b/c it creates a file, which then has MySQL
        # complain.
        # Create a random num sequence seeded with
        # this instance object:
        random.seed(self)
        tmpFileForLearnerPerf =  '/tmp/classExportLearnerPerfTmp' + str(time.time()) + str(random.randint(1,10000)) + '.csv'
        # Ensure the file doesn't exist (highly unlikely):
        try:
            os.remove(tmpFileForLearnerPerf)
        except OSError:
            pass

        try:
            for courseName in self.queryCourseNameList(courseId):
                mySqlCmd = ' '.join([
                                     "SELECT  anon_screen_name," +\
                                              "COUNT(DISTINCT module_id) AS num_problems," +\
                                              "AVG(percent_grade) AS avg_problem_grade,"+\
                                              "AVG(num_attempts) AS avg_num_attempts" +\
                                     "FROM ActivityGrade " +\
                                     "WHERE num_attempts > -1 " +\
                                       "AND course_display_name = '" + courseName + "' " +\
                                     "GROUP BY anon_screen_name"
                                     ])
            for learnerPerfResultLine in self.mysqlDb.query(mySqlCmd):
                tmpFileForLearnerPerf.write(','.join(learnerPerfResultLine) + '\n')

            # Create the final output file, prepending the column
            # name header:
            with open(outFileLearnerPerfName, 'w') as fd:
                fd.write('anon_screen_name,num_problems,avg_program_grade,avg_num_attempts\n')
            self.catFiles(outFileLearnerPerfName, tmpFileForLearnerPerf, mode='a')

        finally:
            try:
                os.remove(tmpFileForLearnerPerf)
            except OSError:
                pass
        # Save information for printTableInfo() method to find:
        infoXchangeFile = tempfile.NamedTemporaryFile()
        self.infoTmpFiles['exportPIIDetails'] = infoXchangeFile

        infoXchangeFile.write(outFileLearnerPerfName + '\n')
        infoXchangeFile.write(str(self.getNumFileLines(outFileLearnerPerfName)) + '\n')

        # Add sample lines:
        infoXchangeFile.write('herrgottzemenschnochamal!\n')
        try:
            with open(outFileLearnerPerfName, 'r') as fd:
                head = []
                for lineNum,line in enumerate(fd):
                    head.append(line)
                    if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                        break
                infoXchangeFile.write(''.join(head))
            infoXchangeFile.write('herrgottzemenschnochamal!\n')
        except IOError as e:
            self.mainThread.logErr('Could not write result sample lines: %s' % `e`)

        # zip-encrypt the Zip file:
        cryptoPwd = detailDict.get("cryptoPwd", '')
        self.zipFiles(outFileLearnerPerfName + '.zip', cryptoPwd, [outFileLearnerPerfName])

        # Remove the un-encrypted original:
        try:
            os.remove(outFileLearnerPerfName)
        except OSError:
            pass

        return outFileLearnerPerfName + '.zip'


    def exportEmailList(self, detailDict):

        try:
            emailStartDate = detailDict['emailStartDate']
        except KeyError:
            self.mainThread.logErr('In exportEmailList: start date was not included; could not export email list.')
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
            self.mainThread.logErr("Email list export needs to be encrypted, and therefore needs a crypto pwd to use.")
            return;
        scriptCmd.extend(['--cryptoPwd', cryptoPwd])

        # If unittesting, tell the script:
        if self.testing:
            scriptCmd.extend(['--testing'])

        # The argument:
        scriptCmd.append(emailStartDate)

        #************
        self.mainThread.logDebug("Script cmd is: %s" % str(scriptCmd))
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

    def exportQuarterlyReport(self, detailDict):
        '''
        Places name of result file into self.mainThread.latestQuarterlyDemographicsFilename,
        so that unittests can find it.

        :param detailDict:
        :type detailDict:
        '''

        # For unittests: None-out the self.mainThread.latestDemographicsFilename
        # so the test can wait for it to fill:
        self.mainThread.latestQuarterlyDemographicsFilename = None

        try:
            # Get the *academic* year (not the calendar year;
            # JavaScript on other end is required to do any
            # conversions):
            quarter = detailDict['quarterRepQuarter']
        except KeyError:
            self.mainThread.logErr('In exportQuarterlyReport: quarter was not included; could not export quarterly report.')
            return
        try:
            academic_year = detailDict['quarterRepYear']
        except KeyError:
            self.mainThread.logErr('In exportQuarterlyReport: academic year was not included; could not export quarterly reqport.')
            return
        if academic_year == '%' or quarter == '%':
            self.mainThread.logErr('In exportQuarterlyReport: wildcards in quarter and academic year not yet supported.')
            return

        doEnrollment   = detailDict.get('quarterRepEnroll', False)
        doEngagement   = detailDict.get('quarterRepEngage', False)
        doDemographics = detailDict.get('quarterRepDemographics', False)

        mayOverwrite = detailDict.get('wipeExisting', False)
        enrollmentFileName = 'enrollment_%s%s.csv' % (quarter, self.getCalendarYear(quarter, academic_year))
        engagementFileName = 'engagement_%s%s.csv' % (quarter, self.getCalendarYear(quarter, academic_year))
        demographicsFileName = 'demographics_%s%s.csv' % (quarter, self.getCalendarYear(quarter, academic_year))

        # Create a Web accessible delivery directory early to check
        # whether target overwrite warning must be issued:
        pickupDirNameRoot = 'QuarterlyRep_%s%s' % (quarter,self.getCalendarYear(quarter, academic_year))
        (pickupDir, existed) = self.constructCourseSpecificDeliveryDir(pickupDirNameRoot) #@UnusedVariable
        pickupEnrollmentPath = os.path.join(pickupDir, enrollmentFileName)
        pickupEngagementPath = os.path.join(pickupDir, engagementFileName)
        pickupDemographicsPath = os.path.join(pickupDir, demographicsFileName)

        if doEnrollment and os.path.exists(pickupEnrollmentPath) and not mayOverwrite:
            # Did enrollment file exist (or maybe just engagement):
            self.writeError("Quarterly report enrollment result for %s%s already existed, and Remove Previous Exports... was not checked." %\
                            (quarter, self.getCalendarYear(quarter,academic_year)))
            return

        if doEngagement and os.path.exists(pickupEngagementPath) and not mayOverwrite:
            self.writeError("Quarterly report engagement result for %s%s already existed, and Remove Previous Exports... was not checked." %\
                            (quarter, self.getCalendarYear(quarter,academic_year)))
            return

        if doDemographics and os.path.exists(pickupDemographicsPath) and not mayOverwrite:
            self.writeError("Quarterly report demographics result for %s%s already existed, and Remove Previous Exports... was not checked." %\
                            (quarter, self.getCalendarYear(quarter,academic_year)))
            return

        # Create a file that printTableInfo can understand:
        infoXchangeFile = tempfile.NamedTemporaryFile(delete=True)
        self.infoTmpFiles['QuarterlyReport'] = infoXchangeFile

        exporter = QuarterlyReportExporter(mySQLUser=self.currUser,mySQLPwd=self.mySQLPwd, parent=self, testing=self.testing)

        minEnrollment = detailDict.get('quarterRepMinEnroll', None) # Use default in createQuarterlyReport.sh
        byActivity   = detailDict.get('quarterRepByActivity', None)

        if doEnrollment:
            self.writeResult('progress', "Start enrollment computations...")
            resFileNameEnroll = exporter.enrollment(academic_year, quarter, printResultFilePath=False, minEnrollment=minEnrollment, byActivity=byActivity)
            if resFileNameEnroll is None:
                self.writeError('Call to quarterly exporter for enrollment failed. See error log.')
                return
            self.writeResult('progress', "Finished enrollment computations.<br>")
            shutil.copyfile(resFileNameEnroll, pickupEnrollmentPath)
            # Note the file name and size in the print table info:
            infoXchangeFile.write(pickupEnrollmentPath + '\n')
            infoXchangeFile.write(str(self.getNumFileLines(pickupEnrollmentPath)) + '\n')

        if doEngagement:
            self.writeResult('progress', "Start engagement computations...")
            resFileNameEngage = exporter.engagement(academic_year, quarter, printResultFilePath=False)
            self.writeResult('progress', "Finished engagement computations.<br>")
            shutil.copyfile(resFileNameEngage, pickupEngagementPath)
            infoXchangeFile.write(pickupEngagementPath + '\n')
            infoXchangeFile.write(str(self.getNumFileLines(pickupEngagementPath)) + '\n')

        if doDemographics:
            self.writeResult('progress', "Start demographics computations...")
            resFileNameDemographics = exporter.demographics(academic_year, quarter, byActivity, printResultFilePath=False)
            self.writeResult('progress', "Finished demographics computations.<br>")
            shutil.copyfile(resFileNameDemographics, pickupDemographicsPath)
            infoXchangeFile.write(pickupDemographicsPath + '\n')
            infoXchangeFile.write(str(self.getNumFileLines(pickupDemographicsPath)) + '\n')
            # Put the CSV result name (resFileName) where
            # unittests can find it:
            self.mainThread.latestQuarterlyDemographicsFilename = resFileNameDemographics



        # Write up to five lines into the print table file,
        # with the special line separator between the filename/filesize
        # info written above, and the samples, and between each
        # sample:
        infoXchangeFile.write('herrgottzemenschnochamal!\n')
        if doEnrollment:
            try:
                with open(pickupEnrollmentPath, 'r') as fd:
                    head = []
                    for lineNum,line in enumerate(fd):
                        head.append(line)
                        if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                            break
                    infoXchangeFile.write(''.join(head))
            except IOError as e:
                self.mainThread.logErr('Could not write result sample lines: %s' % `e`)
            infoXchangeFile.write('herrgottzemenschnochamal!\n')
        if doEngagement:
            try:
                with open(pickupEngagementPath, 'r') as fd:
                    head = []
                    for lineNum,line in enumerate(fd):
                        head.append(line)
                        if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                            break
                    infoXchangeFile.write(''.join(head))
            except IOError as e:
                self.mainThread.logErr('Could not write result sample lines: %s' % `e`)
            infoXchangeFile.write('herrgottzemenschnochamal!\n')

        if doDemographics:
            try:
                with open(pickupDemographicsPath, 'r') as fd:
                    head = []
                    for lineNum,line in enumerate(fd):
                        head.append(line)
                        if lineNum >= CourseCSVServer.NUM_OF_TABLE_SAMPLE_LINES:
                            break
                    infoXchangeFile.write(''.join(head))
            except IOError as e:
                self.mainThread.logErr('Could not write result sample lines: %s' % `e`)
            infoXchangeFile.write('herrgottzemenschnochamal!\n')
            # Save the demographics result file in
            # self.mainThread.latestDemographicsFilename for
            # unittest to check:
            self.mainThread.latestDemographicsFilename = pickupDemographicsPath


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

    def getCalendarYear(self, quarter, academicYear):
        '''
        Given quarter and academic year, return the calendar
        year in which the quarter ran.

        :param quarter: quarter in which some course ran
        :type quarter: string: {fall|winter|spring|summer}; case insensitive
        :param academicYear: academic year in which course ran
        :type academicYear: int
        :return: calendar year in which course ran
        :rtype: int
        '''

        if quarter.lower() == 'fall':
            return academicYear
        else:
            return academicYear + 1

    def getAcademicYear(self, quarter, calendarYear):
        '''
        Given quarter and calendar year, return the academic
        year in which the quarter ran.

        :param quarter: quarter in which some course ran
        :type quarter: string: {fall|winter|spring|summer}; case insensitive
        :param calendarYear: calendar year in which course ran
        :type calendarYear: int
        :return: academic year in which course ran
        :rtype: int
        '''

        if quarter.lower() == 'fall':
            return calendarYear
        else:
            return calendarYear - 1

    def constructCourseSpecificDeliveryDir(self, courseName):
        '''
        Given a course name, construct a directory name where result
        files for that course will be stored to be visible on the
        Web. The parent dir is expected in CourseCSVServer.DELIVERY_HOME.
        The leaf dir is constructed as DELIVERY_HOME/courseName

        :param courseName: course name for which results will be deposited in the dir
        :type courseName: String
        :return: Two-tuple: the directory path, and flag PreExisted.EXISTED if
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
            # Default Unix tends to be no WRITE for
            # OTHER. But for MySQL to write its file,
            # the target dir must be write-open:
            os.chmod(self.fullTargetDir, 0o777)
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
        self.fullEmailTargetDir = os.path.join(CourseCSVServer.DELIVERY_HOME, 'Email_' + emailListStartDate)
        if os.path.isdir(self.fullEmailTargetDir):
            return (self.fullEmailTargetDir, PreExisted.EXISTED)
        else:
            os.makedirs(self.fullEmailTargetDir)
            return (self.fullEmailTargetDir, PreExisted.DID_NOT_EXIST)


    def printTableInfo(self):
        '''
        Writes html to browser that shows result table
        file names and sizes. Also sends a few lines
        from each table as samples.

        The information is in dict self.infoTmpFiles.
        Each exporting method above has its own entry
        in the dict: exportClass, exportForum, exportEngagement,
        exportPIIDetails, etc. Each value is the name of an
        open tmp file that contains alternating: file name,
        file size in lines for as many tables as were output.

        After that information come batches of up to NUM_OF_TABLE_SAMPLE_LINES
        sample lines for each table. The batches are separated
        by the token "herrgottzemenschnochamal!"

        :return: full path of the last table file that was deposited in the Web pickup area.
             This info is used later to construct a pickup URL
        :rtype: String

        '''

        if len(self.infoTmpFiles) == 0:
            # Export methods wrote directly to
            # browser:
            return None
        for exportFileKey in self.infoTmpFiles.keys():
            try:
                tmpFileFd = self.infoTmpFiles.get(exportFileKey)
                # Ensure we are at start of the tmp file:
                if tmpFileFd.closed:
                    continue
                tmpFileFd.seek(0)
                eof = False
                tableInfoDict = OrderedDict()
                # Pull all file name/numlines out of the info file:
                while not eof:
                    tableFileName     = tmpFileFd.readline().strip()
                    if len(tableFileName)  == 0 or tableFileName == 'herrgottzemenschnochamal!':
                        eof = True
                        continue
                    tableFileNumLines = tmpFileFd.readline().strip()
                    tableInfoDict[tableFileName.strip()] = tableFileNumLines
                # Now get all the line samples in the right order:
                sampleLineBatches = []
                if tableFileName == 'herrgottzemenschnochamal!':
                    endOfSampleBatch = False
                    eof = False
                    while not eof:
                        sample = ""
                        while not endOfSampleBatch:
                            try:
                                sampleLine = tmpFileFd.readline().strip()
                            except Exception as e:
                                print("Got it: %s" % `e`) #****************

                            if len(sampleLine) == 0:
                                eof = True
                                endOfSampleBatch = True
                                continue
                            if sampleLine == 'herrgottzemenschnochamal!':
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
                    elif tableFileName.find('piiData') > -1:
                        tblName = 'PIIMappings'
                    elif tableFileName.find('enrollment') > -1:
                        tblName = 'Enrollment'
                    elif tableFileName.find('engagement') > -1:
                        tblName = 'Engagement'
                    elif tableFileName.find('demographics') > -1:
                        tblName = 'Demographics'
                    elif tableFileName.find('QuarterlyReport') > -1:
                        tblName = 'Quarterly'
                    elif tableFileName.find('qualtrics') > -1:
                        tblName = 'Surveys'
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
                try:
                    tmpFileFd.close()
                except:
                    pass

        # Get the last part of the directory, where the tables are available
        # (i.e. the 'CourseSubdir' in:
        # /home/dataman/Data/CustomExcerpts/CourseSubdir/<tables>.csv:)
        tableDir = os.path.basename(os.path.dirname(tableFileName))
        url = "https://%s/researcher/%s/" % (self.mainThread.FQDN, tableDir)

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

    def queryCourseNameList(self, courseID, includeEnrollment=False):
        '''
        Given a MySQL regexp courseID string, return a list
        of matchine course_display_name in the db. If self.mysql
        is None, indicating that the __init__() method was unable
        to log into the db, then return None.

        :param courseID: Course name regular expression in MySQL syntax.
        :type courseID: String
        :param includeEnrollment: each result course name will be followed by its enrollment
            as per the true_courseenrollment table.
        :type includeEnrollment: boolean
        :return: An array of matching course_display_name, which may
                 be empty. None if _init__() was unable to log into db.
                 If includeEnrollment is True, append enrollment to each course name.
        :rtype: {[String] | None}
        '''
        courseNames = []
        # The --silent suppresses a column header line
        # from being displayed ('course_display_name' and 'enrollment'):
        mySqlCmd = [self.courseInfoScript,'-u',self.currUser, '--silent']
        if self.mySQLPwd is not None:
            mySqlCmd.extend(['-w',self.mySQLPwd])
        mySqlCmd.extend([courseID])
        self.mainThread.logDebug("About to query for course names on regexp: '%s'" % mySqlCmd)

        try:
            pipeFromMySQL = subprocess.Popen(mySqlCmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT).stdout
        except Exception as e:
            self.writeError('Error while searching for course names: %s' % `e`)
            return courseNames
        for courseName in pipeFromMySQL:
            courseName = courseName.strip()
            if not includeEnrollment:
                # This got us 'myCourse 10', i.e. course name
                # plus enrollment:
                try:
                    # The ...match(...) returns a match object,
                    # of which we select the 0th capture group.
                    # That group is a tuple: ('myCourseName',),
                    # so therefore the [0]:
                    courseName = CourseCSVServer.COURSE_NAME_SEP_PATTERN.match(courseName).groups(0)[0]
                except:
                    pass
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

        # Only log heartbeat sending every so often:
        if self.mainThread.latestHeartbeatLogTime is None:
            self.mainThread.latestHeartbeatLogTime = time.time()
        elif time.time() - self.mainThread.latestHeartbeatLogTime > CourseCSVServer.PROGRESS_LOGGING_INTERVAL:
            numHeartbeatsSent = int(CourseCSVServer.PROGRESS_LOGGING_INTERVAL / CourseCSVServer.PROGRESS_INTERVAL)
            self.mainThread.logDebug('Sent %d heartbeats.' % numHeartbeatsSent)
            self.mainThread.latestHeartbeatLogTime = time.time()

        msg = {"resp" : "progress", "args" : "."}
        if not self.testing:
            self.mainThread.write_message(msg)
        self.setTimer(CourseCSVServer.PROGRESS_INTERVAL)

    def getDeliveryURL(self, courseIdOrCustomExportFileName):
        '''
        Given a course ID string, return a URL from which
        completed course tables can be picked up:

        :param courseIdOrCustomExportFileName: course identifier, e.g.: /networking/EE120/Fall,
            or an existing file name in ~dataman/Data/CustomExports, such
            as 'Email_2015-03-01'
        :type courseIdOrCustomExportFileName: String
        :return: URL at which tables computed for a class are visible.

        :rtype: String
        '''
        # Replace slashes in class by underscores, so that the
        # course ID can be used as part of a directory name:
        courseIdAsDirName = courseIdOrCustomExportFileName.strip('/').replace('/','_')
        # Add timestamp and current number of microseconds as a UID
#         dateFormat = '%d-%b-%y_%I-%M_%f'
#         today = datetime.datetime.today().strftime(dateFormat)
#         url = "https://%s/researcher/%s_%s" % (self.mainThread.FQDN, courseIdAsDirName, today)
        url = "https://%s/researcher/%s/" % (self.mainThread.FQDN, courseIdAsDirName)
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
            #self.mainThread.logDebug('Cancelling progress timer')

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

    def catFiles(self, destFileName, *srcFileNames, **mode):
        '''
        Simulates Unix cat command. Given a destination
        file and any number of source files, concatenate the
        source files to the destination file. If mode keyword
        is given, it must be one of the Unix file operation
        modes 'w' for 'overwrite existing dest file, or or 'a'
        for 'append to' existing dest file. Default is 'w'

        :param destFileName:
        :type destFileName:
        '''
        mode = mode.get('mode', None)
        if mode is None:
            mode = 'w'
        try:
            with open(destFileName, mode) as outFd:
                for srcFileName in srcFileNames:
                    with open(srcFileName, 'r') as inFd:
                        shutil.copyfileobj(inFd, outFd)
        except (IOError, OSError) as e:
            raise IOError('Error trying to copy files %s to destination file %s: %s' % (srcFileNames, destFileName, `e`))

    # -------------------------------------------  Testing  ------------------

    def echoParms(self):
        for parmName in self.parms.keys():
            print("Parm: '%s': '%s'" % (self.parms.getvalue(parmName, '')))


if __name__ == '__main__':

    application = tornado.web.Application([(r"/exportClass", CourseCSVServer),])
    #application.listen(8080)

    (certFile,keyFile) = CourseCSVServer.getCertAndKey()
    sslArgsDict = {'certfile' : certFile,
                   'keyfile'  : keyFile}

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
