# Copyright 2013-2019 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import pytest

import contextlib
import os.path

import llnl.util.cpu
import spack.paths

# This is needed to check that with repr we could create equivalent objects
from llnl.util.cpu import MicroArchitecture  # noqa


@pytest.fixture(params=[
    'linux-ubuntu18.04-broadwell',
    'linux-rhel7-broadwell',
    'linux-rhel7-skylake_avx512',
    'linux-rhel7-ivybridge',
    'linux-rhel7-haswell',
    'linux-rhel7-zen'
])
def expected_target(request, monkeypatch):
    platform, operating_system, target = request.param.split('-')

    # Monkeypatch for linux
    if platform == 'linux':
        monkeypatch.setattr(llnl.util.cpu.platform, 'system', lambda: 'Linux')

        @contextlib.contextmanager
        def _open(not_used_arg):
            filename = os.path.join(
                spack.paths.test_path, 'data', 'targets', request.param
            )
            with open(filename) as f:
                yield f

        monkeypatch.setattr(llnl.util.cpu, 'open', _open, raising=False)

    # TODO: need to code the fixture logic for Darwin

    return llnl.util.cpu.targets[target]


@pytest.fixture(params=[x for x in llnl.util.cpu.targets])
def supported_target(request):
    return request.param


def test_target_detection(expected_target):
    detected_target = llnl.util.cpu.detect_host()
    assert detected_target == expected_target


def test_no_dashes_in_target_names(supported_target):
    assert '-' not in supported_target


def test_str_conversion(supported_target):
    assert supported_target == str(llnl.util.cpu.targets[supported_target])


def test_repr_conversion(supported_target):
    target = llnl.util.cpu.targets[supported_target]
    assert eval(repr(target)) == target
