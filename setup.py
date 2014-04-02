import multiprocessing
from setuptools import setup, find_packages
setup(
    name = "open_edx_class_export",
    version = "0.20",
    packages = find_packages(),

    # Dependencies on other packages:
    setup_requires   = ['nose>=1.1.2'],
    install_requires = ['online_learning_computations>=0.27',
			'pymysql_utils>=0.25',
			],
    tests_require    = [],

    # Unit tests; they are initiated via 'python setup.py test'
    test_suite       = 'nose.collector', 

    package_data = {
        # If any package contains *.txt or *.rst files, include them:
     #   '': ['*.txt', '*.rst'],
        # And include any *.msg files found in the 'hello' package, too:
     #   'hello': ['*.msg'],
    },

    # metadata for upload to PyPI
    author = "Andreas Paepcke",
    #author_email = "me@example.com",
    description = "In an OpenEdX instance installation, produces class statistics from event stream data.",
    license = "BSD",
    keywords = "openEdX, instruction",
    url = "https://github.com/paepcke/open_edx_class_export",   # project home page, if any
)
