# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import collections
import functools
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


compilers_schema = {
    'type': 'object',
    'properties': {
        'versions': {'type': 'string'},
        'name': {'type': 'string'}
    },
    'required': ['versions']
}


properties = {
    'microarchitectures': {
        'type': 'object',
        'patternProperties': {
            r'([\w]*)': {
                'type': 'object',
                'properties': {
                    'from': {
                        'anyOf': [
                            # More than one parent
                            {'type': 'array', 'items': {'type': 'string'}},
                            # Exactly one parent
                            {'type': 'string'},
                            # No parent
                            {'type': 'null'}
                        ]
                    },
                    'vendor': {
                        'type': 'string'
                    },
                    'features': {
                        'type': 'array',
                        'items': {'type': 'string'}
                    },
                    'compilers': {
                        'type': 'object',
                        'patternProperties': {
                            r'([\w]*)': {
                                'anyOf': [
                                    compilers_schema,
                                    {
                                        'type': 'array',
                                        'items': compilers_schema
                                    }
                                ]
                            }
                        }
                    }
                },
                'required': ['from', 'vendor', 'features']
            }
        }
    },
    'feature_aliases': {
        'type': 'object',
        'patternProperties': {
            r'([\w]*)': {
                'type': 'object',
                'properties': {},
                'additionalProperties': False
            }
        },

    }
}

schema = {
    '$schema': 'http://json-schema.org/schema#',
    'title': 'Schema for microarchitecture definitions and feature aliases',
    'type': 'object',
    'additionalProperties': False,
    'properties': properties,
}


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


def alias_predicate(predicate_schema):
    """Decorator to register a predicate that can be used to define
    feature aliases.

    Args:
        predicate_schema (dict): schema to be enforced in targets.json
            for the predicate
    """
    def decorator(func):
        name = func.__name__

        # Check we didn't register anything else with the same name
        if name in _feature_alias_predicate:
            msg = 'the alias predicate "{0}" already exists'.format(name)
            raise KeyError(msg)

        # Update the overall schema
        alias_schema = properties['feature_aliases']['patternProperties']
        alias_schema[r'([\w]*)']['properties'].update(
            {name: predicate_schema}
        )
        # Register the predicate
        _feature_alias_predicate[name] = func

        return func
    return decorator


@alias_predicate(predicate_schema={'type': 'string'})
def reason(motivation_for_the_alias):
    """This predicate returns always True and it's there to allow writing
    a documentation string in the JSON file to explain why an alias is needed.
    """
    return lambda x: True


@alias_predicate(predicate_schema={
    'type': 'array',
    'items': {'type': 'string'}
})
def any_of(list_of_features):
    """Returns a predicate that is True if any of the feature in the
    list is in the microarchitecture being tested, False otherwise.
    """
    def _impl(microarchitecture):
        return any(x in microarchitecture for x in list_of_features)
    return _impl


@alias_predicate(predicate_schema={
    'type': 'array',
    'items': {'type': 'string'}
})
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
        if self == other:
            return

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

    def to_dict(self, return_list_of_items=False):
        """Returns a dictionary representation of this object.

        Args:
            return_list_of_items (bool): if True returns an ordered list of
                items instead of the dictionary
        """
        list_of_items = [
            ('name', str(self.name)),
            ('vendor', str(self.vendor)),
            ('features', sorted(
                str(x) for x in self.features
            )),
            ('generation', self.generation),
            ('parents', [str(x) for x in self.parents])
        ]
        if return_list_of_items:
            return list_of_items

        return dict(list_of_items)


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

        vendor = values['vendor']
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


#: Mapping from operating systems to chain of commands
#: to obtain a dictionary of raw info on the current cpu
info_factory = collections.defaultdict(list)

#: Mapping from micro-architecture families (x86_64, ppc64le, etc.) to
#: functions checking the compatibility of the host with a given target
compatibility_checks = {}


def info_dict(operating_system):
    """Decorator to mark functions that are meant to return raw info on
    the current cpu.

    Args:
        operating_system (str or tuple): operating system for which the marked
            function is a viable factory of raw info dictionaries.
    """
    def decorator(factory):
        info_factory[operating_system].append(factory)

        @functools.wraps(factory)
        def _impl():
            info = factory()

            # Check that info contains a few mandatory fields
            msg = 'field "{0}" is missing from raw info dictionary'
            assert 'vendor_id' in info, msg.format('vendor_id')
            assert 'flags' in info, msg.format('flags')
            assert 'model' in info, msg.format('model')
            assert 'model_name' in info, msg.format('model_name')

            return info

        return _impl

    return decorator


@info_dict(operating_system='Linux')
def proc_cpuinfo():
    """Returns a raw info dictionary by parsing the first entry of
    ``/proc/cpuinfo``
    """
    info = {}
    with open('/proc/cpuinfo') as file:
        for line in file:
            key, separator, value = line.partition(':')

            # If there's no separator and info was already populated
            # according to what's written here:
            #
            # http://www.linfo.org/proc_cpuinfo.html
            #
            # we are on a blank line separating two cpus. Exit early as
            # we want to read just the first entry in /proc/cpuinfo
            if separator != ':' and info:
                break

            info[key.strip()] = value.strip()
    return info


def check_output(args):
    if sys.version_info[:2] == (2, 6):
        return subprocess.run(
            args, check=True, stdout=subprocess.PIPE).stdout  # nopyqver
    else:
        return subprocess.check_output(args)  # nopyqver


@info_dict(operating_system='Darwin')
def sysctl():
    """Returns a raw info dictionary parsing the output of sysctl."""

    info = {}
    info['vendor_id'] = check_output(
        ['sysctl', '-n', 'machdep.cpu.vendor']
    ).strip()
    info['flags'] = check_output(
        ['sysctl', '-n', 'machdep.cpu.features']
    ).strip().lower()
    info['flags'] += ' ' + check_output(
        ['sysctl', '-n', 'machdep.cpu.leaf7_features']
    ).strip().lower()
    info['model'] = check_output(
        ['sysctl', '-n', 'machdep.cpu.model']
    ).strip()
    info['model name'] = check_output(
        ['sysctl', '-n', 'machdep.cpu.brand_string']
    ).strip()

    # Super hacky way to deal with slight representation differences
    # Would be better to somehow consider these "identical"
    if 'sse4.1' in info['flags']:
        info['flags'] += ' sse4_1'
    if 'sse4.2' in info['flags']:
        info['flags'] += ' sse4_2'
    if 'avx1.0' in info['flags']:
        info['flags'] += ' avx'

    return info


def raw_info_dictionary():
    """Returns a dictionary with information on the cpu of the current host.

    This function calls all the viable factories one after the other until
    there's one that is able to produce the requested information.
    """
    info = {}
    for factory in info_factory[platform.system()]:
        try:
            info = factory()
        except Exception:
            pass

        if info:
            break

    return info


def compatible_microarchitectures(info):
    """Returns an unordered list of known micro-architectures that are
    compatible with the info dictionary passed as argument.

    Args:
        info (dict): dictionary containing information on the host cpu
    """
    architecture_family = platform.machine()
    # If a tester is not registered, be conservative and assume no known
    # target is compatible with the host
    tester = compatibility_checks.get(architecture_family, lambda x, y: False)
    return [x for x in targets.values() if tester(info, x)] or \
           [generic_microarchitecture(architecture_family)]


def detect_host():
    """Detects the host micro-architecture and returns it."""
    # Retrieve a dictionary with raw information on the host's cpu
    info = raw_info_dictionary()

    # Get a list of possible candidates for this micro-architecture
    candidates = compatible_microarchitectures(info)

    # Reverse sort of the depth for the inheritance tree among only targets we
    # can use. This gets the newest target we satisfy.
    return sorted(candidates, key=lambda t: len(t.ancestors), reverse=True)[0]


def compatibility_check(architecture_family):
    """Decorator to register a function as a proper compatibility check.

    A compatibility check function takes the raw info dictionary as a first
    argument and an arbitrary target as the second argument. It returns True
    if the target is compatible with the info dictionary, False otherwise.

    Args:
        architecture_family (str or tuple): architecture family for which
            this test can be used, e.g. x86_64 or ppc64le etc.
    """
    # Turn the argument into something iterable
    if isinstance(architecture_family, six.string_types):
        architecture_family = (architecture_family,)

    def decorator(func):
        # TODO: on removal of Python 2.6 support this can be re-written as
        # TODO: an update +  a dict comprehension
        for arch_family in architecture_family:
            compatibility_checks[arch_family] = func

        return func

    return decorator


@compatibility_check(architecture_family=('ppc64le', 'ppc64'))
def compatibility_check_for_power(info, target):
    basename = platform.machine()
    generation_match = re.search(r'POWER(\d+)', info.get('cpu', ''))
    generation = int(generation_match.group(1))

    # We can use a target if it descends from our machine type and our
    # generation (9 for POWER9, etc) is at least its generation.
    arch_root = targets[basename]
    return (target == arch_root or arch_root in target.ancestors) \
        and target.generation <= generation


@compatibility_check(architecture_family='x86_64')
def compatibility_check_for_x86_64(info, target):
    basename = 'x86_64'
    vendor = info.get('vendor_id', 'generic')
    features = set(info.get('flags', '').split())

    # We can use a target if it descends from our machine type, is from our
    # vendor, and we have all of its features
    arch_root = targets[basename]
    return (target == arch_root or arch_root in target.ancestors) \
        and (target.vendor == vendor or target.vendor == 'generic') \
        and target.features.issubset(features)
