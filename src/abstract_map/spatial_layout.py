from __future__ import absolute_import
import abc
import collections
import itertools
import numpy as np
import os.path
import pudb
import random
import scipy.integrate as ig
import scipy.linalg as la
import scipy.spatial as sp
import sys
import time
import warnings

import abstract_map.tools as tools

warnings.filterwarnings('ignore', '.*GUI is implemented')

# Abstract class compatibility across python 2 and python 3
ABC = abc.ABCMeta('ABC', (object,), {'__slots__': ()})

# Controlling constants
PAUSED_SLEEP_CYCLE = 0.1

# Constants defining when a spatial layout is "settled"
SETTLED_VEL_LIMIT = 0.1
SETTLED_ACC_LIMIT = 0.1

_SETTLED_VEL_LIMIT2 = SETTLED_VEL_LIMIT**2
_SETTLED_ACC_LIMIT2 = SETTLED_ACC_LIMIT**2

# Constants for the default behaviour of spatial layout
FRICTION_COEFFICIENT = 0.1
EXPANSION_COEFFICIENT = 0.01
INTEGRATION_DT = 0.1
SAFE_DISTANCE = 0.2
EXPLORATION_STEP = 0.25

STIFF_XL = 2.5
STIFF_L = 1
STIFF_M = 0.5
STIFF_S = 0.1
STIFF_XS = 0.01

DIR_ZERO = 0

MASS_LEVEL_LABEL = 0
MASS_LEVEL_SIGN = -1


class _Energised(ABC):
    """Abstraction for an inhereting class to denote it contains energy"""

    @abc.abstractmethod
    def totalEnergy(self):
        pass


class EnergyLog(object):
    """Log of the energy within a layout system"""

    def __init__(self):
        """Initialise the empty logs"""
        self.reset()

    def logEnergy(self, layout):
        """Logs the current energy in the spatial layout object"""
        self.t.append(layout._ode.t)
        self.kinetic.append(sum([m.totalEnergy() for m in layout._masses]))
        self.potential.append(
            sum([c.totalEnergy() for c in layout._constraints]))

    def reset(self):
        """Resets the log"""
        self.t = []
        self.kinetic = []
        self.potential = []


class Constraint(_Energised, ABC):
    """A spring like constraint guide for relative position of point-masses"""
    SOURCE_NONE = 0
    SOURCE_SIGN = 1
    SOURCE_LABEL = 2
    SOURCE_HIERARCHICAL = 3

    def __init__(self, ssi_id=None, source=SOURCE_NONE):
        """Constructor which gives a ssi_id to link the constraint to"""
        self._ssi_id = ssi_id
        self._source = source

    @abc.abstractmethod
    def __str__(self):
        """Force every subclass to implement a verbose string representation"""
        pass

    @abc.abstractmethod
    def applyForce(self):
        """Applies the current constraint force to each attached point-mass"""
        pass

    @abc.abstractmethod
    def displacement(self):
        """Distance the spring is displaced from its natural length"""
        pass

    @abc.abstractmethod
    def length(self):
        """Returns length of the constraint (same units as natural length)"""
        pass

    @abc.abstractmethod
    def masses(self):
        """Returns a list of masses in the constraint"""
        pass

    @abc.abstractmethod
    def placementSuggestion(self, mass):
        """Returns a placement tuple suggesting where to place the mass"""
        pass

    def totalEnergy(self):
        """Returns the potential energy held by the constraint"""
        return 0.5 * self._stiffness * np.square(self.displacement())


class ConstraintAngleGlobal(Constraint):
    """A constraint on the angle between two point-masses, in the global frame"""

    def __init__(self, mass_a, mass_b, natural_length, stiffness, ssi_id=-1):
        """Constructs the specified constraint between masses"""
        super(ConstraintAngleGlobal, self).__init__(ssi_id)

        self._mass_a = mass_a
        self._mass_b = mass_b
        self._natural_length = natural_length
        self._stiffness = stiffness

    def __str__(self):
        return "Constrain angle to %s from %s to %f (%f)" % (
            self._mass_a.name, self._mass_b.name, self._natural_length,
            self._stiffness)

    def applyForce(self):
        """Applies the constraint force to masses a and b"""
        force_vector = -self._stiffness * self.displacement() * _orthog(
            _uv(self._mass_a, self._mass_b))

        if not self._mass_a.fixed:
            self._mass_a.acc += force_vector / self._mass_a._mass
        if not self._mass_b.fixed:
            self._mass_b.acc += -force_vector / self._mass_b._mass

    def displacement(self):
        """Distance the spring is displaced from its natural length"""
        return _angleWrap(self.length() - self._natural_length)

    def masses(self):
        """Returns the list of masses in the global angular constraint"""
        return [self._mass_a, self._mass_b]

    def length(self):
        """Returns angle of of mass a relative to mass b, in global frame"""
        return _angle(self._mass_a, self._mass_b)

    def placementSuggestion(self, mass):
        """Returns a placement tuple suggesting where to place the mass"""
        if mass == self._mass_a:
            return {
                'mass': self._mass_b,
                'th': (self._natural_length, self._stiffness)
            }
        elif mass == self._mass_b:
            return {
                'mass': self._mass_a,
                'th': (_angleWrap(self._natural_length + np.pi),
                       self._stiffness)
            }
        else:
            return {}


class ConstraintAngleLocal(Constraint):
    """A constraint on the angle formed by three point-masses"""

    def __init__(self,
                 mass_a,
                 mass_b,
                 mass_c,
                 natural_length,
                 stiffness,
                 ssi_id=-1):
        """Constructs the specified constraint between masses"""
        super(ConstraintAngleLocal, self).__init__(ssi_id)

        self._mass_a = mass_a
        self._mass_b = mass_b
        self._mass_c = mass_c
        self._natural_length = natural_length
        self._stiffness = stiffness

    def __str__(self):
        return "Constrain angle to %s from %s (relative to %s) to %f (%f)" % (
            self._mass_a.name, self._mass_b.name, self._mass_c.name,
            self._natural_length, self._stiffness)

    def applyForce(self):
        """Applies the constraint force to masses a, b, and c"""
        force_vector_a = -self._stiffness * self.displacement() * _orthog(
            _uv(self._mass_a, self._mass_b))
        force_vector_c = -self._stiffness * self.displacement() * -1 * _orthog(
            _uv(self._mass_c, self._mass_b))

        acc_a = force_vector_a / self._mass_a._mass
        acc_c = force_vector_c / self._mass_c._mass
        if not self._mass_a.fixed:
            self._mass_a.acc += acc_a
        if not self._mass_b.fixed:
            self._mass_b.acc += -acc_a + -acc_c
        if not self._mass_c.fixed:
            self._mass_c.acc += acc_c

    def displacement(self):
        """Distance the spring is displaced from its natural length"""
        return _angleWrap(self.length() - self._natural_length)

    def masses(self):
        """Returns the list of masses in the local angular constraint"""
        return [self._mass_a, self._mass_b, self._mass_c]

    def length(self):
        """Returns angle of mass a, relative to vector from mass b to c"""
        return _angle(self._mass_a, self._mass_b, self._mass_c)

    def placementSuggestion(self, mass):
        """Returns a placement tuple suggesting where to place the mass"""
        if mass == self._mass_a:
            return {
                'mass':
                self._mass_b,
                'th': (_angleWrap(
                    _angle(self._mass_c, self._mass_b) + self._natural_length),
                       self._stiffness)
            }
        elif mass == self._mass_b:
            # There is no easy way to do this (the path of possible placements
            # of B follows a complex arc, which is discontinous because it is
            # present on both sides - i.e. constraint can be on left or right
            # side of |AC|). The whole optimisation process is needed for
            # overcoming problems like these. So for now, take the easy option
            # and simply place a suggestion (relative to C) for B to be at the
            # midpoint of |AC|
            r = (1 - np.absolute(self._natural_length) /
                 (2 * np.pi)) * _distance(self._mass_a, self._mass_c)
            a = -np.pi
            b = np.pi
            dummy = Mass('dummy')
            SEARCH_DEPTH = 20
            for i in range(0, SEARCH_DEPTH):
                mid = (a + b) / 2
                dummy.pos = self._mass_a.pos + r * np.array(
                    [np.cos(mid), np.sin(mid)])
                error = _angle(self._mass_a, dummy,
                               self._mass_c) - self._natural_length
                if error > 0:
                    b = mid
                else:
                    a = mid

            return {
                'mass': self._mass_a,
                'r': (r, 0.5 * self._stiffness),
                'th': (mid, 0.5 * self._stiffness)
            }
        elif mass == self._mass_c:
            return {
                'mass':
                self._mass_b,
                'th': (_angleWrap(
                    _angle(self._mass_a, self._mass_b) - self._natural_length),
                       self._stiffness)
            }
        else:
            return {}


class ConstraintDistance(Constraint):
    """A constraint on the distance between two point-masses"""

    def __init__(self, mass_a, mass_b, natural_length, stiffness, ssi_id=-1):
        """Constructs the specified constraint between masses"""
        super(ConstraintDistance, self).__init__(ssi_id)

        self._mass_a = mass_a
        self._mass_b = mass_b
        self._natural_length_unscaled = natural_length
        self._natural_length_scale_fn = None
        self._stiffness = stiffness

    def __getstate__(self):
        """Gets the pickle friendly state of the object"""
        obj_dict = self.__dict__.copy()
        # del obj_dict['_natural_length_scale_fn']
        obj_dict['_natural_length_scale_fn'] = None
        return obj_dict

    def __str__(self):
        return "Constrain distance between %s & %s to %f (%f)" % (
            self._mass_a.name, self._mass_b.name, self._natural_length,
            self._stiffness)

    @property
    def _natural_length(self):
        return self._natural_length_unscaled * (
            1 if self._natural_length_scale_fn is None else
            self._natural_length_scale_fn(self._mass_a, self._mass_b))

    def applyForce(self):
        """Applies the constraint force to masses a and b"""
        force_vector = -self._stiffness * self.displacement() * _uv(
            self._mass_a, self._mass_b)

        if not self._mass_a.fixed:
            self._mass_a.acc += force_vector / self._mass_a._mass
        if not self._mass_b.fixed:
            self._mass_b.acc += -force_vector / self._mass_b._mass

    def displacement(self):
        """Distance the spring is displaced from its natural length"""
        return self.length() - self._natural_length

    def masses(self):
        """Returns the list of masses in the distance constraint"""
        return [self._mass_a, self._mass_b]

    def length(self):
        """Returns distance between position of mass a and b"""
        return _distance(self._mass_a, self._mass_b)

    def placementSuggestion(self, mass):
        """Returns a placement tuple suggesting where to place the mass"""
        if mass == self._mass_a:
            return {
                'mass': self._mass_b,
                'r': (self._natural_length, self._stiffness)
            }
        elif mass == self._mass_b:
            return {
                'mass': self._mass_a,
                'r': (self._natural_length, self._stiffness)
            }
        else:
            return {}

    def setScaleGrabber(self, fn):
        """Sets a function for grabbing the scale unit from the layout"""
        self._natural_length_scale_fn = fn


class MassFixed(_Energised):
    """A point-mass, that is fixed to its initial location"""

    def __init__(self, name, pos, is_label=True):
        """Constructs a new fixed point mass, at a requested position"""
        _Energised.__init__(self)

        self.name = name
        self._mass = 1
        self._level = MASS_LEVEL_LABEL if is_label else MASS_LEVEL_SIGN
        self._parent = None
        self.pos = pos
        self.vel = np.zeros((2))
        self.acc = np.zeros((2))

    @property
    def fixed(self):
        """Returns if the mass is fixed (relies on hierarchy level)"""
        return self._level <= MASS_LEVEL_LABEL

    def applyExpansion(self, coem):
        """Applies the expansion force to the mass"""
        pass

    def applyFriction(self):
        """Applies the friction force to the mass"""
        pass

    def totalEnergy(self):
        """Returns the kinetic energy in the moving mass"""
        return 0


class Mass(MassFixed):
    """A point-mass, representing a toponym's location in a spatial layout"""

    def __init__(self, name, pos=None, vel=None, acc=None):
        """Constructs a new point mass, with a given name"""
        MassFixed.__init__(self, name, np.zeros((2)) if pos is None else pos)

        self._level = MASS_LEVEL_LABEL + 1  # Hierarchy level starting @ lowest
        self.vel = np.zeros((2)) if vel is None else vel
        self.acc = np.zeros((2)) if acc is None else acc

    def applyExpansion(self, coem):
        """Applies the expansion force to the mass"""
        self.acc += (0 if (coem is None or self._level != MASS_LEVEL_LABEL + 1)
                     else EXPANSION_COEFFICIENT * tools.uv(self.pos - coem))

    def applyFriction(self):
        """Applies the friction force to the mass"""
        self.acc += -FRICTION_COEFFICIENT * self.vel

    def totalEnergy(self):
        """Returns the kinetic energy in the moving mass"""
        return 0.5 * self._mass * np.sum(np.square(self.vel))


class RungeKutta45(object):
    """My own rough RungeKutta45 implementation for debugging"""

    def __init__(self, f):
        self.f = f
        self.y = []
        self.t = 0

    def set_initial_value(self, y, t):
        self.y = y
        self.t = t

    def integrate(self, t_new):
        k1 = self.f(self.t, self.y)
        k2 = self.f(self.t, self.y + INTEGRATION_DT * 0.5 * k1)
        k3 = self.f(self.t, self.y + INTEGRATION_DT * 0.5 * k2)
        k4 = self.f(self.t, self.y + INTEGRATION_DT * k3)
        self.y += (1. / 6.) * (k1 + 2 * k2 + 2 * k3 + k4) * INTEGRATION_DT
        self.t = t_new
        return self.y


class ScaleManager(object):
    """Class that manages unit scales, relating them to hierarchical levels"""
    # Tuples correspond to levels the distance relationship is between (e.g.
    # (2, 1) is a constraint between level 2 and level 1 places). The larger
    # number is always first. The defaults below are the starting points, and
    # are updated as scale values are observed in the real world
    # TODO maybe add some sort of safety check for illogical scale observations
    _DEFAULT_SCALES = {
        # Should never exist
        (MASS_LEVEL_SIGN, MASS_LEVEL_SIGN): 1,  # (-1, -1)
        (MASS_LEVEL_LABEL, MASS_LEVEL_LABEL): 1,  # (0, 0)

        # Should never be observed (can't observe an actual place, only label)
        (1, MASS_LEVEL_LABEL): 2,
        (2, MASS_LEVEL_LABEL): 12.5,
        (3, MASS_LEVEL_LABEL): 20,

        # Should be observed and updated as the system goes
        (1, MASS_LEVEL_SIGN): 8,
        (2, MASS_LEVEL_SIGN): 15,
        (1, 1): 4,
        (2, 2): 15,
        (3, 3): 50,
        (2, 1): 5,
        (3, 2): 15
    }  # yapf: disable

    _CONSTANT_SCALES = [
        # Should never exist
        (MASS_LEVEL_SIGN, MASS_LEVEL_SIGN),
        (MASS_LEVEL_LABEL, MASS_LEVEL_LABEL),

        # Should never be observed (can't observe an actual place, only label)
        (1, MASS_LEVEL_LABEL),
        (2, MASS_LEVEL_LABEL),
        (3, MASS_LEVEL_LABEL)
    ]

    def __init__(self):
        """Initialises the manager with the default scales"""
        self._scales = None
        self._observations = None

        self._exploration_factor = None
        self._exploration_step = EXPLORATION_STEP

        self.resetExploration()
        self._generateScales()

    def _generateScales(self):
        """Generates the scales list from the current observation list"""
        # Start with default (and finish if there are no observations)
        self._scales = dict(ScaleManager._DEFAULT_SCALES)
        if self._observations is None or not self._observations:
            return

        # Compute the mean for each entry in the dict
        for k, v in self._observations.items():
            self._scales[k] = np.sum(np.prod(v, 0)) / np.sum(v[1])

        # print("\tNew scale set:\n%s" % (self._scales))

    @staticmethod
    def _level_tuple(level_a, level_b):
        return tuple(sorted((level_a, level_b), reverse=True))

    def bumpExploration(self):
        self._exploration_factor += self._exploration_step

    def resetExploration(self):
        self._exploration_factor = 1

    def scaleUnit(self, mass_a, mass_b):
        """Returns scale unit between two masses, incorporating exploration"""
        level_tuple = ScaleManager._level_tuple(mass_a._level, mass_b._level)
        return (1 if level_tuple in ScaleManager._CONSTANT_SCALES else
                self._exploration_factor) * self._scales.get(level_tuple, 1)

    def setObservations(self, observations):
        """Sets the list of scale observations used by the manager"""
        # Turn list of observations into a dict (skipping out any scales which
        # are in the constant list)
        self._observations = {}
        for o in observations:
            level_tuple = ScaleManager._level_tuple(*o[0])
            if level_tuple in ScaleManager._CONSTANT_SCALES:
                continue
            dist_stiff = np.array(o[1:3])
            self._observations[level_tuple] = np.column_stack(
                (self._observations[level_tuple], dist_stiff
                )) if level_tuple in self._observations else dist_stiff

        # Generate the scales
        self._generateScales()


_debug_step_time = 0
_debug_step_t = 0


class SpatialLayout(object):
    """A set of springs and masses denoting abstract ideas about space"""

    def __init__(self, log=True):
        """Constructs a new empty spatial layout"""
        self._constraints = []
        self._masses = []
        self._scale_manager = ScaleManager()
        self._queued_heirarchies = []

        self._paused = False
        self._system_changed = False
        self._bounced_last_step = False
        self._last_settled = False

        self._energy_log = EnergyLog() if log else None

        self._post_state_change_fcn = None
        self._to_call_list = collections.deque()

        self._log = ({
            'a': [],
            'b': [],
            'c': [],
            'd': [],
            'e': []
        } if log else None)

        self._state_derivative = None
        self._ode = RungeKutta45(self._stateDerivative)
        # self._ode = ig.ode(self._stateDerivative).set_integrator(
        #     'dopri5', atol=1e-5, rtol=1e-2)

        self._coem = None

        self._log_file = (open(os.path.expanduser('~') + '/tmp/am.log', 'w')
                          if log else None)

    def __getstate__(self):
        """Gets the pickle friendly state of the object"""
        obj_dict = self.__dict__.copy()
        obj_dict.pop('_post_state_change_fcn', None)
        obj_dict.pop('_ode', None)
        obj_dict.pop('_to_call_list', None)
        obj_dict.pop('_log_file', None)
        return obj_dict

    def _placeMass(self, mass):
        """Places a mass at its best position according to the constraints"""
        # Get a list of placement suggestions from the added constraints
        # Get a list of the constraints that can suggest where to place the
        # mass (must have the mass, and all other masses must already be in the
        # network)
        cs_complete = [
            c for c in self._constraints
            if set(c.masses()).issubset(self._masses + [mass])
        ]

        # Get all placement suggestions from the influencing constraints
        ps_all = [c.placementSuggestion(mass) for c in cs_complete]

        # Handle the case where we have 0 placement suggestions
        SCALED_UNIT = 1  # TODO DO THIS PROPERLY!
        if not ps_all:
            # Get the placement
            if not self._masses:
                # Nothing else is in the layout, just place at origin
                placement = np.zeros_like(mass.pos)
            elif len(self._masses) < 3:
                # Place a distance of 1 x unit distance in +- 15 degrees from
                # the first placed mass (this allows the convex hull to be
                # derivable for future placements)
                deflection = np.pi / 12 * (1 if len(self._masses) == 1 else -1)
                placement = self._masses[0].pos + SCALED_UNIT * np.array([
                    np.cos(DIR_ZERO + deflection),
                    np.sin(DIR_ZERO + deflection)
                ])
            else:
                # Place a distance of 1 x unit distance outside of the convex
                # hull, in the direction formed from the center of mass to the
                # nearest hull vertice
                mps = np.stack([m.pos for m in self._masses])
                ch = sp.ConvexHull(mps)
                com = np.mean(mps, 0)
                distances = sp.distance.cdist(mps[ch.vertices, :], [com],
                                              'sqeuclidean')
                closest_hull_point = mps[ch.vertices[distances.argmin()], :]
                placement = com + tools.uv(closest_hull_point - com) * (
                    distances.min()**0.5 + SCALED_UNIT)

            # Perform the placement and return
            self._safePlacement(mass, placement)
            return

        # Go through the suggestions, merging all suggestions that are relative
        # to the same mass (m_key) into one placement suggestion so that the
        # extra information can be used to make a smarter placement suggestion
        F_MASS = lambda x: x['mass'].name  # noqa
        ps_all = [p for p in ps_all if p]
        ps_merged = []
        for m_key, g in itertools.groupby(sorted(ps_all, key=F_MASS), F_MASS):
            g = list(g)
            rs = np.array([list(p['r']) for p in g if 'r' in p])
            ths = np.array([list(p['th']) for p in g if 'th' in p])
            merged = {'mass': g[0]['mass']}
            if rs.size > 0:
                merged['r'] = (np.sum(np.prod(rs, 1)) / np.sum(rs[:, 1]),
                               np.sum(rs[:, 1]))
            if ths.size > 0:
                mean_vector = np.sum(
                    np.array([np.cos(ths[:, 0]),
                              np.sin(ths[:, 0])]) * ths[:, 1], 1)
                merged['th'] = (np.arctan2(mean_vector[1], mean_vector[0]),
                                np.sum(ths[:, 1]))
            ps_merged.append(merged)

        # Go through each of the merged placement suggestions, sorting them so
        # that the strongest suggestions are applied first. The suggestions are
        # converted to xy positions, and then merged through a weighted mean
        # TODO sort doesn't yet use "total weights" as a final key...
        ps_merged = sorted(
            ps_merged, key=lambda x: ('r' in x and 'th' in x, 'th' in x))
        placement = np.zeros((2))
        weight = 0
        for p in ps_merged:
            # Turn the placement suggestion into a weighted xy position
            if 'r' in p and 'th' in p:
                # Suggested is simply r,th from reference position
                suggested = p['mass'].pos + np.array([
                    p['r'][0] * np.cos(p['th'][0]),
                    p['r'][0] * np.sin(p['th'][0])
                ])
                w = p['r'][1] + p['th'][1]  # Not sure if should div 2...
            elif 'th' in p:
                # Suggested is on line at angle theta from reference, with
                # distance along line always guaranteed to be greater than
                # 1 (suggesting close to reference is bad for system
                # stability, & 1 is also fallback if no current placement)
                uv = np.array([np.cos(p['th'][0]), np.sin(p['th'][0])])
                r = np.dot(placement - p['mass'].pos, uv)
                suggested = p['mass'].pos + (1 if r < 1 or weight == 0 else
                                             r) * uv
                w = p['th'][1]
            elif 'r' in p:
                # Suggested is a distance r from the reference, in the
                # direction of the suggested placement (direction falls
                # back to a very rough "spread around circle" attempt which
                # only bases the spread on number of masses in the layout)
                th = _spreadAroundCircle(len(self._masses))
                uv = np.array([np.cos(th), np.sin(th)]) if weight == 0 else (
                    (placement - p['mass'].pos) /
                    la.norm(placement - p['mass'].pos))
                suggested = p['mass'].pos + p['r'][0] * uv
                w = p['r'][1]

            # Incorporate the weighted xy position into the weighted mean
            placement = (placement * weight + suggested * w) / (weight + w)
            weight += w

        # FINALLY, place the mass and add it into the network
        self._safePlacement(mass, placement)

    def _pullState(self):
        """Pulls the current state matrix of the system"""
        return np.concatenate(
            [np.concatenate((m.pos, m.vel)) for m in self._masses])

    def _pushState(self, y):
        """Pushes state matrix into system (obeying any safety conditions)"""
        # TODO safety conditions
        for i, m in enumerate(self._masses):
            m.pos = y[(i * 4):(i * 4 + 2)]
            m.vel = y[(i * 4 + 2):(i * 4 + 4)]

    def _pushStateSafely(self, y_a, y_b):
        """Obeys safety criteria (using old state) while pushing new state"""
        self._pushState(y_a)
        y_delta = y_b - y_a
        self._bounced_last_step = False
        for i, m in enumerate(self._masses):
            m.vel = y_b[(i * 4 + 2):(i * 4 + 4)]

        for i, m in enumerate(self._masses):
            self._stepSafely(m, y_delta[(i * 4):(i * 4 + 2)])

    def _refreshForces(self):
        """Refreshes the force value for each mass in the system"""
        for m in self._masses:
            m.acc[:] = 0
            m.applyFriction()
            m.applyExpansion(self._coem)

        for c in self._constraints:
            c.applyForce()

    def _safePlacement(self, mass, placement):
        """Places the mass at closest safe position to desired placement"""
        # Figure out the safe placement (iteratively getting more "desperate")
        safe = not self._masses
        sd2 = SAFE_DISTANCE**2
        it_count = 0  # Used to increase "push distance" to avoid getting stuck
        while not safe:
            dists = sp.distance.cdist(
                np.stack([m.pos for m in self._masses]), [placement],
                'sqeuclidean')
            if dists.min() > sd2:
                safe = True
            else:
                safe = False
                obstruction = self._masses[dists.argmin()].pos
                placement = obstruction + (SAFE_DISTANCE * 1.1**it_count *
                                           tools.uv(placement - obstruction))
            it_count += 1

        # Place the mass at its safe placement and add it to the system
        mass.pos = placement
        self._masses.append(mass)

    def _stateDerivative(self, t, y):
        """Computes the derivative of the current state"""
        self._pushState(y)
        self._refreshForces()
        return np.concatenate(
            [np.concatenate((m.vel, m.acc)) for m in self._masses])

    def _stepSafely(self, mass, step):
        """Steps mass position, while staying a safe distance from others"""
        # Note: we don't handle stepping over a mass and its exclusion zone
        # (mainly because it doesn't matter in terms of integrator stability)
        m_unsafe = []
        m_desired = Mass("desired")
        while m_unsafe is not None:
            # Find any clashes (note the 0.99 scaling factor is to stop
            # floating point error causing the mass to get "stuck" when
            # bouncing away from the collision)
            m_desired.pos = mass.pos + step
            m_unsafe = next((m for m in self._masses if m != mass and
                             _distance(m_desired, m) < SAFE_DISTANCE * 0.99),
                            None)

            # Take a safe "chunk" out of the desired step if we have a clash
            if m_unsafe is not None:
                # Get some metrics for the collision
                intersect = _firstCircleIntersect(mass.pos, m_desired.pos,
                                                  m_unsafe.pos, SAFE_DISTANCE)
                bounce_direction_m = _reflectedDirection(
                    mass.vel, intersect, m_unsafe.pos, outside=True)
                bounce_direction_mu = _reflectedDirection(
                    m_unsafe.vel, intersect, m_unsafe.pos, outside=False)
                bounced_position = _reflectedPosition(
                    mass.pos, step, intersect, bounce_direction_m)
                # Update states from the collision, and reduce the step
                mass.vel = _rotateVectorTo(mass.vel, bounce_direction_m)
                m_unsafe.vel = _rotateVectorTo(m_unsafe.vel,
                                               bounce_direction_mu)

                mass.pos = intersect
                step = bounced_position - mass.pos
                self._bounced_last_step = True

        # We now have a safe remaining step, apply it
        mass.pos += step

    def addConstraints(self, cs, place=True):
        for c in cs:
            self.addConstraint(c, place=place)

        # Update the observed distance if we have a label batch of constraints
        # Note: this heavily relies on the adder calling this methods rather
        # than singular addConstraint. This is a BAD solution, but will have to
        # do for now...
        if any(c._source == Constraint.SOURCE_LABEL for c in cs):
            self._scale_manager.setObservations(self.getObservedDistances())

    def addConstraint(self, c, place=True):
        """Adds a constraint (and any new masses to the layout)"""
        # Force only one mass in the system with a specified name
        for i, m in enumerate(c.masses()):
            m_found = self.getMass(m.name)
            if m_found is not None:
                if i == 0:
                    c._mass_a = m_found
                elif i == 1:
                    c._mass_b = m_found
                elif i == 2:
                    c._mass_c = m_found

        # Add in the constraint, attaching to scale manager if appropraite
        self._constraints.append(c)
        if type(c) == ConstraintDistance:
            c.setScaleGrabber(self._scale_manager.scaleUnit)

        # Now that there are constraints to inform placement, add the mass
        for m in reversed(c.masses()):
            self.addMass(m, place=place)

        # Mark that the system state has been changed
        self.markSystemChanged()

    def addHierarchy(self, h):
        """Adds hints about hierarchy to the spatial layout"""
        # Look for the two masses, queueing and bailing if both don't already
        # exist in the layout
        m_child = self.getMass(h[0])
        m_parent = self.getMass(h[1])
        if m_child is None or m_parent is None:
            self._queued_heirarchies.append(h)
            return

        # Sanity check to make sure that we can proceed
        if m_child._parent is not None:
            raise ValueError(
                ("Trying to add a parent (%s - %d) to a child (%s - %d) "
                 "that already has a parent (%s). "
                 "Operation not supported.") % (m_parent.name, m_parent._level,
                                                m_child.name, m_child._level,
                                                m_child.m_parent.name))

        # Look up the tree from the child, ensuring that all parents have a
        # level greater than their child
        m_child._parent = m_parent
        m = m_child
        while m._parent is not None:
            if m._parent._level <= m._level:
                m._parent._level = m._level + 1
            m = m._parent

        # # Attempt to add a hierarchy constraint if it is valid to do so
        # if m_parent._parent is not None:
        #     # Look for an existing matching hierarchical constraint
        #     m_parent_of_parent = m_parent._parent
        #     existing_constraint = next(
        #         (c for c in self._constraints
        #          if sorted([m_child, m_parent, m_parent_of_parent]) == sorted(
        #              c.masses())), None)

        #     # Add new hierarchical constraint if an existing one wasn't found
        #     if existing_constraint is None:
        #         c = ConstraintAngleLocal(m_child, m_parent, m_parent_of_parent,
        #                                  np.pi, STIFF_S)
        #         self.addConstraint(c)
        #         print("\tAdded: %s" % (c))

    def addMass(self, m, place=True):
        """Adds a mass to the layout (only if it is new)"""
        if m not in self._masses:
            # First try and add any queued level information to the mass
            self._masses.append(m)
            hs = self._queued_heirarchies
            self._queued_heirarchies = []
            for h in hs:
                self.addHierarchy(h)
            self._masses.pop()

            # Then perform the placement of the mass
            if place and not m.fixed:
                self._placeMass(m)
            else:
                self._masses.append(m)

            # if m.name:
            #     print("\tAdded: %s" % (m.name))
            # else:
            #     print("\033[91mAdded: %s\033[0m" % (m.name))

            # Lastly, mark that the system state has been changed
            self.markSystemChanged()

    def callInStep(self, fn, *args):
        """Adds a request to call a function with args in the next step"""
        self._to_call_list.append((fn, args))

    def executeWaitingCalls(self):
        """Executes all calls waiting in the queue"""
        while self._to_call_list:
            to_call = self._to_call_list.popleft()
            to_call[0](*to_call[1])

    def getMass(self, name):
        """Returns a mass with the requested name if it exists"""
        return next((m for m in self._masses if m.name == name), None)

    def getObservedDistances(self):
        """Returns a list of observed distances (used with scale manager)"""
        # Start with the list of fixed masses (a fixed mass by definition is an
        # observed location in the environment)
        observed_masses = {m.name: m.pos for m in self._masses if m.fixed}

        # Get the list of label observations (through their constraints)
        label_dist_constraints = [
            c for c in self._constraints
            if c._source == Constraint.SOURCE_LABEL and
            type(c) == ConstraintDistance
        ]
        label_ang_constraints = [
            c for c in self._constraints
            if c._source == Constraint.SOURCE_LABEL and
            type(c) == ConstraintAngleGlobal
        ]
        # print("OBSERVED DISTANCE LIST:")
        # print("\tHave following label constraints:")
        # for c in label_dist_constraints + label_ang_constraints:
        #     print("\t\t%s" % (c))

        # Add the location suggested by each label to the list of observed
        # masses (observing the label 'observes' the place at some assumed
        # relative position)
        for dist_c in label_dist_constraints:
            # Find the corresponding angular constraint
            mass_label = (dist_c._mass_a
                          if not dist_c._mass_a.fixed else dist_c._mass_b)
            mass_fixed = (dist_c._mass_a
                          if mass_label is dist_c._mass_b else dist_c._mass_b)
            ang_c = next(
                (c for c in label_ang_constraints if mass_label in c.masses()),
                None)
            if ang_c is None:
                raise ValueError(
                    "Angular constraint for observation of %s not found" %
                    (mass_label.name))

            # Determine the position suggested by the label observation
            th = _angleWrap(ang_c._natural_length +
                            (0 if mass_label == ang_c._mass_a else np.pi))
            pos = mass_fixed.pos + dist_c._natural_length * np.array(
                [np.cos(th), np.sin(th)])

            # Add the observed label to the list
            observed_masses[mass_label.name] = pos

        # print("\tObserved masses:")
        # for m in observed_masses:
        #     print("\t\t%s" % (m))

        # Get the list of distance constraints with both labels observed
        observed_constraints = [
            c for c in self._constraints
            if type(c) == ConstraintDistance and c._mass_a.name in
            observed_masses and c._mass_b.name in observed_masses
        ]

        # print("\tObserved distance constraints:")
        # for c in observed_constraints:
        #     print("\t\t%s - %s" % (c._mass_a.name, c._mass_b.name))

        # Calculate the observed distance for each constraint
        observations = []  # a list of ((levels tuple), distance, stiffness)
        for c in observed_constraints:
            d = c._mass_a.pos - c._mass_b.pos
            observations.append(((c._mass_a._level, c._mass_b._level),
                                 (d[0]**2 + d[1]**2)**0.5, c._stiffness))

        # Filter out all observations of the distance between a label and place
        # (it makes no sense because the system is incapable of assuming this
        # distance it just gives it some arbitrary value)
        return [o for o in observations if MASS_LEVEL_LABEL not in o[0]]

    def initialiseState(self):
        """Initialises the state to best match provided constraints"""
        # Sort all masses and constraints into the "best" order (best is
        # defined as iteratively placing the mass that will "complete" the most
        # remaining constraints on placement)
        cs = []
        ms = []
        while self._constraints or self._masses:
            # Recompute score for each mass (number of constraints that its
            # placement will complete)
            scores = [
                sum([
                    len(list(set(c.masses()).intersection(self._masses))) == 1
                    for c in self._constraints
                ])
                for m in self._masses
            ]

            # Place the "best" unplaced mass
            m_best = self._masses[scores.index(min(scores))]
            ms.append(m_best)
            self._masses.remove(m_best)

            # Move all constraints with all masses placed to the placed list
            c_placed = [
                c for c in self._constraints
                if len(set(c.masses()).intersection(self._masses)) == 0
            ]
            for c in c_placed:
                cs.append(c)
                self._constraints.remove(c)

        # Now place all of the masses in order (using the constraints to inform
        # placement)
        self._constraints = cs
        for m in ms:
            self._placeMass(m)

    def isObserved(self, name):
        m = self.getMass(name)
        if m is None:
            return False
        else:
            return next(
                (c for c in self._constraints
                 if c._source == Constraint.SOURCE_LABEL and m in c.masses()),
                None) is not None

    def isSettled(self):
        """Uses ODE state derivative to check if the layout has settled down"""
        if self._state_derivative is None:
            return False
        else:
            vels = [
                x for i, x in enumerate(self._state_derivative)
                if not i % 4 // 2
            ]
            accs = [
                x for i, x in enumerate(self._state_derivative) if i % 4 // 2
            ]
            # abs_vels = np.abs(vels)
            # abs_accs = np.abs(accs)
            # i_vel = np.floor_divide(np.argmax(abs_vels), 2)
            # i_acc = np.floor_divide(np.argmax(abs_accs), 2)
            # print("Vel: %f,%f (%s) Acc: %f,%f (%s)" %
            #       (vels[i_vel * 2], vels[i_vel * 2 + 1],
            #        self._masses[i_vel].name, accs[i_acc * 2],
            #        accs[i_acc * 2 + 1], self._masses[i_acc].name))
            return all([
                vels[i]**2 + vels[i + 1]**2 < _SETTLED_VEL_LIMIT2
                for i in range(0, len(vels), 2)
            ]) and all([
                accs[i]**2 + accs[i + 1]**2 < _SETTLED_ACC_LIMIT2
                for i in range(0, len(accs), 2)
            ])

    def logEnergy(self):
        """Writes the current system energy to the energy log if available"""
        if self._energy_log is not None:
            self._energy_log.logEnergy(self)

    def markStateChanged(self):
        """Explicit declaration of a change of system state"""
        self.logEnergy()
        if self._post_state_change_fcn is not None:
            self._post_state_change_fcn(self)

    def markSystemChanged(self, reset_history=False):
        """Explicit declaration of a change in system structure"""
        # Always unpause (a new system needs the optimiser)
        self._paused = False

        # Reset the history if requested
        if reset_history:
            self.resetEnergyLog()

        # Record system change & mark state change (system change changes state)
        self._system_changed = True
        self.markStateChanged()

    def step(self):
        """Performs a single iteration of the spatial layout optimisation"""
        # Execute any waiting functions before we start the step
        self.executeWaitingCalls()

        # Return from here until new / modified SSI unpauses the network
        if self._paused:
            if self._log is not None:
                self._log_file.write("UNPAUSED!\n")
                self._log_file.flush()
            time.sleep(PAUSED_SLEEP_CYCLE)
            return

        # We don't have any masses, so mark a step and exit
        if not self._masses:
            self.markStateChanged()
            return

        # Handle system changes if present
        if self._system_changed or self._bounced_last_step:
            self._ode.set_initial_value(self._pullState(), self._ode.t)
            self._system_changed = False

        # Perform a step with the ODE integrator
        ta = time.time()
        state = np.copy(self._ode.y)
        state_next = self._ode.integrate(self._ode.t + INTEGRATION_DT)
        if self._log is not None:
            self._log['a'].append(self._ode.t)
            self._log['b'].append(time.time() - ta)
            self._log_file.write("INT\n")
            self._log_file.flush()

        # Safely apply the suggested new state
        ta = time.time()
        # self._pushState(state_next)
        self._pushStateSafely(state, state_next)
        if self._log is not None:
            self._log['c'].append(time.time() - ta)
            self._log_file.write("STEPPED\n")
            self._log_file.flush()

        # Record the true state derivative and mark system state change
        ta = time.time()
        self._refreshForces()
        self._state_derivative = np.concatenate(
            [np.concatenate((m.vel, m.acc)) for m in self._masses])
        self.markStateChanged()
        if self._log is not None:
            self._log['d'].append(time.time() - ta)
            self._log_file.write("DONE\n")
            self._log_file.flush()

    def resetEnergyLog(self):
        """Resets the energy log"""
        if self._energy_log is not None:
            self._energy_log.reset()

    def randomiseState(self, window_size=5):
        """Randomises the initial state within a given window size"""
        for m in self._masses:
            m.pos[0] = (random.random() - 0.5) * window_size
            m.pos[1] = (random.random() - 0.5) * window_size
            m.vel = np.zeros_like(m.vel)
            m.acc = np.zeros_like(m.acc)

        # Mark that the system state has been changed
        self.markSystemChanged(reset_history=True)

    def updateConstraints(self, cs):
        """Update existing constraints from a tag id (instead of adding)"""
        # Ensure the update is valid
        assert cs[0]._ssi_id is not None, "To update, ssi_ids not be none"
        assert all(
            c._ssi_id == cs[0]._ssi_id for c in cs
        ), "All constraints that are being updated must have the same tag ID"

        # Split into keep & update constraints
        ssi_id = cs[0]._ssi_id
        constraints_keep = [
            c for c in self._constraints if c._ssi_id != ssi_id
        ]
        # constraints_update = [
        #     c for c in self._constraints if c._ssi_id == ssi_id
        # ]

        # Update the constraints (keeping the previous pause status)
        self._constraints = constraints_keep
        paused = self._paused
        self.addConstraints(cs)
        self._paused = paused

        # Mark that the system state has been changed
        self.markStateChanged()


def _angle(mass_a, mass_b, mass_c=None):
    """Compute the angle formed by mass a, relative to b (and optionally c) """
    v_ab = mass_a.pos - mass_b.pos
    ret = np.arctan2(v_ab[1], v_ab[0])
    if mass_c is not None:
        v_cb = mass_c.pos - mass_b.pos
        ret -= np.arctan2(v_cb[1], v_cb[0])

    return _angleWrap(ret)


def _angleWrap(angle):
    """Returns the angle, in the range of [-PI,+PI)"""
    ret = (angle + np.pi) % (2 * np.pi)
    if ret < 0:
        ret += 2 * np.pi

    return ret - np.pi


def _distance(mass_a, mass_b):
    """Computes the distance between two masses"""
    ab = mass_a.pos - mass_b.pos
    return (ab[0]**2 + ab[1]**2)**0.5


def _firstCircleIntersect(line_a, line_b, circle_center, circle_r):
    """Finds the first point that line from a to b intesecting a circle"""
    # Find coefficients for the linear equation
    disp = line_b - line_a
    use_vertical = np.abs(disp[0]) < np.abs(disp[1])
    m = disp[0] / disp[1] if use_vertical else disp[1] / disp[0]
    c = (-m * line_a[1] + line_a[0]
         if use_vertical else -m * line_a[0] + line_a[1])

    # Find coefficients for the quadratic equation, and find the roots
    quad_a = -1 - m**2
    quad_b = (-2 * m * c +
              2 * m * (circle_center[0] if use_vertical else circle_center[1])
              + 2 * (circle_center[1] if use_vertical else circle_center[0]))
    quad_c = (-c**2 + circle_r**2 - circle_center[0]**2 - circle_center[1]**2 +
              2 * c * (circle_center[0] if use_vertical else circle_center[1]))
    discriminant = quad_b**2 - 4 * quad_a * quad_c
    if discriminant < 0:
        raise ValueError("Intersection discriminant < 0")
    root_1 = (-quad_b + discriminant**0.5) / (2 * quad_a)
    root_2 = (-quad_b - discriminant**0.5) / (2 * quad_a)

    # Return the intersect that is closest to line_a
    intersect_1 = np.array([(m * root_1 + c if use_vertical else root_1),
                            (root_1 if use_vertical else m * root_1 + c)])
    intersect_2 = np.array([(m * root_2 + c if use_vertical else root_2),
                            (root_2 if use_vertical else m * root_2 + c)])
    d1 = intersect_1 - line_a
    d2 = intersect_2 - line_a
    return (intersect_1 if (d1[0]**2 + d1[1]**2)**0.5 <
            (d2[0]**2 + d2[1]**2)**0.5 else intersect_2)


def _reflectedDirection(velocity, reflect_point, reflect_origin, outside=True):
    """Gets the direction of reflection from a given point"""
    # Here we do reflection based on input velocity direction relative to the
    # tangent of the intersection point with the "safety" circle. If "outside"
    # parameter is true, then the reflected direction will be left of the
    # tangent, otherwise reflection will be right of tangent
    vel_ang = np.arctan2(velocity[1], velocity[0])
    tan_ang = _angleWrap(
        np.arctan2(reflect_point[1] - reflect_origin[1], reflect_point[0] -
                   reflect_origin[0]) + np.pi / 2)
    direction = -1 if outside else 1
    return _angleWrap(tan_ang +
                      direction * np.abs(_angleWrap(vel_ang - tan_ang)))


def _reflectedPosition(start_point, step, reflect_point, reflect_direction):
    """Gets the point when a step is reflected around a given point"""
    reflect_step = reflect_point - start_point
    r = ((step[0]**2 + step[1]**2)**0.5 -
         (reflect_step[0]**2 + reflect_step[1]**2)**0.5)
    return reflect_point + r * np.array(
        [np.cos(reflect_direction),
         np.sin(reflect_direction)])


def _rotateVectorTo(vector, angle):
    """Rotates a vector to a requested orientation"""
    r = (vector[0]**2 + vector[1]**2)**0.5
    return np.array([r * np.cos(angle), r * np.sin(angle)])


def _spreadAroundCircle(n):
    """Returns the angle in radians when trying to spread around a circle"""
    n = n % 16
    if n == 0:
        return 0
    else:
        return (1 if n % 2 == 0 else -1) * np.pi * (1 + 2 * np.floor(
            0.5 * (n - 2**np.floor(np.log2(n))))) / (2**np.floor(np.log2(n)))


def _uv(mass_a, mass_b):
    """Returns the unit vector pointing to mass a, from mass b"""
    ab = mass_a.pos - mass_b.pos
    return (np.array([1, 0]).T if np.array_equal(mass_a.pos, mass_b.pos) else
            ab / (ab[0]**2 + ab[1]**2)**0.5)


def _orthog(vector):
    return np.array([-vector[1], vector[0]])
