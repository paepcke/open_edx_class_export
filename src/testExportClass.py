'''
Created on Mar 26, 2014

@author: paepcke
'''

import tornado;
from tornado.ioloop import IOLoop;
from tornado.websocket import WebSocketHandler;
from tornado.httpserver import HTTPServer;

from collections import OrderedDict
import os
import time
import unittest

from pymysql_utils.pymysql_utils import MySQLDB

from exportClass import CourseCSVServer


class ExportClassTest(unittest.TestCase):

    oneStudentTestData = [
    ('CME/MedStats/2013-2015','abc','page_close','2013-08-30 03:27:00',0),  # start session
    ('CME/MedStats/2013-2015','abc','load_video','2013-08-30 03:27:20',1),  # 20sec 
    ('CME/MedStats/2013-2015','abc','seq_goto','2013-08-30 03:37:00',0),    # 10min
    ('CME/MedStats/2013-2015','abc','page_close','2013-08-30 04:10:00',0),   # end session ==> 20sec + 10min + 5min (for last non-video act)
    ('CME/MedStats/2013-2015','abc','load_video','2013-09-14 03:27:24',1),  # start/end session ==> 5min for action at 4:10
    ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 03:27:25',0),    # start session ==> 15min for action at 3:27 
    ('CME/MedStats/2013-2015','abc','page_close','2013-09-15 03:30:35',0),  # 3min 
    ('CME/MedStats/2013-2015','abc','load_video','2013-09-15 03:59:00',1),  # 29min 
    ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 04:05:00',0),    #  6min 
    ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 04:36:54',1)     # end session ==> 15 min for the final video session 
    ]                                                                     # total: 1hr:8min:20sec
    
    
    courseRuntimesData = [
                          ('CME/MedStats/2013-2015', '2013-07-30 03:27:00', '2013-10-30 03:27:00')
                          ]
    
    
    def setUp(self):
        application = None
        request = None #HTTPRequest.HTTPRequest()
        self.courseServer = CourseCSVServer(application, request, testing=True)
        try:
            self.mysqldb = MySQLDB(host='localhost', port=3306, user='unittest', db='unittest')
        except ValueError as e:
            self.fail(str(e) + " (For unit testing, localhost MySQL server must have user 'unittest' without password, and a database called 'unittest')")

    def tearDown(self):
        try:
            self.mysqldb.dropTable('unittest.Activities')
            self.mysqldb.close()
        except:
            pass

    def testOnMessage(self):
        self.buildSupportTables()
        jsonMsg = '{"req" : "getData", "args" : {"courseId" : "CME/MedStats/2013-2015", "wipeExisting" : "False", "inclPII" : "False", "cryptoPwd" : "foobar"}}'
        self.courseServer.on_message(jsonMsg)
        with open(self.courseServer.latestResultSummaryFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            #print(courseSummaryLine)
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,5,92,2,0,0\n', fd.readline())
            
        with open(self.courseServer.latestResultDetailFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-08-30,03:27:00,15\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-08-30,04:10:00,5\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-14,03:27:24,15\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-15,03:27:25,42\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-15,04:36:54,15\n', fd.readline())

        with open(self.courseServer.latestResultWeeklyEffortFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            #print(courseWeeklyLine)
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,4,20\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,6,72\n', fd.readline())

        os.remove(self.courseServer.latestResultSummaryFilename)
        os.remove(self.courseServer.latestResultDetailFilename)
        os.remove(self.courseServer.latestResultWeeklyEffortFilename)

    def buildSupportTables(self):
        # Activities table:
        schema = OrderedDict([('course_display_name','varchar(255)'),
                              ('anon_screen_name', 'varchar(40)'),
                              ('event_type', 'varchar(120)'),
                              ('time', 'datetime'),
                              ('isVideo', 'TINYINT')
                              ])
        self.mysqldb.dropTable('unittest.Activities')
        self.mysqldb.createTable('unittest.Activities', schema)
        colNames = ['course_display_name','anon_screen_name','event_type','time','isVideo']
        colValues = ExportClassTest.oneStudentTestData
        self.mysqldb.bulkInsert('Activities', colNames, colValues)

        # Course runtimes:
        schema = OrderedDict([('course_display_name','varchar(255)'),
                              ('course_start_date', 'datetime'),
                              ('course_end_date', 'datetime')
                              ])
        self.mysqldb.dropTable('unittest.CourseRuntimes')
        self.mysqldb.createTable('unittest.CourseRuntimes', schema)
        colNames = ['course_display_name','course_start_date','course_end_date']
        colValues = ExportClassTest.courseRuntimesData
        self.mysqldb.bulkInsert('CourseRuntimes', colNames, colValues)
        
        
if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testOnMessage']
    unittest.main()