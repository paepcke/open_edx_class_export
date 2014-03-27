'''
Created on Mar 26, 2014

@author: paepcke
'''
import tornado
import unittest

from exportClass import CourseCSVServer
from tornado.httpserver import HTTPServer;

class Test(unittest.TestCase):

    def setUp(self):
        application = tornado.web.Application([(r"/exportClass", CourseCSVServer),])
        request = None #HTTPRequest.HTTPRequest()
        self.courseServer = CourseCSVServer(application, request, testing=True)


    def testOnMessage(self):
        #jsonMsg = "{'req' : 'getData', 'args' : '%Solar%'}"
        jsonMsg = '{"req" : "getData", "args" : {"courseId" : "Engineering/Solar/Fall2013", "wipeExisting" : "False", "inclPII" : "False", "cryptoPwd" : "foobar"}}'
        self.courseServer.on_message(jsonMsg)


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testOnMessage']
    unittest.main()