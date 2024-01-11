from setuptools import setup, find_packages
import sys
import os

version = '1.0.3'

setup(name='pbcon',
      version=version,
      description="Convenience tools to work with pybricks.",
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
          'pybricksdev',  # Add pybricksdev as a requirement
      ],
      entry_points={
          'console_scripts': [
              'pbscan = pbcon.pbscan:main',
              'pbcon = pbcon.pbcon:main',  # Add pbcon as a console script
          ],
      },
      )
