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
from spack.util.executable import which
import platform
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
                                0x3a: 'ivybridge',
                                0x3e: 'ivybridge',
                                0x3c: 'haswell',
                                0x3f: 'haswell',
                                0x45: 'haswell',
                                0x46: 'haswell',
                                0x3d: 'broadwell',
                                0x4f: 'broadwell',
                                0x1c: 'atom',
                                0x26: 'atom',
                                0x36: 'atom',
                                0x4d: 'atom'
                                }

def create_dict_from_cpuinfo():
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

def get_cpu_name():
    name = get_cpu_name_from_cpuinfo()
    return name if name else platform.machine()

def get_cpu_name_from_cpuinfo():
    # TODO: Elsewhere create dict of codenames (targets) and flag sets.
    # Return cpu name or an empty string if one cannot be determined.
    cpuinfo = create_dict_from_cpuinfo()
    if not cpuinfo:
        return ''

    if 'vendor_id' in cpuinfo and cpuinfo['vendor_id'] == 'GenuineIntel':
        if 'model name' not in cpuinfo or 'model' not in cpuinfo:
            # We don't have the information we need to determine the
            # microarchitecture name
            return ''
        return get_intel_cpu_name(cpuinfo['model name'],
                                       int(cpuinfo['model']))
    elif 'vendor_id' in cpuinfo and cpuinfo['vendor_id'] == 'AuthenticAMD':
        if 'model name' not in cpuinfo:
            # We don't have the information we need to determine the
            # microarchitecture name
            return ''
        return get_amd_cpu_name(cpuinfo['model name'])
    elif 'cpu' in cpuinfo and 'POWER' in cpuinfo['cpu']:
        return get_ibm_cpu_name(cpuinfo['cpu'])
    else:
        return ''

def get_ibm_cpu_name(cpu):
    power_pattern = re.compile('POWER(\d+)')
    power_match = power_pattern.search(cpu)
    if power_match:
        return 'power' + power_match.group(1)
    else:
        return ''

def get_intel_cpu_name(model, number):
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
