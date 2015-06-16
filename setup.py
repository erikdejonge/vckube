# coding=utf-8
"""
Profiler utility for python
Active8 (04-03-15)
license: GNU-GPL2
"""
from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()

from setuptools import setup
setup(name='vckube',
      version='33',
      description='Kubernetes Vagrant Provisioning and management script',
      url='https://github.com/erikdejonge/vckube',
      author='Erik de Jonge',
      author_email='erik@a8.nl',
      license='GPL',
      packages=['vckube'],
      zip_safe=True,
      #install_requires=['paramiko', 'python-vagrant', 'consoleprinter', 'cmdssh'],
      entry_points={
          'console_scripts': [
              'vckube=vckube:main',
          ],
      },
      classifiers=[
          "Programming Language :: Python",
          "Programming Language :: Python :: 3",
          "Development Status :: 4 - Beta ",
          "Intended Audience :: Developers",
          "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)",
          "Operating System :: POSIX",
          "Topic :: Software Development :: Libraries :: Python Modules",
          "Topic :: Software Development :: Testing",
          "Topic :: System",
      ])
