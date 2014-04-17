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
import tempfile
import time
import unittest
import zipfile

from pymysql_utils.pymysql_utils import MySQLDB

from exportClass import CourseCSVServer


class TestSet:
    ONE_STUDENT_ONE_CLASS = 0
    TWO_STUDENTS_ONE_CLASS = 1
    TWO_STUDENTS_TWO_CLASSES = 2
    
class ExportClassTest(unittest.TestCase):

    # Test data for one student in one class. Student is active in 2 of the
    # class' weeks:
    #
    # Week 4:
    # Session1: 15    total each week: Week4: 20
    # Session2:  5      	         Week6: 72
    # 
    # Week 6:
    # Session3: 15 
    # Session4: 42         
    # Session5: 15
    # ------------
    #           92
    # 
    # Sessions in weeks:
    # week4: [20]        ==> median = 20
    # week6: [15,42,15]  ==> median = 15
    # 
    # The engagement summary file for one class:
    # totalStudentSessions, totalEffortAllStudents, oneToTwentyMin, twentyoneToSixtyMin, greaterSixtyMin
    #         5	                     92			        2                 0                  0
    #
    # The all_data detail file resulting from the data:
    # Platform,Course,Student,Date,Time,SessionLength
    #    'OpenEdX,CME/MedStats/2013-2015,abc,2013-08-30,03:27:00,15
    #    'OpenEdX,CME/MedStats/2013-2015,abc,2013-08-30,04:10:00,5
    #    'OpenEdX,CME/MedStats/2013-2015,abc,2013-09-14,03:27:24,15
    #    'OpenEdX,CME/MedStats/2013-2015,abc,2013-09-15,03:27:25,42
    #    'OpenEdX,CME/MedStats/2013-2015,abc,2013-09-15,04:36:54,15    
    #
    # The weekly effort file from the data:
    # platform,course,student,week,effortMinutes
    #    'OpenEdX,CME/MedStats/2013-2015,abc,4,20
    #    'OpenEdX,CME/MedStats/2013-2015,abc,6,72
    
    oneStudentTestData = [
      ('CME/MedStats/2013-2015','abc','page_close','2013-08-30 03:27:00',0),  # week 4; start session 
      ('CME/MedStats/2013-2015','abc','load_video','2013-08-30 03:27:20',1),  # 20sec (gets rounded to 0min)
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-08-30 03:37:00',0),    # 9min:40sec (gets rounded to 10min)
                                                                              # 0min + 10min + 5min = 15min
    
      ('CME/MedStats/2013-2015','abc','page_close','2013-08-30 04:10:00',0),  # 5min
    
      ('CME/MedStats/2013-2015','abc','load_video','2013-09-14 03:27:24',1),  # week 6; 15min (for the single video)
    
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 03:27:25',0), 
      ('CME/MedStats/2013-2015','abc','page_close','2013-09-15 03:30:35',0),  # 3min 
      ('CME/MedStats/2013-2015','abc','load_video','2013-09-15 03:59:00',1),  # 28min 
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 04:05:00',0),    #  6min 
      							 		                                    # 3min + 28min + 6min + 5min = 42
    
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 04:36:54',1)     # 15
    ] 
    
    
    courseRuntimesData = [
                          ('CME/MedStats/2013-2015', '2013-07-30 03:27:00', '2013-10-30 03:27:00'),
                          ('My/RealCourse/2013-2015', '2013-09-01 03:27:00', '2013-10-30 03:27:00')
                          ]
    
    twoStudentsOneClassTestData = [
      ('CME/MedStats/2013-2015','abc','page_close','2013-08-30 03:27:00',0),   
      ('CME/MedStats/2013-2015','abc','load_video','2013-08-30 03:27:20',1),
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-08-30 03:37:00',0),
                                                                          
    
      ('CME/MedStats/2013-2015','abc','page_close','2013-08-30 04:10:00',0),  
      ('CME/MedStats/2013-2015','def','page_close','2013-08-30 04:10:00',1),  # Second student  
    
      ('CME/MedStats/2013-2015','abc','load_video','2013-09-14 03:27:24',1),
    
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 03:27:25',0), 
      ('CME/MedStats/2013-2015','abc','page_close','2013-09-15 03:30:35',0),  
      ('CME/MedStats/2013-2015','abc','load_video','2013-09-15 03:59:00',1),  
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 04:05:00',0),    
      ('CME/MedStats/2013-2015','def','page_close','2013-09-16 04:10:00',1),  # Second student  
      	  						 		  
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 04:36:54',1)     
    ] 
    
    twoStudentsTwoClassesTestData = [
      ('CME/MedStats/2013-2015','abc','page_close','2013-08-30 03:27:00',0),   
      ('CME/MedStats/2013-2015','abc','load_video','2013-08-30 03:27:20',1),
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-08-30 03:37:00',0),
                                                                          
    
      ('CME/MedStats/2013-2015','abc','page_close','2013-08-30 04:10:00',0),  
      ('My/RealCourse/2013-2015','def','page_close','2013-09-01 04:10:00',1),  # Second student  
    
      ('CME/MedStats/2013-2015','abc','load_video','2013-09-14 03:27:24',1),
    
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 03:27:25',0), 
      ('CME/MedStats/2013-2015','abc','page_close','2013-09-15 03:30:35',0),  
      ('CME/MedStats/2013-2015','abc','load_video','2013-09-15 03:59:00',1),  
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 04:05:00',0),    
      ('My/RealCourse/2013-2015','def','page_close','2013-09-16 04:10:00',1),  # Second student  
      	  						 		  
      ('CME/MedStats/2013-2015','abc','seq_goto','2013-09-15 04:36:54',1)     
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

    def testOneStudentOneClass(self):
        self.buildSupportTables(TestSet.ONE_STUDENT_ONE_CLASS)
        jsonMsg = '{"req" : "getData", "args" : {"courseId" : "CME/MedStats/2013-2015", "engagementData" : "True", "wipeExisting" : "True", "inclPII" : "False", "cryptoPwd" : "foobar"}}'
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
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,5,20\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,7,72\n', fd.readline())

        os.remove(self.courseServer.latestResultSummaryFilename)
        os.remove(self.courseServer.latestResultDetailFilename)
        os.remove(self.courseServer.latestResultWeeklyEffortFilename)

    def testTwoStudentsOneClass(self):
        self.buildSupportTables(TestSet.TWO_STUDENTS_ONE_CLASS)
        jsonMsg = '{"req" : "getData", "args" : {"courseId" : "CME/MedStats/2013-2015", "engagementData" : "True", "wipeExisting" : "True", "inclPII" : "False", "cryptoPwd" : "foobar"}}'
        self.courseServer.on_message(jsonMsg)
        with open(self.courseServer.latestResultSummaryFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            #print(courseSummaryLine)
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,7,122,4,0,0\n', fd.readline())
            
        with open(self.courseServer.latestResultDetailFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-08-30,03:27:00,15\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-08-30,04:10:00,5\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-14,03:27:24,15\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-15,03:27:25,42\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-15,04:36:54,15\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,def,2013-08-30,04:10:00,15\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,def,2013-09-16,04:10:00,15\n', fd.readline())

        with open(self.courseServer.latestResultWeeklyEffortFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            #print(courseWeeklyLine)
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,5,20\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,7,72\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,def,5,15\n', fd.readline())
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,def,7,15\n', fd.readline())


        os.remove(self.courseServer.latestResultSummaryFilename)
        os.remove(self.courseServer.latestResultDetailFilename)
        os.remove(self.courseServer.latestResultWeeklyEffortFilename)

    def testTwoStudentsTwoClasses(self):
        self.buildSupportTables(TestSet.TWO_STUDENTS_TWO_CLASSES)
        jsonMsg = '{"req" : "getData", "args" : {"courseId" : "None", "engagementData" : "True", "wipeExisting" : "True", "inclPII" : "False", "cryptoPwd" : "foobar"}}'
        self.courseServer.on_message(jsonMsg)
        with open(self.courseServer.latestResultSummaryFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            #print(courseSummaryLine)
            # Read the rest of the summary lines, and
            # sort them just to ensure that we compare each
            # line to its ground truth:
            allSummaryLines = fd.readlines()
            allSummaryLines.sort()
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,5,92,2,0,0\n', allSummaryLines[0])
            self.assertEqual('OpenEdX,My/RealCourse/2013-2015,2,30,2,0,0\n', allSummaryLines[1])
     
        with open(self.courseServer.latestResultDetailFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            allDetailLines = fd.readlines()
            allDetailLines.sort()
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-08-30,03:27:00,15\n', allDetailLines[0])
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-08-30,04:10:00,5\n', allDetailLines[1])
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-14,03:27:24,15\n', allDetailLines[2])
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-15,03:27:25,42\n', allDetailLines[3])
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,2013-09-15,04:36:54,15\n', allDetailLines[4])
            self.assertEqual('OpenEdX,My/RealCourse/2013-2015,def,2013-09-01,04:10:00,15\n', allDetailLines[5])
            self.assertEqual('OpenEdX,My/RealCourse/2013-2015,def,2013-09-16,04:10:00,15\n', allDetailLines[6])
 
        with open(self.courseServer.latestResultWeeklyEffortFilename, 'r') as fd:
            # Read and discard the csv file's header line:
            fd.readline()
            #print(courseWeeklyLine)
            allWeeklyLines = fd.readlines()
            allWeeklyLines.sort()
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,5,20\n', allWeeklyLines[0])
            self.assertEqual('OpenEdX,CME/MedStats/2013-2015,abc,7,72\n', allWeeklyLines[1])
            self.assertEqual('OpenEdX,My/RealCourse/2013-2015,def,1,15\n', allWeeklyLines[2])
            self.assertEqual('OpenEdX,My/RealCourse/2013-2015,def,3,15\n', allWeeklyLines[3])
 
 
        os.remove(self.courseServer.latestResultSummaryFilename)
        os.remove(self.courseServer.latestResultDetailFilename)
        os.remove(self.courseServer.latestResultWeeklyEffortFilename)


    def testZipFiles(self):
        file1 = tempfile.NamedTemporaryFile()
        file2 = tempfile.NamedTemporaryFile()
        file1.write('foo')
        file2.write('bar')
        file1.flush()
        file2.flush()
        self.courseServer.zipFiles('/tmp/zipFileUnittest.zip',
                                   'foobar',
                                   [file1.name, file2.name]
                                   )
        # Read it all back:
        zipfile.ZipFile('/tmp/zipFileUnittest.zip').extractall(pwd='foobar')

    def buildSupportTables(self, testSetToLoad):
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
        if testSetToLoad == TestSet.ONE_STUDENT_ONE_CLASS:
            colValues = ExportClassTest.oneStudentTestData
        elif testSetToLoad == TestSet.TWO_STUDENTS_ONE_CLASS:
            colValues = ExportClassTest.twoStudentsOneClassTestData
        elif testSetToLoad == TestSet.TWO_STUDENTS_TWO_CLASSES:
            colValues = ExportClassTest.twoStudentsTwoClassesTestData
        else:
            raise ValueError('Requested test set unavailable: %s' % testSetToLoad)
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