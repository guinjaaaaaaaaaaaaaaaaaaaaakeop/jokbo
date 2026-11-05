"""The source checkout's alias for plugin/jokbo.py.
Installed copies never contain this file; it holds no runtime.
"""
import importlib.util
import os
import runpy
import sys

if __name__ == "__main__":
    sys.path[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin")
    runpy.run_path(os.path.join(sys.path[0], "jokbo.py"), run_name="__main__")
else:
    spec = importlib.util.spec_from_file_location(
        __name__, os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin", "jokbo.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[__name__] = module
    spec.loader.exec_module(module)
