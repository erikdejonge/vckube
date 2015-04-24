#!/usr/bin/env bash
rm -Rf build
rm -Rf dist
rm -Rf *.egg-info
echo "readme: https://github.com/erikdejonge/$(basename `pwd`)" > README.rst
python3 setup.py build
#python3 setup.py register
python3 setup.py sdist
python3 setup.py sdist upload
rm README.rst
rm -Rf build
rm -Rf dist
rm -Rf *.egg-info
echo
echo -e "\033[0;32m---------\033[0m"
echo