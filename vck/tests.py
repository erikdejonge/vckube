# coding=utf-8
# coding=utf-8
"""
unittester
-
Active8 (05-03-15)
author: erik@a8.nl
license: GNU-GPL2
"""
from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()
from unittester import *
from vckube import *


class K8svpTestCase(unittest.TestCase):
    """
    """

    def test_command(self):
        """
        test_assert_raises
        """
        pass

    # remote_cmd(server, cmd):
    # def run_cmd(cmd, pr=False, shell=False, streamoutput=True, returnoutput=False):
    #     def scp(server, cmdtype, fp1, fp2):
    # def test_remote_command(self):
    # def test_scp(self):
    #     scp(server, cmdtype, fp1, fp2)

def main():
    """
    main
    """
    unit_test_main(globals())


if __name__ == "__main__":
    main()
