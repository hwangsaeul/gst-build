#!/usr/bin/env python3

import argparse
import contextlib
import json
import os
import platform
import re
import site
import shutil
import subprocess
import sys
import tempfile
import pathlib

from distutils.sysconfig import get_python_lib
from distutils.util import strtobool

from scripts.common import get_meson
from scripts.common import git
from scripts.common import win32_get_short_path_name

SCRIPTDIR = os.path.dirname(os.path.realpath(__file__))
PREFIX_DIR = os.path.join(SCRIPTDIR, 'prefix')
# Use '_build' as the builddir instead of 'build'
DEFAULT_BUILDDIR = os.path.join(SCRIPTDIR, 'build')
if not os.path.exists(DEFAULT_BUILDDIR):
    DEFAULT_BUILDDIR = os.path.join(SCRIPTDIR, '_build')


def listify(o):
    if isinstance(o, str):
        return [o]
    if isinstance(o, list):
        return o
    raise AssertionError('Object {!r} must be a string or a list'.format(o))

def stringify(o):
    if isinstance(o, str):
        return o
    if isinstance(o, list):
        if len(o) == 1:
            return o[0]
        raise AssertionError('Did not expect object {!r} to have more than one element'.format(o))
    raise AssertionError('Object {!r} must be a string or a list'.format(o))

def prepend_env_var(env, var, value, sysroot):
    if value.startswith(sysroot):
        value = value[len(sysroot):]
    # Try not to exceed maximum length limits for env vars on Windows
    if os.name is 'nt':
        value = win32_get_short_path_name(value)
    env_val = env.get(var, '')
    val = os.pathsep + value + os.pathsep
    # Don't add the same value twice
    if val in env_val or env_val.startswith(value + os.pathsep):
        return
    env[var] = val + env_val
    env[var] = env[var].replace(os.pathsep + os.pathsep, os.pathsep).strip(os.pathsep)


def get_subprocess_env(options, gst_version):
    env = os.environ.copy()

    env["CURRENT_GST"] = os.path.normpath(SCRIPTDIR)
    env["GST_VALIDATE_SCENARIOS_PATH"] = os.path.normpath(
        "%s/subprojects/gst-devtools/validate/data/scenarios" % SCRIPTDIR)
    env["GST_VALIDATE_PLUGIN_PATH"] = os.path.normpath(
        "%s/subprojects/gst-devtools/validate/plugins" % options.builddir)
    env["GST_VALIDATE_APPS_DIR"] = os.path.normpath(
        "%s/subprojects/gst-editing-services/tests/validate" % SCRIPTDIR)
    prepend_env_var(env, "PATH", os.path.normpath(
        "%s/subprojects/gst-devtools/validate/tools" % options.builddir),
        options.sysroot)
    prepend_env_var(env, "PATH", os.path.join(SCRIPTDIR, 'meson'),
        options.sysroot)
    env["GST_VERSION"] = gst_version
    env["GST_ENV"] = 'gst-' + gst_version
    env["GST_PLUGIN_SYSTEM_PATH"] = ""
    env["GST_PLUGIN_SCANNER"] = os.path.normpath(
        "%s/subprojects/gstreamer/libs/gst/helpers/gst-plugin-scanner" % options.builddir)
    env["GST_PTP_HELPER"] = os.path.normpath(
        "%s/subprojects/gstreamer/libs/gst/helpers/gst-ptp-helper" % options.builddir)
    env["GST_REGISTRY"] = os.path.normpath(options.builddir + "/registry.dat")

    sharedlib_reg = re.compile(r'\.so|\.dylib|\.dll')
    typelib_reg = re.compile(r'.*\.typelib$')

    if os.name is 'nt':
        lib_path_envvar = 'PATH'
    elif platform.system() == 'Darwin':
        lib_path_envvar = 'DYLD_LIBRARY_PATH'
    else:
        lib_path_envvar = 'LD_LIBRARY_PATH'

    prepend_env_var(env, "GST_PLUGIN_PATH", os.path.join(SCRIPTDIR, 'subprojects',
                                                        'gst-python', 'plugin'),
                    options.sysroot)
    prepend_env_var(env, "GST_PLUGIN_PATH", os.path.join(PREFIX_DIR, 'lib',
                                                        'gstreamer-1.0'),
                    options.sysroot)
    prepend_env_var(env, "GST_PLUGIN_PATH", os.path.join(options.builddir, 'subprojects',
                                                         'libnice', 'gst'),
                    options.sysroot)
    prepend_env_var(env, "GST_VALIDATE_SCENARIOS_PATH",
                    os.path.join(PREFIX_DIR, 'share', 'gstreamer-1.0',
                                 'validate', 'scenarios'),
                    options.sysroot)
    prepend_env_var(env, "GI_TYPELIB_PATH", os.path.join(PREFIX_DIR, 'lib',
                                                         'lib', 'girepository-1.0'),
                    options.sysroot)
    prepend_env_var(env, "PKG_CONFIG_PATH", os.path.join(PREFIX_DIR, 'lib', 'pkgconfig'),
                    options.sysroot)

    # gst-indent
    prepend_env_var(env, "PATH", os.path.join(SCRIPTDIR, 'gstreamer', 'tools'),
                    options.sysroot)

    # Library and binary search paths
    prepend_env_var(env, "PATH", os.path.join(PREFIX_DIR, 'bin'),
                    options.sysroot)
    if lib_path_envvar != 'PATH':
        prepend_env_var(env, lib_path_envvar, os.path.join(PREFIX_DIR, 'lib'),
                        options.sysroot)
        prepend_env_var(env, lib_path_envvar, os.path.join(PREFIX_DIR, 'lib64'),
                        options.sysroot)
    elif 'QMAKE' in os.environ:
        # There's no RPATH on Windows, so we need to set PATH for the qt5 DLLs
        prepend_env_var(env, 'PATH', os.path.dirname(os.environ['QMAKE']),
                        options.sysroot)

    meson = get_meson()
    targets_s = subprocess.check_output(meson + ['introspect', options.builddir, '--targets'])
    targets = json.loads(targets_s.decode())
    paths = set()
    mono_paths = set()
    srcdir_path = pathlib.Path(options.srcdir)
    for target in targets:
        filenames = listify(target['filename'])
        for filename in filenames:
            root = os.path.dirname(filename)
            if srcdir_path / "subprojects/gst-devtools/validate/plugins" in (srcdir_path / root).parents:
                continue
            if filename.endswith('.dll'):
                mono_paths.add(os.path.join(options.builddir, root))
            if typelib_reg.search(filename):
                prepend_env_var(env, "GI_TYPELIB_PATH",
                                os.path.join(options.builddir, root),
                                options.sysroot)
            elif sharedlib_reg.search(filename):
                if not target['type'].startswith('shared'):
                    continue

                prepend_env_var(env, lib_path_envvar,
                                os.path.join(options.builddir, root),
                                options.sysroot)
            elif target['type'] == 'executable' and target['installed']:
                paths.add(os.path.join(options.builddir, root))

    with open(os.path.join(options.builddir, 'GstPluginsPath.json')) as f:
        for plugin_path in json.load(f):
            prepend_env_var(env, 'GST_PLUGIN_PATH', plugin_path,
                            options.sysroot)

    for p in paths:
        prepend_env_var(env, 'PATH', p, options.sysroot)

    if os.name != 'nt':
        for p in mono_paths:
            prepend_env_var(env, "MONO_PATH", p, options.sysroot)

    presets = set()
    encoding_targets = set()
    pkg_dirs = set()
    python_dirs = set(["%s/subprojects/gstreamer/libs/gst/helpers/" % options.srcdir])
    if '--installed' in subprocess.check_output(meson + ['introspect', '-h']).decode():
        installed_s = subprocess.check_output(meson + ['introspect', options.builddir, '--installed'])
        for path, installpath in json.loads(installed_s.decode()).items():
            installpath_parts = pathlib.Path(installpath).parts
            path_parts = pathlib.Path(path).parts

            # We want to add all python modules to the PYTHONPATH
            # in a manner consistent with the way they would be imported:
            # For example if the source path /home/meh/foo/bar.py
            # is to be installed in /usr/lib/python/site-packages/foo/bar.py,
            # we want to add /home/meh to the PYTHONPATH.
            # This will only work for projects where the paths to be installed
            # mirror the installed directory layout, for example if the path
            # is /home/meh/baz/bar.py and the install path is
            # /usr/lib/site-packages/foo/bar.py , we will not add anything
            # to PYTHONPATH, but the current approach works with pygobject
            # and gst-python at least.
            if 'site-packages' in installpath_parts:
                install_subpath = os.path.join(*installpath_parts[installpath_parts.index('site-packages') + 1:])
                if path.endswith(install_subpath):
                    python_dirs.add(path[:len (install_subpath) * -1])

            if path.endswith('.prs'):
                presets.add(os.path.dirname(path))
            elif path.endswith('.gep'):
                encoding_targets.add(
                    os.path.abspath(os.path.join(os.path.dirname(path), '..')))
            elif path.endswith('.pc'):
                # Is there a -uninstalled pc file for this file?
                uninstalled = "{0}-uninstalled.pc".format(path[:-3])
                if os.path.exists(uninstalled):
                    pkg_dirs.add(os.path.dirname(path))

            if path.endswith('gstomx.conf'):
                prepend_env_var(env, 'GST_OMX_CONFIG_DIR', os.path.dirname(path),
                                options.sysroot)

        for p in presets:
            prepend_env_var(env, 'GST_PRESET_PATH', p, options.sysroot)

        for t in encoding_targets:
            prepend_env_var(env, 'GST_ENCODING_TARGET_PATH', t, options.sysroot)

        for pkg_dir in pkg_dirs:
            prepend_env_var(env, "PKG_CONFIG_PATH", pkg_dir, options.sysroot)
    prepend_env_var(env, "PKG_CONFIG_PATH", os.path.join(options.builddir,
                                                         'subprojects',
                                                         'gst-plugins-good',
                                                         'pkgconfig'),
                    options.sysroot)

    for python_dir in python_dirs:
        prepend_env_var(env, 'PYTHONPATH', python_dir, options.sysroot)

    mesonpath = os.path.join(SCRIPTDIR, "meson")
    if os.path.join(mesonpath):
        # Add meson/ into PYTHONPATH if we are using a local meson
        prepend_env_var(env, 'PYTHONPATH', mesonpath, options.sysroot)

    # For devhelp books
    if not 'XDG_DATA_DIRS' in env or not env['XDG_DATA_DIRS']:
        # Preserve default paths when empty
        prepend_env_var(env, 'XDG_DATA_DIRS', '/usr/local/share/:/usr/share/', '')

    prepend_env_var (env, 'XDG_DATA_DIRS', os.path.join(options.builddir,
                                                        'subprojects',
                                                        'gst-docs',
                                                        'GStreamer-doc'),
                     options.sysroot)

    return env

def get_windows_shell():
    command = ['powershell.exe' ,'-noprofile', '-executionpolicy', 'bypass', '-file', 'cmd_or_ps.ps1']
    result = subprocess.check_output(command)
    return result.decode().strip()

# https://stackoverflow.com/questions/1871549/determine-if-python-is-running-inside-virtualenv
def in_venv():
    return (hasattr(sys, 'real_prefix') or
            (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="gstreamer-uninstalled")

    parser.add_argument("--builddir",
                        default=DEFAULT_BUILDDIR,
                        help="The meson build directory")
    parser.add_argument("--srcdir",
                        default=SCRIPTDIR,
                        help="The top level source directory")
    parser.add_argument("--sysroot",
                        default='',
                        help="The sysroot path used during cross-compilation")
    options, args = parser.parse_known_args()

    if not os.path.exists(options.builddir):
        print("GStreamer not built in %s\n\nBuild it and try again" %
              options.builddir)
        exit(1)
    options.builddir = os.path.abspath(options.builddir)

    if not os.path.exists(options.srcdir):
        print("The specified source dir does not exist" %
              options.srcdir)
        exit(1)

    # The following incantation will retrieve the current branch name.
    gst_version = git("rev-parse", "--symbolic-full-name", "--abbrev-ref", "HEAD",
                      repository_path=options.srcdir).strip('\n')

    if not args:
        if os.name is 'nt':
            shell = get_windows_shell()
            if shell == 'powershell.exe':
                args = ['powershell.exe']
                args += ['-NoLogo', '-NoExit']
                prompt = 'function global:prompt {  "[gst-' + gst_version + '"+"] PS " + $PWD + "> "}'
                args += ['-Command', prompt]
            else:
                args = [os.environ.get("COMSPEC", r"C:\WINDOWS\system32\cmd.exe")]
                args += ['/k', 'prompt [gst-{}] $P$G'.format(gst_version)]
        else:
            args = [os.environ.get("SHELL", os.path.realpath("/bin/sh"))]
        if "bash" in args[0] and not strtobool(os.environ.get("GST_BUILD_DISABLE_PS1_OVERRIDE", r"FALSE")):
            tmprc = tempfile.NamedTemporaryFile(mode='w')
            bashrc = os.path.expanduser('~/.bashrc')
            if os.path.exists(bashrc):
                with open(bashrc, 'r') as src:
                    shutil.copyfileobj(src, tmprc)
            tmprc.write('\nexport PS1="[gst-%s] $PS1"' % gst_version)
            tmprc.flush()
            # Let the GC remove the tmp file
            args.append("--rcfile")
            args.append(tmprc.name)
    try:
        exit(subprocess.call(args, close_fds=False,
                             env=get_subprocess_env(options, gst_version)))
    except subprocess.CalledProcessError as e:
        exit(e.returncode)
