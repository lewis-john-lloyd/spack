##############################################################################
# Copyright (c) 2013-2016, Lawrence Livermore National Security, LLC.
# Produced at the Lawrence Livermore National Laboratory.
#
# This file is part of Spack.
# Created by Todd Gamblin, tgamblin@llnl.gov, All rights reserved.
# LLNL-CODE-647188
#
# For details, see https://github.com/llnl/spack
# Please also see the LICENSE file for our notice and the LGPL.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License (as
# published by the Free Software Foundation) version 2.1, February 1999.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the IMPLIED WARRANTY OF
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the terms and
# conditions of the GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
##############################################################################
import platform
import re
import subprocess
import sys
import os
import json
from ordereddict_backport import OrderedDict

class Target(object):
    def __init__(self, name, parents, vendor, features, compilers, generation=0):
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
    def __repr__(self):
        return self.name


def targets_from_json():
    filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'targets.json')
    with open(filename, 'r') as f:
        data = json.loads(f.read(), object_pairs_hook=OrderedDict)
    
    targets = OrderedDict()
    for name, values in data.items():
        # Get direct parents of target
        parents = values['from']
        if isinstance(parents, basestring):
            parents = [parents]
        if parents is None:
            parents = []
        parents = [targets.get(p) for p in parents]

        # Get target vendor
        vendor = values.get('vendor', None)
        if not vendor:
            vendor = parents[0].vendor

        features = set(values['features'])
        compilers = values.get('compilers', {})
        generation = values.get('generation', 0)
        targets[name] = Target(name, parents, vendor, features, compilers, generation)

    return targets


# Instantiated DAG forest of processor dependencies
targets = targets_from_json()


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
        return subprocess.run(args, check=True, stdout=PIPE).stdout # nopyqver
    else:
        return subprocess.check_output(args) # nopyqver


def create_dict_from_sysctl():
    cpuinfo = {}
    try:
        cpuinfo['vendor_id'] = check_output(['sysctl', '-n',
                                  'machdep.cpu.vendor']).strip()
        cpuinfo['flags'] = check_output(['sysctl', '-n',
                                 'machdep.cpu.features']).strip().lower()
        cpuinfo['flags'] += ' ' + check_output(['sysctl', '-n',
                                 'machdep.cpu.leaf7_features']).strip().lower()
        cpuinfo['model'] = check_output(['sysctl', '-n',
                                         'machdep.cpu.model']).strip()
        cpuinfo['model name'] = check_output(['sysctl', '-n',
                                          'machdep.cpu.brand_string']).strip()

        # Super hacky way to deal with slight representation differences
        # Would be better to somehow consider these "identical"
        if 'sse4.1' in cpuinfo['flags']:
            cpuinfo['flags'] += ' sse4_1'
        if 'sse4.2' in cpuinfo['flags']:
            cpuinfo['flags'] += ' sse4_2'
        if 'avx1.0' in cpuinfo['flags']:
            cpuinfo['flags'] += ' avx'
    except:
        pass
    return cpuinfo


def get_cpu_name():
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
                  key=lambda t: len(t.ancestors), reverse=True)[0].name


def get_power_target_tester(cpuinfo, basename):
    generation = int(re.search(r'POWER(\d+)', cpuinfo.get('cpu', '')).matches(1))

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
