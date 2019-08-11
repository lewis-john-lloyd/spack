# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import json
import os
import platform
import re
import subprocess
import sys

try:
    from collections.abc import MutableMapping
except ImportError:
    from collections import MutableMapping

import six


class LazyDictionary(MutableMapping):
    """Lazy dictionary that gets constructed on first access to any object key

    Args:
        factory (callable): factory function to construct the dictionary
    """
    def __init__(self, factory, *args, **kwargs):
        self.factory = factory
        self.args = args
        self.kwargs = kwargs
        self._data = None

    @property
    def data(self):
        if self._data is None:
            self._data = self.factory(*self.args, **self.kwargs)
        return self._data

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __delitem__(self, key):
        del self.data[key]

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)


def _load_targets_json():
    """Loads ``targets.json`` in memory."""
    directory_name = os.path.dirname(os.path.abspath(__file__))
    filename = os.path.join(directory_name, 'targets.json')
    with open(filename, 'r') as f:
        return json.load(f)


#: In memory representation of the data in targets.json, loaded on first access
_targets_json = LazyDictionary(_load_targets_json)

#: Known predicates that can be used to construct feature aliases
_feature_alias_predicate = {}


class FeatureAliasTest(object):
    """A test that must be passed for a feature alias to succeed.

    Args:
        rules (dict): dictionary of rules to be met. Each key must be a
            valid alias predicate
    """
    def __init__(self, rules):
        self.rules = rules
        self.predicates = []
        for name, args in rules.items():
            self.predicates.append(_feature_alias_predicate[name](args))

    def __call__(self, microarchitecture):
        return all(
            feature_test(microarchitecture) for feature_test in self.predicates
        )


def _feature_aliases():
    """Returns the dictionary of all defined feature aliases."""
    json_data = _targets_json['feature_aliases']
    aliases = {}
    for alias, rules in json_data.items():
        aliases[alias] = FeatureAliasTest(rules)
    return aliases


def alias_predicate(func):
    """Decorator to register a predicate that can be used to define
    feature aliases.
    """
    name = func.__name__
    if name in _feature_alias_predicate:
        msg = 'the feature alias predicate "{0}" already exists'.format(name)
        raise KeyError(msg)

    # TODO: This must update the schema when we'll add it
    _feature_alias_predicate[name] = func

    return func


@alias_predicate
def any_of(list_of_features):
    """Returns a predicate that is True if any of the feature in the
    list is in the microarchitecture being tested, False otherwise.
    """
    def _impl(microarchitecture):
        return any(x in microarchitecture for x in list_of_features)
    return _impl


@alias_predicate
def families(list_of_families):
    """Returns a predicate that is True if the architecture family of
    the microarchitecture being tested is in the list, False otherwise.
    """
    def _impl(microarchitecture):
        return str(microarchitecture.architecture_family) in list_of_families
    return _impl


class MicroArchitecture(object):
    #: Aliases for micro-architecture's features
    feature_aliases = LazyDictionary(_feature_aliases)

    def __init__(
            self, name, parents, vendor, features, compilers, generation=0
    ):
        """Represents a specific CPU micro-architecture.

        Args:
            name (str): name of the micro-architecture (e.g. skylake).
            parents (list): list of parents micro-architectures, if any.
                Parenthood is considered by cpu features and not
                chronologically. As such each micro-architecture is
                compatible with its ancestors. For example "skylake",
                which has "broadwell" as a parent, supports running binaries
                optimized for "broadwell".
            vendor (str): vendor of the micro-architecture
            features (list of str): supported CPU flags. Note that the semantic
                of the flags in this field might vary among architectures, if
                at all present. For instance x86_64 processors will list all
                the flags supported by a given CPU while Arm processors will
                list instead only the flags that have been added on top of the
                base model for the current micro-architecture.
            compilers (dict): compiler support to generate tuned code for this
                micro-architecture. This dictionary has as keys names of
                supported compilers, while values are list of dictionaries
                with fields:

                * name: name of the micro-architecture according to the
                    compiler. This is the name passed to the ``-march`` option
                    or similar. Not needed if the name is the same as that
                    passed in as argument above.
                * versions: versions that support this micro-architecture.

            generation (int): generation of the micro-architecture, if
                relevant.
        """
        self.name = name
        self.parents = parents
        self.vendor = vendor
        self.features = features
        self.compilers = compilers
        self.generation = generation

    @property
    def ancestors(self):
        value = self.parents[:]
        for parent in self.parents:
            value.extend(a for a in parent.ancestors if a not in value)
        return value

    def _ensure_strictly_orderable(self, other):
        if not (self in other.ancestors or other in self.ancestors):
            msg = "There is no ordering relationship between targets "
            msg += "%s and %s." % (self.name, other.name)
            raise ValueError(msg)

    def __eq__(self, other):
        if not isinstance(other, MicroArchitecture):
            return NotImplemented

        return (self.name == other.name and
                self.vendor == other.vendor and
                self.features == other.features and
                self.ancestors == other.ancestors and
                self.compilers == other.compilers and
                self.generation == other.generation)

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        if not isinstance(other, MicroArchitecture):
            return NotImplemented

        self._ensure_strictly_orderable(other)

        # If the current micro-architecture is in the list of ancestors
        # of the other micro-architecture, then it's less than the other
        if self in other.ancestors:
            return True

        return False

    def __le__(self, other):
        return (self == other) or (self < other)

    def __gt__(self, other):
        return not (self <= other)

    def __ge__(self, other):
        return not (self < other)

    def __repr__(self):
        cls_name = self.__class__.__name__
        fmt = cls_name + '({0.name!r}, {0.parents!r}, {0.vendor!r}, ' \
                         '{0.features!r}, {0.compilers!r}, {0.generation!r})'
        return fmt.format(self)

    def __str__(self):
        return self.name

    def __contains__(self, feature):
        # Here we look first in the raw features, and fall-back to
        # feature aliases if not match was found
        if feature in self.features:
            return True

        # Check if the alias is defined, if not it will return False
        match_alias = MicroArchitecture.feature_aliases.get(
            feature, lambda x: False
        )
        return match_alias(self)

    @property
    def architecture_family(self):
        """Returns the architecture family a given target belongs to"""
        roots = [x for x in [self] + self.ancestors if not x.ancestors]
        msg = "a target is expected to belong to just one architecture family"
        msg += "[found {0}]".format(', '.join(str(x) for x in roots))
        assert len(roots) == 1, msg

        return roots.pop()


def generic_microarchitecture(name):
    """Returns a generic micro-architecture with no vendor and no features.

    Args:
        name (str): name of the micro-architecture
    """
    return MicroArchitecture(
        name, parents=[], vendor='generic', features=[], compilers={}
    )


def _known_microarchitectures():
    """Returns a dictionary of the known micro-architectures. If the
    current host platform is unknown adds it too as a generic target.
    """

    # TODO: Simplify this logic using object_pairs_hook to OrderedDict
    # TODO: when we stop supporting python2.6

    def fill_target_from_dict(name, data, targets):
        """Recursively fills targets by adding the micro-architecture
        passed as argument and all its ancestors.

        Args:
            name (str): micro-architecture to be added to targets.
            data (dict): raw data loaded from JSON.
            targets (dict): dictionary that maps micro-architecture names
                to ``MicroArchitecture`` objects
        """
        values = data[name]

        # Get direct parents of target
        parent_names = values['from']
        if isinstance(parent_names, six.string_types):
            parent_names = [parent_names]
        if parent_names is None:
            parent_names = []
        for p in parent_names:
            # Recursively fill parents so they exist before we add them
            if p in targets:
                continue
            fill_target_from_dict(p, data, targets)
        parents = [targets.get(p) for p in parent_names]

        # Get target vendor
        vendor = values.get('vendor', None)
        if not vendor:
            vendor = parents[0].vendor

        features = set(values['features'])
        compilers = values.get('compilers', {})
        generation = values.get('generation', 0)

        targets[name] = MicroArchitecture(
            name, parents, vendor, features, compilers, generation
        )

    targets = {}
    data = _targets_json['microarchitectures']
    for name in data:
        if name in targets:
            # name was already brought in as ancestor to a target
            continue
        fill_target_from_dict(name, data, targets)

    # Add the host platform if not present
    host_platform = platform.machine()
    targets.setdefault(host_platform, generic_microarchitecture(host_platform))

    return targets


#: Dictionary of known micro-architectures
targets = LazyDictionary(_known_microarchitectures)


def supported_target_names():
    return targets.keys()


def _create_cpuinfo_dict():
    """Returns a dictionary with information on the host CPU."""
    dict_factory = {
        'Linux': _create_dict_from_proc,
        'Darwin': _create_dict_from_sysctl
    }
    return dict_factory[platform.system()]()


def _create_dict_from_proc():
    # Initialize cpuinfo from file
    cpuinfo = {}
    try:
        with open('/proc/cpuinfo') as file:
            for line in file:
                if line.strip():
                    key, _, value = line.partition(':')
                    cpuinfo[key.strip()] = value.strip()
    except IOError:
        return None
    return cpuinfo


def _create_dict_from_sysctl():

    def check_output(args):
        if sys.version_info >= (3, 0):
            return subprocess.run(
                args, check=True, stdout=subprocess.PIPE).stdout  # nopyqver
        else:
            return subprocess.check_output(args)  # nopyqver

    cpuinfo = {}
    try:
        cpuinfo['vendor_id'] = check_output(
            ['sysctl', '-n', 'machdep.cpu.vendor']
        ).strip()
        cpuinfo['flags'] = check_output(
            ['sysctl', '-n', 'machdep.cpu.features']
        ).strip().lower()
        cpuinfo['flags'] += ' ' + check_output(
            ['sysctl', '-n', 'machdep.cpu.leaf7_features']
        ).strip().lower()
        cpuinfo['model'] = check_output(
            ['sysctl', '-n', 'machdep.cpu.model']
        ).strip()
        cpuinfo['model name'] = check_output(
            ['sysctl', '-n', 'machdep.cpu.brand_string']
        ).strip()

        # Super hacky way to deal with slight representation differences
        # Would be better to somehow consider these "identical"
        if 'sse4.1' in cpuinfo['flags']:
            cpuinfo['flags'] += ' sse4_1'
        if 'sse4.2' in cpuinfo['flags']:
            cpuinfo['flags'] += ' sse4_2'
        if 'avx1.0' in cpuinfo['flags']:
            cpuinfo['flags'] += ' avx'
    except Exception:
        pass
    return cpuinfo


def detect_host():
    """Detects the host micro-architecture and returns it."""
    cpuinfo = _create_cpuinfo_dict()
    basename = platform.machine()

    if basename == 'x86_64':
        tester = _get_x86_target_tester(cpuinfo, basename)
    elif basename in ('ppc64', 'ppc64le'):
        tester = _get_power_target_tester(cpuinfo, basename)
    else:
        return generic_microarchitecture(basename)

    # Reverse sort of the depth for the inheritance tree among only targets we
    # can use. This gets the newest target we satisfy.
    return sorted(list(filter(tester, targets.values())),
                  key=lambda t: len(t.ancestors), reverse=True)[0]


def _get_power_target_tester(cpuinfo, basename):
    """Returns a tester function for the Power architecture."""
    generation_match = re.search(r'POWER(\d+)', cpuinfo.get('cpu', ''))
    generation = int(generation_match.group(1))

    def can_use(target):
        # We can use a target if it descends from our machine type and our
        # generation (9 for POWER9, etc) is at least its generation.
        return ((target == targets[basename] or
                 targets[basename] in target.ancestors) and
                target.generation <= generation)

    return can_use


def _get_x86_target_tester(cpuinfo, basename):
    """Returns a tester function for the x86_64 architecture."""
    vendor = cpuinfo.get('vendor_id', 'generic')
    features = set(cpuinfo.get('flags', '').split())

    def can_use(target):
        # We can use a target if it descends from our machine type, is from our
        # vendor, and we have all of its features
        return ((target == targets[basename]
                 or targets[basename] in target.ancestors) and
                (target.vendor == vendor or target.vendor == 'generic') and
                target.features.issubset(features))

    return can_use
