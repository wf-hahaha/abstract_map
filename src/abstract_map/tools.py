import collections
import numpy as np
import os
import pudb
import sys
import tf_conversions

import geometry_msgs.msg as geometry_msgs


class abstractstatic(staticmethod):
    """Allows the abstractstatic decorator in Python 2 (not needed in 3.3+)"""
    __slots__ = ()

    def __init__(self, function):
        super(abstractstatic, self).__init__(function)
        function.__isabstractmethod__ = True

    __isabstractmethod__ = True


class HiddenPrints:
    """Hacky solution to stop library calls from printing junk..."""

    def __enter__(self):
        self._original_stdout = sys.stdout
        sys.stdout = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._original_stdout


def flatten(x):
    # Flattens a list recursively into its items
    if isinstance(x, dict):
        return [a for i in x.values() for a in flatten(i)]
    elif isinstance(x, collections.Iterable):
        return [a for i in x for a in flatten(i)]
    else:
        return [x]


def levelInHierarchy(h, hierarchy):
    """Gets the level, defined as levels above bottom of hierarchy"""
    hs = [h]
    level = 0
    while any(x[2] for x in hs):
        children = [c for x in hs for c in x[2]]
        hs = [next(x for x in hierarchy if x[0] == c) for c in children]
        level += 1
    return level


def poseMsgToXYTh(msg):
    """Returns a tuple with 2D x, y, th from a ROS pose msg"""
    return (msg.position.x, msg.position.y,
            quaternionMsgToYaw(msg.orientation))


def quaternionMsgToTuple(msg):
    """Helper function for converting a quaternion msg to a tuple"""
    return (msg.x, msg.y, msg.z, msg.w)


def quaternionMsgToYaw(msg):
    """Helper function for getting the yaw angle from a quaternion msg"""
    r, p, y = tf_conversions.transformations.euler_from_quaternion(
        quaternionMsgToTuple(msg))
    return y


def uv(vector):
    """Returns the unit vector of a 2D vector"""
    return (np.array([1, 0]) if not vector.any() else
            vector / (vector[0]**2 + vector[1]**2)**0.5)


def xythToPoseMsg(x, y, th):
    return geometry_msgs.Pose(
        position=geometry_msgs.Point(x, y, 0),
        orientation=yawToQuaternionMsg(th))


def yawToQuaternionMsg(yaw):
    return geometry_msgs.Quaternion(*yawToTuple(yaw))


def yawToTuple(yaw):
    return tf_conversions.transformations.quaternion_from_euler(0, 0, yaw)
