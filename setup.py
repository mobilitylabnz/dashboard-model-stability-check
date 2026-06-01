# setup.py
from setuptools import setup
from Cython.Build import cythonize

setup(
    packages=[],
    ext_modules=cythonize("aimsun_panels_output_duckdb.py"),
)