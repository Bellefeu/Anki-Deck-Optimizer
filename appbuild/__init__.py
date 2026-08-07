"""Publisher build tooling for PRISM.

This is a real package, with this file, on purpose. Left as a namespace
directory it lost every import race against the `packaging` distribution that
setuptools installs, which is also why the folder is not called that.
"""
