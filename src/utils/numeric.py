"""
Coerce numeric values to Python float for use with abs() and other scalar operations.
Avoids "abs(): Argument #1 must be of type int|float, array given" when numpy
arrays are passed by mistake (e.g. from threshold/angle calculations).
"""
import numpy as np


def to_scalar(x):
    """
    Return a single Python float from a number or array.
    Use before abs() and other scalar-only operations to avoid passing ndarray.
    """
    a = np.asarray(x)
    if a.size == 0:
        return 0.0
    return float(a.flat[0])
