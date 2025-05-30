from setuptools import setup, find_packages
import sys
import os

version = '1.3.5'

setup(name='pbcon',
      version=version,
      description="Convenience tools to work with pybricksdev.",
      long_description="""\
""",
      classifiers=[],  # Get strings from http://pypi.python.org/pypi?%3Aaction=list_classifiers
      keywords='pybricks lego robotics micropython',
      author='Tom Schank',
      author_email='DrTom@schank.ch',
      url='',
      license='',
      packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
      include_package_data=True,
      zip_safe=False,
      install_requires=[
          'autopep8==2.1.0',
          'humanize>=4.9.0',
          'pybricks>=3.0.0',
          'pybricksdev==1.0.0a48',
          'urwid>=2.5.3',
      ],
      entry_points={
          'console_scripts': [
              'pbscan = pbcon.pbscan:main',
              'pbcon = pbcon.pbcon:main',
          ],
      },
      )
