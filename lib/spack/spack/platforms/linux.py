# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import platform
from spack.architecture import Platform, Target
from spack.operating_systems.linux_distro import LinuxDistro

from spack.util.executable import which
import re

_known_intel_names_by_number = {0x06: 'presler',
                                0x16: 'merom',
                                0x0f: 'merom',
                                0x1d: 'penryn',
                                0x17: 'penryn',
                                0x2e: 'nehalem',
                                0x1a: 'nehalem',
                                0x1e: 'nehalem',
                                0x2f: 'westmere',
                                0x2c: 'westmere',
                                0x25: 'westmere',
                                0x2d: 'sandybridge',
                                0x2a: 'sandybridge',
                                0x3a: 'ivybridge'}

class Linux(Platform):
    priority    = 90

    def __init__(self):
        super(Linux, self).__init__('linux')

        # Add "basic" architectures
        self.add_target('x86_64', Target('x86_64'))
        self.add_target('ppc64le', Target('ppc64le'))
        self.add_target('ppc64', Target('ppc64'))

        # Add Intel architectures
        self.add_target('haswell', Target('haswell'))
        self.add_target('broadwell', Target('broadwell'))
        self.add_target('ivybridge', Target('ivybridge'))
        self.add_target('sandybridge', Target('sandybridge'))
        self.add_target('mic-knl', Target('mic-knl'))

        # Add IBM architectures
        self.add_target('power7', Target('power7'))
        self.add_target('power8', Target('power8'))
        self.add_target('power9', Target('power9'))

        # Eternal TODO: Add more architectures as needed.

        # Get specific default
        self.default = self.get_cpu_name()
        self.front_end = self.default
        self.back_end = self.default

        if not self.default:
            # Fall back on more general name.
            # This will likely fall in "basic" architectures list
            self.default = platform.machine()
            self.front_end = self.default
            self.back_end = self.default

        if self.default not in self.targets:
            self.add_target(self.default, Target(self.default))

        linux_dist = LinuxDistro()
        self.default_os = str(linux_dist)
        self.front_os = self.default_os
        self.back_os = self.default_os
        self.add_operating_system(str(linux_dist), linux_dist)

    @classmethod
    def detect(cls):
        return 'linux' in platform.system().lower()

    def get_cpu_name(self):
        name = self.get_cpu_name_from_cpuinfo()
        if not name:
            name = self.get_cpu_name_from_dmesg()
        return name

    def get_cpu_name_from_dmesg(self):
        # Parse/Grep output of dmesg for processor name
        name = ''
        changed = 0
        text = ''

        try:
            dmesg = which('dmesg')
            text = dmesg(output=str, error=str)
        except:
            return ''

        if 'Broadwell' in text:
            changed += 1
            name = 'broadwell'
        if 'Haswell' in text:
            changed += 1
            name = 'haswell'
        if 'Ivy Bridge' in text:
            changed += 1
            name = 'ivybridge'
        if 'Sandy Bridge' in text:
            changed += 1
            name = 'sandybridge'
        if 'Atom' in text:
            changed += 1
            name = 'atom'
        if 'Power9' in text or 'POWER9' in text:
            changed += 1
            name = 'power9'
        if 'Power8' in text or 'POWER8' in text:
            changed += 1
            name = 'power8'
        if 'Power7' in text or 'POWER7' in text:
            changed += 1
            name = 'power7'

        if changed == 1:
            return name
        else:
            return ''

    def get_cpu_name_from_cpuinfo(self):
        # TODO: Elsewhere create dict of codenames (targets) and flag sets.
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
            return ''

        if 'vendor_id' in cpuinfo and cpuinfo['vendor_id'] == 'GenuineIntel':
            if 'model name' not in cpuinfo or 'model' not in cpuinfo:
                # We don't have the information we need to determine the
                # microarchitecture name
                return ''
            return self.get_intel_cpu_name(cpuinfo['model name'],
                                           int(cpuinfo['model']))
        elif 'vendor_id' in cpuinfo and cpuinfo['vendor_id'] == 'AuthenticAMD':
            if 'model name' not in cpuinfo:
                # We don't have the information we need to determine the
                # microarchitecture name
                return ''
            return self.get_amd_cpu_name(cpuinfo['model name'])
        elif 'cpu' in cpuinfo and 'POWER' in cpuinfo['cpu']:
            return self.get_ibm_cpu_name(cpuinfo['cpu'])
        else:
            return ''

    def get_ibm_cpu_name(self, cpu):
        if 'POWER9' in cpu:
            return 'power9'
        elif 'POWER8' in cpu:
            return 'power8'
        elif 'POWER7' in cpu:
            return 'power7'
        else:
            return ''

    def get_intel_cpu_name(self, model, number):
        if number in _known_intel_names_by_number:
            return _known_intel_names_by_number[number]
        if 'Xeon' in model:
            if 'E3-' in model or 'E5-' in model or 'E7-' in model:
                if 'v4' in model:
                    return 'broadwell'
                elif 'v3' in model:
                    return 'haswell'
                elif 'v2' in model:
                    return 'ivybridge'
                elif 'E3-' in model or 'E5-' in model:
                    return 'sandybridge'
                elif 'E7-' in model:
                    return 'westmere'
                else:
                    return ''
            elif 'D-1' in model:
                return 'broadwell'
            elif 'Phi' in model:
                # KNC was a coprocessor, would not show up here.
                if '72' not in model:
                    # All knl we know of have model numbers beginning '72'.
                    return ''
                return 'knl'
            else:
                # We won't go back all the way to Xeon 3000, 5000, etc series.
                # Processors with those models should be in the dict.
                return ''
        elif 'Core' in model:
            # Some of these patterns don't work for older models.
            # Fortunately, those models are in the known names dict
            # and will not fall into this code.
            if 'i3-5' in model or 'i5-5' in model or 'i7-5' in model:
                if '960X' in model or '930K' in model or '820K' in model:
                    return 'haswell'
                else:
                    return 'broadwell'
            elif 'i3-4' in model or 'i5-4' in model or 'i7-4' in model:
                if '960X' in model or '930K' in model or '820K' in model:
                    return 'ivybridge'
                else:
                    return 'haswell'
            elif 'i3-3' in model or 'i5-3' in model or 'i7-3' in model:
                if '960X' in model or '930K' in model or '820K' in model:
                    return 'sandybridge'
                else:
                    return 'ivybridge'
            elif 'i3-2' in model or 'i5-2' in model or 'i7-2' in model:
                return 'sandybridge'
            elif 'M-5' in model:
                return 'broadwell'
            else:
                return ''
        elif 'Celeron' in model:
            return ''
        elif 'Pentium' in model:
            return ''
        elif 'Atom' in model:
            # Known bug: some atom processors will incorrectly return 'Core'
            # Solution: If you have one of those systems, patch it and reboot.
            return 'atom'
        else:
            return ''
