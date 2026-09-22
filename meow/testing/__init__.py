"""The checks that have to be able to FAIL.

`stress` throws edge cases at every module; `smoke` starts the real
application and fails on a traceback. Both are verified to fail on real bugs,
which is the only property that makes a check worth running - a check that
cannot fail is decoration.
"""
