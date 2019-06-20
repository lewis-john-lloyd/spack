# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import platform
import re
import subprocess
import sys
import os
import json

import six

from ordereddict_backport import OrderedDict


class Target(object):
    def __init__(
            self, name, parents, vendor, features, compilers, generation=0
    ):
        self.name = name
        self.ancestors = parents
        for parent in parents:
            self.ancestors.extend(
                list(filter(lambda a: a not in self.ancestors,
                            parent.ancestors))
            )
        self.vendor = vendor
        self.features = features
        self.compilers = compilers
        self.generation = generation

    def _ensure_strictly_orderable(self, other):
        if not (self in other.ancestors or other in self.ancestors):
            msg = "There is no ordering relationship between targets "
            msg += "%s and %s." % (self.name, other.name)
            raise TypeError(msg)

    def __eq__(self, other):
        return (self.name == other.name and
                self.vendor == other.vendor and
                self.features == other.features and
                self.ancestors == other.ancestors and
                self.compilers == other.compilers and
                self.generation == other.generation)

    def __str__(self):
        return self.name

    @property
    def architecture_family(self):
        """Returns the architecture family a given target belongs to"""
        # TODO: memoize this function
        roots = [x for x in [self] + self.ancestors if not x.ancestors]
        msg = "a target is expected to belong to just one architecture family"
        msg += "[found {0}]".format(', '.join(str(x) for x in roots))
        assert len(roots) == 1, msg

        return roots.pop()


_generic_values = {'from': None,
                   'vendor': 'generic',
                   'features': []}


def get_targets_from_json():
    # TODO: Simplify this logic using object_pairs_hook to OrderedDict
    # when we stop supporting python2.6
    this_dir = os.path.dirname(os.path.abspath(__file__))
    filename = os.path.join(this_dir, 'targets.json')
    with open(filename, 'r') as f:
        data = json.load(f)

    if platform.machine() not in data:
        data[platform.machine()] = _generic_values

    targets = OrderedDict()
    for name in data:
        if name in targets:
            # name was already brought in as ancestor to a target
            continue
        fill_target_from_dict(name, data, targets)
    return targets


def fill_target_from_dict(name, data, targets):
    values = data[name]

    # Get direct parents of target
    parents = values['from']
    if isinstance(parents, six.string_types):
        parents = [parents]
    if parents is None:
        parents = []
    for p in [p for p in parents if p not in targets]:
        # Recursively fill parents so they exist before we add them
        fill_target_from_dict(p, data, targets)
    parents = [targets.get(p) for p in parents]

    # Get target vendor
    vendor = values.get('vendor', None)
    if not vendor:
        vendor = parents[0].vendor

    features = set(values['features'])
    compilers = values.get('compilers', {})
    generation = values.get('generation', 0)

    targets[name] = Target(
        name, parents, vendor, features, compilers, generation
    )


targets = get_targets_from_json()


def supported_target_names():
    return targets.keys()


def create_cpuinfo_dict():
    system = platform.system()
    if system == 'Linux':
        return create_dict_from_proc()
    elif system == 'Darwin':
        return create_dict_from_sysctl()


def create_dict_from_proc():
    # Initialize cpuinfo from file
    cpuinfo = {}
    try:
        with open('/proc/cpuinfo') as file:
            text = file.readlines()
            for line in text:
                if line.strip():
                    key, _, value = line.partition(':')
                    cpuinfo[key.strip()] = value.strip()
    except IOError:
        return None
    return cpuinfo


def check_output(args):
    if sys.version_info >= (3, 0):
        return subprocess.run(
            args, check=True, stdout=subprocess.PIPE).stdout  # nopyqver
    else:
        return subprocess.check_output(args)  # nopyqver


def create_dict_from_sysctl():
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


def get_cpu():
    cpuinfo = create_cpuinfo_dict()
    basename = platform.machine()

    if basename == 'x86_64':
        tester = get_x86_target_tester(cpuinfo, basename)
    elif basename in ('ppc64', 'ppc64le'):
        tester = get_power_target_tester(cpuinfo, basename)
    else:
        return basename

    # Reverse sort of the depth for the inheritance tree among only targets we
    # can use. This gets the newest target we satisfy.
    return sorted(list(filter(tester, targets.values())),
                  key=lambda t: len(t.ancestors), reverse=True)[0]


def get_power_target_tester(cpuinfo, basename):
    generation = int(
        re.search(r'POWER(\d+)', cpuinfo.get('cpu', '')).matches(1)
    )

    def can_use(target):
        # We can use a target if it descends from our machine type and our
        # generation (9 for POWER9, etc) is at least its generation.
        return ((target == targets[basename] or
                 targets[basename] in target.ancestors) and
                target.generation <= generation)

    return can_use


def get_x86_target_tester(cpuinfo, basename):
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
