Run 
python setup.py install

Then, assuming your virtual environment is
under $HOME/.virtualenv you need to:

add2virtualenv ~/.virtualenvs/online_learning_computations/lib/python2.7/site-packages/online_learning_computations-0.26-py2.7.egg/src/

Or, in general
add2virtualenv <yourVirtualEnvs>/online_learning_computations/lib/python2.7/site-packages/online_learning_computations-0.26-py2.7.egg/src/


If you don't do the above, the code will not find module 'engagement'
for reasons unknown right now. To verify this problem: enter python
shell, and import engagement.
This import will fail before the add2virtualenv, but succeed after.

