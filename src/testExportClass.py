'''
Created on Mar 26, 2014

@author: paepcke
'''

import tornado;
from tornado.ioloop import IOLoop;
from tornado.websocket import WebSocketHandler;
from tornado.httpserver import HTTPServer;

from collections import OrderedDict
import time
import unittest

from exportClass import CourseCSVServer
from pymysql_utils.pymysql_utils import MySQLDB


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
    
    def setUp(self):
        application = None
        request = None #HTTPRequest.HTTPRequest()
        self.courseServer = CourseCSVServer(application, request, testing=True)

    def tearDown(self):
        try:
            #*****self.mysqldb = MySQLDB(host='localhost', port=3306, user='unittest', db='unittest')
            self.mysqldb.dropTable('unittest.Activities')
            self.mysqldb.close()
        except:
            pass

    def testOnMessage(self):
        self.buildActivitiesTable()
        #************
        #*********self.mysqldb = MySQLDB(host='localhost', port=3306, user='unittest', db='unittest')
        queryIt = self.mysqldb.query('use unittest; SELECT course_display_name, anon_screen_name, time, isVideo FROM Activities WHERE course_display_name LIKE "CME/MedStats/2013-2015" ORDER BY anon_screen_name, time;')
        for res in queryIt:
            print(res)
        return
        #************
        
        jsonMsg = '{"req" : "getData", "args" : {"courseId" : "CME/MedStats/2013-2015", "wipeExisting" : "False", "inclPII" : "False", "cryptoPwd" : "foobar"}}'
        self.courseServer.on_message(jsonMsg)

    def buildActivitiesTable(self):
        try:
            self.mysqldb = MySQLDB(host='localhost', port=3306, user='unittest', db='unittest')
        except ValueError as e:
            self.fail(str(e) + " (For unit testing, localhost MySQL server must have user 'unittest' without password, and a database called 'unittest')")
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
        #***********self.mysqldb.close()
        


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testOnMessage']
    unittest.main()