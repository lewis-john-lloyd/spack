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

_known_intel_names_by_number = {
    0x06: 'presler',
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
    0x47: 'broadwell',
    0x4f: 'broadwell',
    0x56: 'broadwell',
    0x1c: 'atom',
    0x26: 'atom',
    0x36: 'atom',
    0x4d: 'atom'
    }

# Tuple of name, flags added, flags removed (default [])
_intel_processors = [
    ('i686', []),
    ('pentium2', ['mmx']),
    ('pentium3', ['sse']),
    ('pentium4', ['sse2']),
    ('prescott', ['sse3']),
    ('x86_64', ['lm'], ['mmx', 'sse', 'sse2', 'sse3']),
    ('nocona', ['sse3'])
    ('core2', ['mmx', 'sse', 'sse2', 'ssse3'], ['sse3']),
    ('nehalem', ['sse4_1', 'sse4_2', 'popcnt']),
    ('westmere', ['aes', 'pclmulqdq']),
    ('sandybridge', ['avx']),
    ('ivybridge', ['fsgsbase', 'rdrand', 'f16c']),
    ('haswell', ['movbe', 'avx2', 'fma', 'bmi1', 'bmi2']),
    ('broadwell', ['rdseed', 'adx']),
    ('skylake', ['xsavec', 'xsaves'])
    ]

# We will need to build on these and combine with names when intel releases
# further avx512 processors.
# _intel_avx12 = ['avx512f', 'avx512cd']

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

def create_dict_from_sysctl():
    cpuinfo = {}
    try:
        cpuinfo['vendor_id'] = subprocess.check_output(['sysctl', '-n',
                                  'machdep.cpu.vendor']).strip()
        cpuinfo['flags'] = subprocess.check_output(['sysctl', '-n',
                                 'machdep.cpu.features']).strip().lower()
        cpuinfo['model'] = subprocess.check_output(['sysctl', '-n',
                                         'machdep.cpu.model']).strip()
        cpuinfo['model name'] = subprocess.check_output(['sysctl', '-n',
                                          'machdep.cpu.brand_string']).strip()
    except:
        pass
    return cpuinfo

def get_cpu_name():
    name = get_cpu_name_helper(platform.system())
    return name if name else platform.machine()

def get_cpu_name_helper(system):
    # TODO: Elsewhere create dict of codenames (targets) and flag sets.
    # Return cpu name or an empty string if one cannot be determined.
    cpuinfo = {}
    if system == 'Linux':
        cpuinfo = create_dict_from_cpuinfo()
    elif system == 'Darwin':
        cpuinfo = create_dict_from_sysctl()
    if not cpuinfo:
        return ''

    if 'vendor_id' in cpuinfo and cpuinfo['vendor_id'] == 'GenuineIntel':
        if 'model' not in cpuinfo and ('model name' not in cpuinfo or
                                            'flags' not in cpuinfo):
            # We don't have the information we need to determine the
            # microarchitecture name
            return ''
        return get_intel_cpu_name(cpuinfo)
    elif 'vendor_id' in cpuinfo and cpuinfo['vendor_id'] == 'AuthenticAMD':
        if 'model name' not in cpuinfo:
            # We don't have the information we need to determine the
            # microarchitecture name
            return ''
        return get_amd_cpu_name(cpuinfo)
    elif 'cpu' in cpuinfo and 'POWER' in cpuinfo['cpu']:
        return get_ibm_cpu_name(cpuinfo['cpu'])
    else:
        return ''

def get_ibm_cpu_name(cpu):
    power_pattern = re.compile('POWER(\d+)')
    power_match = power_pattern.search(cpu)
    if power_match:
        if 'le' in platform.machine():
            return 'power' + power_match.group(1) + 'le'
        return 'power' + power_match.group(1)
    else:
        return ''

def get_intel_cpu_name(cpuinfo):
#    model_number = int(cpuinfo['model'])
#    if model_number in _known_intel_names_by_number:
#        return _known_intel_names_by_number[model_number]
    model_name = cpuinfo['model name']
    if 'Atom' in model_name:
        return 'atom'
    elif 'Quark' in model_name:
        return 'quark'
    elif 'Xeon' in model_name and 'Phi' in model_name:
        # This is hacky and needs to be extended for newer avx512 chips
        return 'knl'
    else:
        ret = ''
        flag_list = cpuinfo['flags'].split()
        proc_flags = []
        for entry in _intel_processors:
            try:
                proc, flags_added, flags_removed = entry
            except ValueError:
                proc, flags_added = entry
                flags_removed = []
            proc_flags = filter(lambda x: x not in flags_removed, proc_flags)
            proc_flags.extend(flags_added)
            if all(f in flag_list for f in proc_flags):
                ret = proc
        return ret

def get_amd_cpu_name(cpuinfo):
    return ''
