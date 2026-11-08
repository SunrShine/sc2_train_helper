
import importlib.util
import os
import re
import sys


def _import_protocol(base_path, protocol_module_name):
    """
    Import a protocol module from the versions directory.

    Patched for Python 3.12+: the original implementation used the removed
    ``imp`` module. Behaviour is identical (module is cached in sys.modules).
    """

    try:
        return sys.modules[protocol_module_name]
    except KeyError:
        pass

    pathname = os.path.join(base_path, protocol_module_name + '.py')
    spec = importlib.util.spec_from_file_location(protocol_module_name, pathname)
    if spec is None or spec.loader is None:
        raise ImportError('unable to load protocol module: %s' % pathname)
    module = importlib.util.module_from_spec(spec)
    sys.modules[protocol_module_name] = module
    spec.loader.exec_module(module)
    return module


def list_all(base_path=None):
    """
    Returns a list of the current protocol version file names in the versions module sorted by name.
    """
    if base_path is None:
        base_path = os.path.dirname(__file__)
    pattern = re.compile('.*protocol[0-9]+.py$')
    files = [ f for f in os.listdir(base_path) \
        if pattern.match(f) ]
    files.sort()
    return files


def latest():
    """
    Import the latest protocol version in the versions module (directory)
    """
    base_path = os.path.dirname(__file__)
    files = list_all(base_path)
    latest_version = files[-1]
    module_name = latest_version.split('.')[0]
    return _import_protocol(base_path, module_name)


def build(build_version):
    """
    Get the module for a specific build version
    """
    base_path = os.path.dirname(__file__)
    return _import_protocol(base_path, 'protocol{0:05d}'.format(build_version))
