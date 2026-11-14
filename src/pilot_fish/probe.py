#%% Imports
import inspect
import time
from types import CodeType
from .registry import type_registry

#%% Create Classes

class TapMethod:

    def __init__(self, tap, function):
        self.tap = tap
        self.function = function
        self.type = type_registry.register_type(function.__name__)

    def __call__(self, *args, **kwargs):
        if self.tap.monitor is None:
            return

        value = self.function(*args, **kwargs)
        self.tap._record(self.type, value)
        return

class Tap:

    def __init__(self):
        self.monitor = None
        self.extension_code_objects: set[CodeType] = set()
        self.tapped_code_objects: set[CodeType] = set()

    def __call__(self, function):
        self.tapped_code_objects.add(inspect.unwrap(function).__code__)
        return function

    def extend(self, function):

        function_name = function.__name__

        if hasattr(Tap, function_name):
            raise ValueError("Cannot name extension function after existing Tap function")

        setattr(self, function_name, TapMethod(self, function))
        self.extension_code_objects.add(inspect.unwrap(function).__code__)
        return function

    def _record(self, record_type: int, value):

        try:
            thread_state = self.monitor._thread_local.thread_state
        except AttributeError:
            return

        if thread_state is None:
            return

        call_id, code_object, _ = thread_state.call_stack[-1]
        thread_state.push((record_type, call_id, None, code_object, None, time.perf_counter_ns(), value))
